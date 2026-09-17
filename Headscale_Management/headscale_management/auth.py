import secrets
from fastapi import HTTPException, Request


def authenticate(request: Request):
    value = request.headers.get("authorization", "")
    settings = request.app.state.settings
    if not value.startswith("Bearer "):
        raise HTTPException(401, "invalid credentials")
    supplied = value[7:]
    for role in ("controller", "gateway", "bootstrap"):
        if secrets.compare_digest(supplied, getattr(settings, role + "_secret")):
            return role
    raise HTTPException(401, "invalid credentials")


def require(actor, *roles):
    if actor not in roles:
        raise HTTPException(403, "denied")
