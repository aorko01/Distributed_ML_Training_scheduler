"""Scoped CLI authentication for dml-ssh (plan.md §5).

Username/password over HTTPS once -> short access token (scope
interactive:ssh) + rotating opaque refresh token (server-side hash,
revocation, finite expiry). CLI tokens cannot access general user routes.
"""
import hashlib
import secrets
from datetime import timedelta
from fastapi import HTTPException
from app.models.cli_token_model import CliRefreshToken
from app.models.user_model import User
from app.services.scheduling.types import now, utc
from app.utils.auth import create_access_token, verify_password

CLI_SCOPE = "interactive:ssh"
ACCESS_MINUTES = 15
REFRESH_DAYS = 30


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login(db, username: str, password: str):
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(401, "Invalid credentials")
    if not user.is_active:
        raise HTTPException(400, "Inactive user")
    return issue_pair(db, user)


def issue_pair(db, user):
    access = create_access_token(
        {"sub": user.user_id, "username": user.username, "scope": CLI_SCOPE},
        expires_delta=timedelta(minutes=ACCESS_MINUTES),
    )
    raw = secrets.token_urlsafe(48)
    db.add(CliRefreshToken(
        id=secrets.token_hex(16), user_id=user.user_id, token_hash=_hash(raw),
        scope=CLI_SCOPE, created_at=now(), expires_at=now() + timedelta(days=REFRESH_DAYS),
    ))
    db.commit()
    return {"access_token": access, "refresh_token": raw, "token_type": "bearer",
            "expires_in": ACCESS_MINUTES * 60, "scope": CLI_SCOPE}


def refresh(db, raw: str):
    row = db.query(CliRefreshToken).filter_by(token_hash=_hash(raw)).first()
    if not row or row.revoked_at or utc(row.expires_at) <= now():
        raise HTTPException(401, "Login required (dml-ssh login)")
    if row.scope != CLI_SCOPE:
        raise HTTPException(401, "Invalid scope")
    user = db.query(User).filter(User.user_id == row.user_id).first()
    if not user or not user.is_active:
        row.revoked_at = now()
        db.commit()
        raise HTTPException(401, "Account disabled")
    # Rotate: revoke old, issue new pair (replay of old fails closed).
    row.revoked_at = now()
    pair = issue_pair(db, user)
    row.replaced_by = pair["refresh_token"][:8]
    db.commit()
    return pair


def logout(db, raw: str):
    row = db.query(CliRefreshToken).filter_by(token_hash=_hash(raw)).first()
    if row and not row.revoked_at:
        row.revoked_at = now()
        db.commit()
    return {"status": "logged out"}


def revoke_all(db, user_id: str):
    rows = db.query(CliRefreshToken).filter_by(user_id=user_id, revoked_at=None).all()
    for row in rows:
        row.revoked_at = now()
    db.commit()
    return {"revoked": len(rows)}
