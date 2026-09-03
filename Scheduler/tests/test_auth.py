"""Unit tests for Scheduler/app/utils/auth.py."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import timedelta  # noqa: E402

from app.utils.auth import (  # noqa: E402
    verify_password,
    get_password_hash,
    create_access_token,
    decode_token,
)


class TestPasswordHashing:
    def test_hash_password_returns_string(self):
        hashed = get_password_hash("mypassword")
        assert isinstance(hashed, str)
        assert len(hashed) > 0

    def test_hash_password_is_bcrypt(self):
        hashed = get_password_hash("mypassword")
        assert hashed.startswith("$2")

    def test_verify_password_correct(self):
        password = "secure_password_123"
        hashed = get_password_hash(password)
        assert verify_password(password, hashed) is True

    def test_verify_password_incorrect(self):
        hashed = get_password_hash("correct_password")
        assert verify_password("wrong_password", hashed) is False

    def test_different_hashes_for_same_password(self):
        h1 = get_password_hash("same_password")
        h2 = get_password_hash("same_password")
        assert h1 != h2

    def test_verify_empty_password(self):
        hashed = get_password_hash("")
        assert verify_password("", hashed) is True
        assert verify_password("not_empty", hashed) is False


class TestJWTTokenCreation:
    def test_create_access_token_returns_string(self):
        token = create_access_token({"sub": "user123", "username": "alice"})
        assert isinstance(token, str)
        assert len(token) > 0

    def test_create_access_token_with_custom_expiry(self):
        token = create_access_token(
            {"sub": "user123"}, expires_delta=timedelta(hours=1)
        )
        assert isinstance(token, str)

    def test_decode_token_valid(self):
        data = {"sub": "user123", "username": "alice"}
        token = create_access_token(data)
        decoded = decode_token(token)
        assert decoded is not None
        assert decoded.user_id == "user123"
        assert decoded.username == "alice"

    def test_decode_token_expired(self):
        token = create_access_token(
            {"sub": "user123", "username": "alice"},
            expires_delta=timedelta(seconds=-1),
        )
        decoded = decode_token(token)
        assert decoded is None

    def test_decode_token_malformed(self):
        decoded = decode_token("not.a.valid.jwt.token")
        assert decoded is None

    def test_decode_token_missing_sub(self):
        token = create_access_token({"username": "alice"})
        decoded = decode_token(token)
        assert decoded is None

    def test_decode_token_empty_string(self):
        decoded = decode_token("")
        assert decoded is None
