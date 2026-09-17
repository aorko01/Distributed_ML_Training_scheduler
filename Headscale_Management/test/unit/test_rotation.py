from dataclasses import replace
import time
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest
from fastapi import HTTPException

from headscale_management.grant_service import GrantService
from headscale_management.schemas import Claim
from conftest import ready, ticket


def test_rotation_accepts_bounded_old_key_without_losing_replay_state(system):
    ready(system)
    issued = ticket(system).json()
    old = system.app.state.grants.private.public_key().public_bytes(serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    new = Ed25519PrivateKey.generate()
    private = new.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    new_public = new.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    configured = replace(system.settings, signing_key=private, signing_kid="new", verification_keys={
        "new": {"pem": new_public}, "primary": {"pem": old, "not_after": time.time() + 120}})
    endpoints = system.app.state.endpoints
    endpoints.settings = configured
    rotated = GrantService(endpoints)
    request = Claim(ticket=issued["ticket"], request_id=str(uuid4()))
    assert rotated.claim(request, "gateway")["user"] == "user-a"
    system.now[0] += 121
    with pytest.raises(HTTPException) as error:
        rotated.decode(issued["ticket"], admission=False)
    assert error.value.status_code == 401


def test_mismatched_signing_public_keys_rejected(system):
    wrong = Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    with pytest.raises(ValueError):
        replace(system.settings, verification_keys={"primary": {"pem": wrong}})
