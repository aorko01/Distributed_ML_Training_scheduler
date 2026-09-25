from typing import Generator
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from jose import jwt, JWTError
from app.db.database import SessionLocal
from app.models.user_model import User
from app.utils.auth import SECRET_KEY, ALGORITHM

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    user = db.query(User).filter(User.user_id == user_id).first()
    if user is None:
        raise credentials_exception
    return user

def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def get_current_superuser(
    current_user: User = Depends(get_current_active_user),
) -> User:
    """Admin principal: only active superusers may use the Admin console API."""
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return current_user


def get_cli_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Scoped CLI principal: only tokens carrying interactive:ssh scope.

    SSH info accepts a normal browser owner token or this scoped CLI token;
    SSH connection grants accept the scoped CLI token. A CLI token cannot be
    used for general user routes.
    """
    from jose import jwt as _jwt, JWTError as _JWTError
    from app.utils.auth import SECRET_KEY as _KEY, ALGORITHM as _ALG
    try:
        payload = _jwt.decode(token, _KEY, algorithms=[_ALG])
        if payload.get("scope") != "interactive:ssh" or not payload.get("sub"):
            raise HTTPException(status_code=401, detail="CLI login required")
    except _JWTError:
        raise HTTPException(status_code=401, detail="CLI login required")
    user = db.query(User).filter(User.user_id == payload["sub"]).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Account disabled")
    return user


def get_ssh_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """SSH info principal: browser owner token (no scope) or CLI token."""
    from jose import jwt as _jwt, JWTError as _JWTError
    from app.utils.auth import SECRET_KEY as _KEY, ALGORITHM as _ALG
    try:
        payload = _jwt.decode(token, _KEY, algorithms=[_ALG])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Could not validate credentials")
    except _JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    user = db.query(User).filter(User.user_id == user_id).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Account disabled")
    return user