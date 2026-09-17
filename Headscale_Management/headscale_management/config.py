from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def secret(name):
    filename = os.getenv(name + "_FILE")
    value = Path(filename).read_text().strip() if filename else os.getenv(name, "")
    if len(value) < 32 or any(word in value.lower() for word in ("change-me", "changeme", "your-secret", "default")):
        raise ValueError(f"{name} requires a non-default secret of at least 32 characters")
    return value


@dataclass
class Settings:
    database_url: str
    headscale_url: str
    login_server: str
    headscale_key: str
    controller_secret: str
    gateway_secret: str
    bootstrap_secret: str
    encryption_key: str
    signing_key: str
    gateway_id: str = "gateway-main"
    gateway_generation: str = "gateway-v1"
    signing_kid: str = "primary"
    issuer: str = "dml-interactive"
    ca_file: str | None = None
    allow_insecure_test: bool = False
    enrollment_ttl: int = 300
    resource_lease: int = 60
    admission_ttl: int = 60
    session_max: int = 1800
    session_lease: int = 15
    reconcile_interval: float = 5
    observation_ttl: int = 15
    ports: tuple[int, ...] = (9000,)
    ranges: tuple[str, ...] = ("100.64.0.0/10", "fd7a:115c:a1e0::/48")
    required_policy_file: str | None = None
    verification_keys: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.database_url.startswith("sqlite:////"):
            raise ValueError("management requires an absolute file-backed SQLite URL")
        for url in (self.headscale_url, self.login_server):
            if not url.startswith("https://") and not (self.allow_insecure_test and url.startswith("http://")):
                raise ValueError("Headscale requires verified HTTPS outside disposable tests")
        credentials = (self.headscale_key, self.controller_secret, self.gateway_secret, self.bootstrap_secret)
        if any(len(c) < 32 for c in credentials) or len(set(credentials)) != len(credentials):
            raise ValueError("separate high-entropy service credentials are required")
        Fernet(self.encryption_key.encode())
        if not isinstance(serialization.load_pem_private_key(self.signing_key.encode(), password=None), Ed25519PrivateKey):
            raise ValueError("signing key must be Ed25519")
        if self.verification_keys:
            private = serialization.load_pem_private_key(self.signing_key.encode(), password=None)
            current = serialization.load_pem_public_key(self.verification_keys[self.signing_kid]["pem"].encode())
            if current.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw) != private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw):
                raise ValueError("active public verification key does not match signing key")
        if len(self.verification_keys) > 2:
            raise ValueError("at most two ticket verification keys")
        import time
        for kid, record in self.verification_keys.items():
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            if not isinstance(serialization.load_pem_public_key(record["pem"].encode()), Ed25519PublicKey):
                raise ValueError("ticket verification requires Ed25519")
            if kid != self.signing_kid and (not isinstance(record.get("not_after"), (int, float)) or record["not_after"] > time.time() + 3600):
                raise ValueError("retiring-key overlap must be bounded")
        if not (1 <= self.enrollment_ttl <= 300 and 1 <= self.admission_ttl <= 60
                and 1 <= self.session_lease <= self.session_max <= 1800
                and 0 < self.reconcile_interval < self.observation_ttl <= 15
                and 1 <= self.resource_lease <= 300):
            raise ValueError("invalid bounded lifecycle TTLs")
        if not self.ports or any(p != 9000 for p in self.ports):
            raise ValueError("this phase permits only TCP 9000")
        standards = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
        for value in self.ranges:
            network = ipaddress.ip_network(value)
            if not any(network.version == s.version and network.subnet_of(s) for s in standards):
                raise ValueError("invalid tailnet range")

    @classmethod
    def from_env(cls):
        private = Path(os.environ["HM_SIGNING_KEY_FILE"]).read_text()
        return cls(database_url=os.environ["HM_DATABASE_URL"], headscale_url=os.environ["HM_HEADSCALE_URL"].rstrip("/"),
            login_server=os.environ["HM_LOGIN_SERVER"], headscale_key=secret("HM_HEADSCALE_KEY"),
            controller_secret=secret("HM_CONTROLLER_SECRET"), gateway_secret=secret("HM_GATEWAY_SECRET"),
            bootstrap_secret=secret("HM_BOOTSTRAP_SECRET"), encryption_key=secret("HM_ENCRYPTION_KEY"), signing_key=private,
            gateway_id=os.getenv("GATEWAY_ID", "gateway-main"), gateway_generation=os.getenv("GATEWAY_GENERATION", "gateway-v1"),
            signing_kid=os.getenv("HM_SIGNING_KID", "primary"), ca_file=os.getenv("HM_CA_FILE"),
            allow_insecure_test=os.getenv("HM_ALLOW_INSECURE_TEST") == "1",
            required_policy_file=os.environ["HM_REQUIRED_POLICY_FILE"],
            verification_keys=json.loads(Path(os.environ["HM_VERIFICATION_KEYS_FILE"]).read_text()) if os.getenv("HM_VERIFICATION_KEYS_FILE") else {},
            **{name: float(os.environ["HM_" + name.upper()]) if name == "reconcile_interval" else int(os.environ["HM_" + name.upper()])
               for name in ("enrollment_ttl", "resource_lease", "admission_ttl", "session_max", "session_lease", "reconcile_interval", "observation_ttl")
               if "HM_" + name.upper() in os.environ})
