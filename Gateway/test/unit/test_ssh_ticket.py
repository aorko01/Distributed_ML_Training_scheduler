"""Gateway accepts old (purpose-less) and new (purpose-signed) tickets."""
import time
import jwt
from interactive_gateway.auth import verify, InvalidTicket
from types import SimpleNamespace


def _settings(private_pem, public_pem):
    return SimpleNamespace(verification_keys={"k1": {"pem": public_pem}},
                           issuer="dml-interactive", gateway_id="gateway-main")


def test_old_and_ssh_tickets():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    private = Ed25519PrivateKey.generate()
    priv_pem = private.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    pub_pem = private.public_key().public_bytes(serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    settings = _settings(priv_pem, pub_pem)
    now = int(time.time())
    base = {"iss": "dml-interactive", "aud": "gateway-main", "sub": "u", "jti": "j1",
            "iat": now, "nbf": now, "exp": now + 60, "resource_id": "r",
            "generation": "1", "service": "workspace", "protocol": "tcp-stream-v1"}
    old = jwt.encode(base, priv_pem, algorithm="EdDSA", headers={"kid": "k1"})
    assert verify(old, settings, "r", "workspace")["purpose"] == "browser"
    new = jwt.encode({**base, "jti": "j2", "purpose": "ssh"}, priv_pem, algorithm="EdDSA", headers={"kid": "k1"})
    assert verify(new, settings, "r", "workspace")["purpose"] == "ssh"
    bad = jwt.encode({**base, "jti": "j3", "purpose": "admin"}, priv_pem, algorithm="EdDSA", headers={"kid": "k1"})
    try:
        verify(bad, settings, "r", "workspace")
        raise AssertionError("bad purpose accepted")
    except InvalidTicket:
        pass
