"""In-memory per-IP reference-counting + TTL tracker for the SSH gateway whitelist.

Each entry tracks how many concurrent SSH connections are using a given source
IP.  The OCI NSG ingress rule is added on the *first* reference and removed
when the count drops back to zero (or when the TTL expires — a safety net for
leaked entries).
"""

import asyncio
import logging
import os
import time

from app.services.oci_whitelist_service import add_ip, remove_ip

logger = logging.getLogger("whitelist_tracker")

WHITELIST_TTL_SECONDS = int(os.getenv("WHITELIST_TTL_SECONDS", "86400"))
WHITELIST_SWEEP_INTERVAL = int(os.getenv("WHITELIST_SWEEP_INTERVAL", "300"))

# ip -> {"count": int, "expires_at": float}
_entries: dict[str, dict] = {}
_lock = asyncio.Lock()


async def add(ip: str) -> None:
    """Register *ip* for whitelisting.

    Increments the reference count.  On the first reference (count was 0) the
    OCI NSG ingress rule is added.  The expiry timestamp is refreshed on every
    call so that an active session keeps the rule alive.
    """
    async with _lock:
        entry = _entries.get(ip)
        if entry is None:
            _entries[ip] = {
                "count": 1,
                "expires_at": time.time() + WHITELIST_TTL_SECONDS,
            }
            first = True
        else:
            entry["count"] += 1
            entry["expires_at"] = time.time() + WHITELIST_TTL_SECONDS
            first = False

    if first:
        # Best-effort; add_ip never raises.
        add_ip(ip)


async def remove(ip: str) -> None:
    """Deregister *ip* from the whitelist.

    Decrements the reference count.  When it reaches zero the OCI NSG ingress
    rule is removed and the entry is dropped.  If the IP is not tracked this is
    a no-op.
    """
    async with _lock:
        entry = _entries.get(ip)
        if entry is None:
            return
        entry["count"] -= 1
        if entry["count"] <= 0:
            del _entries[ip]
            should_remove = True
        else:
            should_remove = False

    if should_remove:
        # Best-effort; remove_ip never raises.
        remove_ip(ip)


async def sweep_expired() -> None:
    """Remove all entries whose TTL has expired (safety net for leaked entries)."""
    now = time.time()
    expired: list[str] = []
    async with _lock:
        for ip, entry in list(_entries.items()):
            if now >= entry["expires_at"]:
                expired.append(ip)
                del _entries[ip]

    for ip in expired:
        logger.info("Sweeping expired whitelist entry for %s", ip)
        # Best-effort; remove_ip never raises.
        remove_ip(ip)


async def sweep_loop() -> None:
    """Background loop that periodically sweeps expired entries."""
    while True:
        try:
            await sweep_expired()
        except Exception as e:
            logger.error("sweep_expired failed: %s", e)
        await asyncio.sleep(WHITELIST_SWEEP_INTERVAL)
