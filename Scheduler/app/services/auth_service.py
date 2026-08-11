from sqlalchemy.orm import Session
from app.models.user_model import User
from app.schemas.user_schema import UserCreate, UserLogin, UserUpdate
from app.utils.auth import verify_password, get_password_hash, create_access_token

def create_user(db: Session, user_data: UserCreate) -> User:
    hashed_password = get_password_hash(user_data.password)
    db_user = User(
        username=user_data.username,
        email=user_data.email,
        name=user_data.name,
        hashed_password=hashed_password
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def get_user_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username).first()

def get_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()

def get_user_by_id(db: Session, user_id: str) -> User | None:
    return db.query(User).filter(User.user_id == user_id).first()

def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = get_user_by_username(db, username)
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user

def create_user_token(user: User) -> str:
    return create_access_token(data={"sub": user.user_id, "username": user.username})

def update_user_profile(db: Session, user_id: str, user_update: UserUpdate) -> User | None:
    user = get_user_by_id(db, user_id)
    if not user:
        return None
    if user_update.name is not None:
        user.name = user_update.name
    if user_update.email is not None:
        existing = get_user_by_email(db, user_update.email)
        if existing and existing.user_id != user_id:
            return None
        user.email = user_update.email
    db.commit()
    db.refresh(user)
    return user