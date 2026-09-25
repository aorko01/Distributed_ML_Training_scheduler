import hmac
import json
import os
import stat
from fastapi import Header, HTTPException


def read_secret(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o027
            or info.st_size > 16384
        ):
            raise ValueError("Unsafe protected file")
        value = os.read(fd, 16385).decode("ascii").strip()
    finally:
        os.close(fd)
    return value


def worker_auth(authorization: str = Header(default="")):
    # Protected map: identity -> list of active secrets; removing a secret revokes
    # it on the next request. Two entries support bounded rotation overlap.
    # Credentials live in the DB (managed via the Admin UI, no restart needed)
    # with the JSON file as fallback for pre-existing deployments.
    if not authorization:
        raise HTTPException(401, "Worker authentication required")
    db_available = True
    try:
        from app.db.database import SessionLocal
        from app.services import worker_credential_service as creds

        db = SessionLocal()
        try:
            matched = creds.match_db_secret(db, authorization)
        finally:
            db.close()
    except Exception:
        db_available = False
        matched = None
    if matched:
        return matched
    try:
        values = json.loads(read_secret(os.environ["WORKER_CREDENTIALS_FILE"]))
        file_matched = None
        for identity, secrets in values.items():
            if not isinstance(secrets, list) or not 1 <= len(secrets) <= 2:
                raise ValueError()
            for secret in secrets:
                if not 32 <= len(secret) <= 256 or len(set(secret)) < 8:
                    raise ValueError()
                if hmac.compare_digest(
                    authorization.encode(), ("Bearer " + secret).encode()
                ):
                    if file_matched:
                        raise ValueError()
                    file_matched = identity
        if file_matched:
            return file_matched
    except (OSError, KeyError, ValueError, TypeError, UnicodeError):
        if not db_available:
            raise HTTPException(503, "Worker authentication unavailable") from None
        # File missing/unusable but DB was readable: fall through to 401.
        pass
    raise HTTPException(401, "Worker authentication required")
