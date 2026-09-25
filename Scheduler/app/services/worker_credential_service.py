"""DB-backed worker credentials with JSON-file fallback.

The file at ``WORKER_CREDENTIALS_FILE`` is still honoured so existing
deployments keep working, but admins can now register ``(worker_id, secret)``
pairs from the Admin UI without touching the host file or restarting the
Scheduler: ``worker_auth`` checks this DB table first, then the file.
"""

import hmac
import json
import os

from sqlalchemy.orm import Session

from app.models.worker_credential_model import WorkerCredential

MAX_SECRETS_PER_WORKER = 2


def validate_secret(secret: str) -> str | None:
    """Return an error message when *secret* violates the auth policy."""
    if not isinstance(secret, str):
        return "Secret must be a string"
    if not 32 <= len(secret) <= 256:
        return "Secret must be 32-256 characters"
    if len(set(secret)) < 8:
        return "Secret must contain at least 8 distinct characters"
    return None


def validate_worker_id(worker_id: str) -> str | None:
    if not isinstance(worker_id, str) or not worker_id.strip():
        return "worker_id must be a non-empty string"
    if len(worker_id) > 128:
        return "worker_id must be at most 128 characters"
    return None


def load_file_credentials() -> dict[str, list[str]]:
    """Best-effort read of the legacy JSON credentials file."""
    path = os.environ.get("WORKER_CREDENTIALS_FILE")
    if not path:
        return {}
    try:
        with open(path, "r", encoding="ascii") as fh:
            values = json.load(fh)
    except (OSError, ValueError, UnicodeError):
        return {}
    if not isinstance(values, dict):
        return {}
    cleaned: dict[str, list[str]] = {}
    for identity, secrets in values.items():
        if (
            isinstance(identity, str)
            and isinstance(secrets, list)
            and 1 <= len(secrets) <= MAX_SECRETS_PER_WORKER
            and all(isinstance(s, str) for s in secrets)
        ):
            cleaned[identity] = secrets
    return cleaned


def match_db_secret(db: Session, authorization: str) -> str | None:
    """Return the worker identity whose DB secret matches, else None."""
    if not authorization.startswith("Bearer "):
        return None
    try:
        rows = db.query(WorkerCredential).all()
    except Exception:
        return None
    matched: str | None = None
    for row in rows:
        secrets = row.secrets if isinstance(row.secrets, list) else []
        for secret in secrets:
            if not isinstance(secret, str):
                continue
            try:
                if hmac.compare_digest(
                    authorization.encode(), ("Bearer " + secret).encode()
                ):
                    if matched and matched != row.worker_id:
                        # Same secret shared by two identities: refuse to pick.
                        return None
                    matched = row.worker_id
            except (TypeError, ValueError):
                continue
    return matched


def match_file_secret(authorization: str) -> str | None:
    values = load_file_credentials()
    matched: str | None = None
    for identity, secrets in values.items():
        for secret in secrets:
            if validate_secret(secret) is not None:
                continue
            try:
                if hmac.compare_digest(
                    authorization.encode(), ("Bearer " + secret).encode()
                ):
                    if matched:
                        return None
                    matched = identity
            except (TypeError, ValueError):
                continue
    return matched


def list_credentials(db: Session) -> list[dict]:
    """Union of DB + file identities (secrets never returned)."""
    file_values = load_file_credentials()
    merged: dict[str, dict] = {}
    for worker_id in file_values:
        merged[worker_id] = {
            "worker_id": worker_id,
            "source": "file",
            "num_secrets": len(file_values[worker_id]),
        }
    try:
        rows = db.query(WorkerCredential).all()
    except Exception:
        rows = []
    for row in rows:
        secrets = row.secrets if isinstance(row.secrets, list) else []
        merged[row.worker_id] = {
            "worker_id": row.worker_id,
            "source": "db",
            "num_secrets": len(secrets),
            "created_at": (
                row.created_at.isoformat() if row.created_at is not None else None
            ),
            "updated_at": (
                row.updated_at.isoformat() if row.updated_at is not None else None
            ),
        }
    return [merged[key] for key in sorted(merged)]


def add_credential(db: Session, worker_id: str, secret: str) -> WorkerCredential:
    """Insert *secret* for *worker_id* (rotation overlap: max 2 secrets)."""
    worker_id = worker_id.strip()
    row = (
        db.query(WorkerCredential)
        .filter(WorkerCredential.worker_id == worker_id)
        .first()
    )
    if row is None:
        row = WorkerCredential(worker_id=worker_id, secrets=[secret])
        db.add(row)
    else:
        secrets = list(row.secrets) if isinstance(row.secrets, list) else []
        if secret in secrets:
            raise ValueError("Secret already registered for this worker")
        if len(secrets) >= MAX_SECRETS_PER_WORKER:
            raise ValueError(
                "Worker already has 2 secrets; revoke one before adding another"
            )
        # Flag the JSON column as mutated so SQLAlchemy persists the change.
        row.secrets = [*secrets, secret]
    db.commit()
    db.refresh(row)
    return row


def remove_credential(db: Session, worker_id: str) -> bool:
    row = (
        db.query(WorkerCredential)
        .filter(WorkerCredential.worker_id == worker_id)
        .first()
    )
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
