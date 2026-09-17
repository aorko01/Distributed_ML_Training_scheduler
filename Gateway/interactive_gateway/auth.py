import time
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

REQUIRED = ["iss", "aud", "sub", "jti", "iat", "nbf", "exp", "resource_id", "generation", "service", "protocol"]


class InvalidTicket(Exception):
    pass


def validate_keyring(keys):
    if not isinstance(keys, dict) or not 1 <= len(keys) <= 2:
        raise ValueError("configure one active public key and at most one retiring key")
    active = 0
    for kid, record in keys.items():
        if not isinstance(kid, str) or not 1 <= len(kid) <= 64 or not isinstance(record, dict):
            raise ValueError("invalid verification key record")
        if set(record) - {"pem", "not_after"} or "pem" not in record:
            raise ValueError("invalid verification key metadata")
        if not isinstance(serialization.load_pem_public_key(record["pem"].encode()), Ed25519PublicKey):
            raise ValueError("verification requires Ed25519 public keys")
        if record.get("not_after") is None:
            active += 1
        elif not isinstance(record["not_after"], (int, float)) or record["not_after"] > time.time() + 3600:
            raise ValueError("retiring-key overlap is bounded to one hour")
    if active != 1:
        raise ValueError("exactly one active verification key required")


def verify(ticket, settings, resource_id, service, clock=time.time):
    try:
        if not isinstance(ticket, str) or len(ticket) > 8192:
            raise ValueError()
        header = jwt.get_unverified_header(ticket)
        record = settings.verification_keys[header["kid"]]
        if header.get("alg") != "EdDSA" or (record.get("not_after") is not None and clock() >= record["not_after"]):
            raise ValueError()
        claims = jwt.decode(ticket, record["pem"], algorithms=["EdDSA"], issuer=settings.issuer, audience=settings.gateway_id,
            options={"require": REQUIRED, "verify_exp": False, "verify_nbf": False, "verify_iat": False})
        if (claims["exp"] <= clock() or claims["nbf"] > clock() or claims["iat"] > clock()
                or claims["exp"] - claims["iat"] > 60 or claims["protocol"] != "tcp-stream-v1"
                or claims["resource_id"] != resource_id or claims["service"] != service):
            raise ValueError()
        for key in ("sub", "jti", "resource_id", "generation", "service"):
            if not isinstance(claims[key], str) or not 1 <= len(claims[key]) <= 128:
                raise ValueError()
        return claims
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        raise InvalidTicket("invalid ticket") from None
