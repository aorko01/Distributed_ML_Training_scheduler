import os
from app.db.database import SessionLocal
from app.services.auth_service import get_user_by_username, create_user
from app.schemas.user_schema import UserCreate


def seed_admin_user():
    """Create a superuser at startup if none exists."""
    db = SessionLocal()
    try:
        admin_username = os.getenv("ADMIN_USERNAME", "admin")
        admin_password = os.getenv("ADMIN_PASSWORD", "admin")
        admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")

        existing = get_user_by_username(db, admin_username)
        if existing:
            if not existing.is_superuser:
                existing.is_superuser = True
                db.commit()
            return

        user_data = UserCreate(
            username=admin_username,
            email=admin_email,
            password=admin_password,
            name="Admin",
        )
        user = create_user(db, user_data)
        user.is_superuser = True
        db.commit()
    finally:
        db.close()
