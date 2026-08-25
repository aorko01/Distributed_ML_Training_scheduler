from pydantic import BaseModel


class AuthKeyCreateRequest(BaseModel):
    expiry_seconds: int = 3600
    reusable: bool = True
    ephemeral: bool = True
    user: str | None = None


class AuthKeyResponse(BaseModel):
    auth_key: str


class AuthKeyRevokeResponse(BaseModel):
    status: str
