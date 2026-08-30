"""In-memory, TTL'd, single-use one-time password store for SSH gateway auth.

The Scheduler issues a short-lived password when a user requests connect info
for a running interactive session. The user pastes the SSH command shown in
the Dashboard and types this one-time password instead of their account
password. The gateway validates it against this store via the
``POST /interactive/sessions/verify-ephemeral`` endpoint.

The store is stdlib-only (no external dependencies) and lives entirely in
process memory: passwords are keyed by their SHA-256 digest so the plaintext
is never retained after issuance.
"""

import hashlib
import secrets
import threading
import time

# Keyed by sha256(password).hexdigest(). Each entry:
#   {"username": str, "session_id": str, "headscale_ip": str, "expires_at": float}
_store: dict[str, dict] = {}
_lock = threading.Lock()


def _purge_expired() -> None:
    """Remove all entries whose TTL has elapsed. Caller must hold ``_lock``."""
    now = time.time()
    expired = [digest for digest, entry in _store.items() if entry["expires_at"] <= now]
    for digest in expired:
        del _store[digest]


def issue_ephemeral_password(
    username: str,
    session_id: str,
    headscale_ip: str,
    ttl_seconds: int = 300,
) -> str:
    """Issue a single-use, TTL'd password for ``username`` / ``session_id``.

    - Lazily purges expired entries.
    - Revokes any previously issued password for the same ``session_id`` so
      only one valid password per session exists at a time.
    - Returns the plaintext password (the caller shows it to the user).
    """
    password = secrets.token_urlsafe(12)
    digest = hashlib.sha256(password.encode("utf-8")).hexdigest()

    with _lock:
        _purge_expired()
        # One valid password per session at a time: drop any prior entry.
        for existing_digest, entry in list(_store.items()):
            if entry["session_id"] == session_id:
                del _store[existing_digest]
        _store[digest] = {
            "username": username,
            "session_id": session_id,
            "headscale_ip": headscale_ip,
            "expires_at": time.time() + ttl_seconds,
        }

    return password


def verify_ephemeral_password(username: str, password: str) -> dict | None:
    """Validate a one-time password.

    Returns ``{"session_id": ..., "headscale_ip": ...}`` on a valid, unused,
    non-expired match for ``username``; otherwise ``None``. On success the
    entry is consumed (single-use). Expired or username-mismatched entries are
    deleted.
    """
    digest = hashlib.sha256(password.encode("utf-8")).hexdigest()

    with _lock:
        _purge_expired()
        entry = _store.get(digest)
        if entry is None:
            return None

        now = time.time()
        if entry["expires_at"] <= now or entry["username"] != username:
            # Expired or wrong user: delete and reject.
            del _store[digest]
            return None

        # Valid single-use match: consume it.
        del _store[digest]
        return {
            "session_id": entry["session_id"],
            "headscale_ip": entry["headscale_ip"],
        }
