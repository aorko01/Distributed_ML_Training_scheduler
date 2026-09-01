"""In-memory, TTL'd, time-windowed password store for SSH gateway auth.

The Scheduler issues a short-lived password when a user requests connect info
for a running interactive session. The user pastes the SSH command shown in
the Dashboard and types this password instead of their account password. The
gateway validates it against this store via the
``POST /interactive/sessions/verify-ephemeral`` endpoint.

The store is stdlib-only (no external dependencies) and lives entirely in
process memory: passwords are keyed by their SHA-256 digest so the plaintext
is never retained after issuance.

OTP validity model:
  - Issued with a base TTL (default 300 s).
  - On first successful verification, a grace period is added so that
    follow-up connections (e.g. VS Code Remote-SSH opens several without
    multiplexing) all succeed within the same window.
  - A hard cap from issuance prevents infinite extension.
  - One valid password per session at a time; reissuing revokes the old one.
"""

import hashlib
import secrets
import threading
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ISSUE_TTL_SECONDS = 300      # Base validity window from issuance.
GRACE_SECONDS = 300          # Extra window granted on first successful use.
MAX_LIFETIME_SECONDS = 600   # Hard cap from issuance (prevents infinite ext).

# Keyed by sha256(password).hexdigest(). Each entry:
#   {"username": str, "session_id": str, "headscale_ip": str,
#    "issued_at": float, "expires_at": float, "first_used_at": float | None}
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
    ttl_seconds: int = ISSUE_TTL_SECONDS,
) -> str:
    """Issue a time-windowed, multi-use password for ``username`` / ``session_id``.

    - Lazily purges expired entries.
    - Revokes any previously issued password for the same ``session_id`` so
      only one valid password per session exists at a time.
    - Returns the plaintext password (the caller shows it to the user).
    """
    password = secrets.token_urlsafe(12)
    digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
    now = time.time()

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
            "issued_at": now,
            "expires_at": now + ttl_seconds,
            "first_used_at": None,
        }

    return password


def verify_ephemeral_password(username: str, password: str) -> dict | None:
    """Validate an ephemeral password.

    Returns ``{"session_id": ..., "headscale_ip": ...}`` on a valid,
    non-expired match for ``username``; otherwise ``None``.

    The password remains valid for the full time window (base TTL + grace
    extension on first use) and can be reused for multiple connections within
    that window.  This allows VS Code Remote-SSH to open several TCP
    connections without requiring client-side connection multiplexing.

    Expired or username-mismatched entries are deleted.
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

        # Valid match — keep the entry (multi-use within the window).
        # On first use, extend the window so follow-up connections succeed.
        if entry.get("first_used_at") is None:
            entry["first_used_at"] = now
            entry["expires_at"] = min(
                entry["expires_at"] + GRACE_SECONDS,
                max(entry["expires_at"], entry["issued_at"] + MAX_LIFETIME_SECONDS),
            )

        return {
            "session_id": entry["session_id"],
            "headscale_ip": entry["headscale_ip"],
        }
