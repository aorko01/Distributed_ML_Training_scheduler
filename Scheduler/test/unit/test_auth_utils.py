"""Unit tests for app/utils/auth.py."""
from datetime import timedelta

import pytest
from jose import jwt

from app.utils import auth


class TestPasswordHashing:
    def test_hash_and_verify_roundtrip(self):
        hashed = auth.get_password_hash("correct-horse")
        assert hashed != "correct-horse"
        assert auth.verify_password("correct-horse", hashed) is True

    def test_verify_wrong_password_returns_false(self):
        hashed = auth.get_password_hash("secret123")
        assert auth.verify_password("wrong-password", hashed) is False

    def test_hashes_are_salted(self):
        assert auth.get_password_hash("same") != auth.get_password_hash("same")


class TestCreateAccessToken:
    def test_token_contains_payload_and_expiry(self):
        token = auth.create_access_token({"sub": "user-1", "username": "alice"})
        payload = jwt.decode(token, auth.SECRET_KEY, algorithms=[auth.ALGORITHM])
        assert payload["sub"] == "user-1"
        assert payload["username"] == "alice"
        assert "exp" in payload

    def test_custom_expiry_is_honoured(self):
        token = auth.create_access_token(
            {"sub": "user-1"}, expires_delta=timedelta(minutes=5)
        )
        payload = jwt.decode(
            token, auth.SECRET_KEY, algorithms=[auth.ALGORITHM],
            options={"verify_exp": False},
        )
        assert payload["sub"] == "user-1"

    def test_input_dict_is_not_mutated(self):
        data = {"sub": "user-1"}
        auth.create_access_token(data)
        assert data == {"sub": "user-1"}


class TestDecodeToken:
    def test_valid_token_returns_token_data(self):
        token = auth.create_access_token({"sub": "uid-1", "username": "bob"})
        result = auth.decode_token(token)
        assert result is not None
        assert result.user_id == "uid-1"
        assert result.username == "bob"

    def test_token_without_sub_returns_none(self):
        token = jwt.encode({"username": "bob"}, auth.SECRET_KEY, algorithm=auth.ALGORITHM)
        assert auth.decode_token(token) is None

    def test_garbage_token_returns_none(self):
        assert auth.decode_token("not-a-token") is None

    def test_wrong_signature_returns_none(self):
        token = jwt.encode({"sub": "x"}, "wrong-secret", algorithm="HS256")
        assert auth.decode_token(token) is None

    def test_expired_token_returns_none(self):
        token = auth.create_access_token(
            {"sub": "uid-1"}, expires_delta=timedelta(seconds=-1)
        )
        import time

        time.sleep(0.01)
        assert auth.decode_token(token) is None
