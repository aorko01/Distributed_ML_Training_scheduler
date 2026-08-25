import logging

from fastapi import APIRouter, Header, HTTPException

import config
from headscale_client import HeadscaleError, headscale_client
from schemas import AuthKeyCreateRequest, AuthKeyResponse, AuthKeyRevokeResponse

logger = logging.getLogger("api")

router = APIRouter()


def _require_token(authorization: str | None):
    expected = f"Bearer {config.AUTH_TOKEN}"
    if not authorization or authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing auth token")


@router.post("/auth-keys", response_model=AuthKeyResponse)
def create_auth_key(
    body: AuthKeyCreateRequest,
    authorization: str | None = Header(default=None),
):
    _require_token(authorization)
    try:
        key = headscale_client.create_preauth_key(
            expiry_seconds=body.expiry_seconds,
            user=body.user,
            reusable=body.reusable,
            ephemeral=body.ephemeral,
        )
    except HeadscaleError as e:
        logger.error("Pre-auth key creation failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e))
    return {"auth_key": key}


@router.delete("/auth-keys/{key}", response_model=AuthKeyRevokeResponse)
def revoke_auth_key(key: str, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    try:
        headscale_client.revoke_key(key)
    except HeadscaleError as e:
        logger.error("Pre-auth key revocation failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e))
    return {"status": "revoked"}
