"""Unit tests for app/services/auth_service.py."""
import pytest

from app.schemas.user_schema import UserCreate, UserUpdate
from app.services import auth_service
from app.utils.auth import verify_password
from conftest import make_user


class TestCreateUser:
    def test_password_is_hashed(self, db):
        user = auth_service.create_user(
            db,
            UserCreate(username="alice", email="alice@example.com", password="s3cret"),
        )
        assert user.username == "alice"
        assert user.hashed_password != "s3cret"
        assert verify_password("s3cret", user.hashed_password)

    def test_optional_name(self, db):
        user = auth_service.create_user(
            db, UserCreate(username="bob", email="bob@example.com", password="pw")
        )
        assert user.name is None


class TestLookups:
    def test_get_by_username_email_id(self, db):
        user = make_user(db, username="carol", email="carol@example.com")
        assert auth_service.get_user_by_username(db, "carol").user_id == user.user_id
        assert (
            auth_service.get_user_by_email(db, "carol@example.com").user_id
            == user.user_id
        )
        assert auth_service.get_user_by_id(db, user.user_id).username == "carol"
        assert auth_service.get_user_by_username(db, "nobody") is None


class TestAuthenticateUser:
    def test_success(self, db):
        from app.utils.auth import get_password_hash

        make_user(db, username="dave", password_hash=get_password_hash("pw123"))
        user = auth_service.authenticate_user(db, "dave", "pw123")
        assert user is not None

    def test_wrong_password_returns_none(self, db):
        from app.utils.auth import get_password_hash

        make_user(db, username="erin", password_hash=get_password_hash("pw123"))
        assert auth_service.authenticate_user(db, "erin", "wrong") is None

    def test_unknown_user_returns_none(self, db):
        assert auth_service.authenticate_user(db, "ghost", "pw") is None


class TestCreateUserToken:
    def test_token_decodes_to_user(self, db):
        from app.utils.auth import decode_token

        user = make_user(db, username="frank")
        token = auth_service.create_user_token(user)
        data = decode_token(token)
        assert data.user_id == user.user_id
        assert data.username == "frank"


class TestUpdateUserProfile:
    def test_update_name(self, db):
        user = make_user(db)
        out = auth_service.update_user_profile(
            db, user.user_id, UserUpdate(name="New Name")
        )
        assert out.name == "New Name"

    def test_update_email(self, db):
        user = make_user(db)
        out = auth_service.update_user_profile(
            db, user.user_id, UserUpdate(email="new@example.com")
        )
        assert out.email == "new@example.com"

    def test_email_taken_by_other_returns_none(self, db):
        u1 = make_user(db)
        u2 = make_user(db)
        assert (
            auth_service.update_user_profile(
                db, u1.user_id, UserUpdate(email=u2.email)
            )
            is None
        )

    def test_same_email_allowed(self, db):
        user = make_user(db)
        out = auth_service.update_user_profile(
            db, user.user_id, UserUpdate(email=user.email)
        )
        assert out is not None

    def test_missing_user_returns_none(self, db):
        assert (
            auth_service.update_user_profile(db, "ghost", UserUpdate(name="x"))
            is None
        )
