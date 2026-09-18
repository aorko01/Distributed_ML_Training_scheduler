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
    try:
        values = json.loads(read_secret(os.environ["WORKER_CREDENTIALS_FILE"]))
        matched = None
        for identity, secrets in values.items():
            if not isinstance(secrets, list) or not 1 <= len(secrets) <= 2:
                raise ValueError()
            for secret in secrets:
                if not 32 <= len(secret) <= 256 or len(set(secret)) < 8:
                    raise ValueError()
                if hmac.compare_digest(
                    authorization.encode(), ("Bearer " + secret).encode()
                ):
                    if matched:
                        raise ValueError()
                    matched = identity
    except (OSError, KeyError, ValueError, TypeError, UnicodeError):
        raise HTTPException(503, "Worker authentication unavailable") from None
    if not matched:
        raise HTTPException(401, "Worker authentication required")
    return matched
