from dataclasses import dataclass
import json
import os
from pathlib import Path


def secret(name):
    filename = os.getenv(name + "_FILE")
    value = Path(filename).read_text().strip() if filename else os.getenv(name, "")
    if len(value) < 32 or any(word in value.lower() for word in ("change-me", "changeme", "your-secret", "default")):
        raise ValueError(f"{name} requires a non-default secret of at least 32 characters")
    return value


@dataclass
class Settings:
    management_url: str
    gateway_secret: str
    verification_keys: dict
    gateway_id: str = "gateway-main"
    generation: str = "gateway-v1"
    issuer: str = "dml-interactive"
    origins: tuple[str, ...] = ()
    allow_cli: bool = False
    socks_host: str = "127.0.0.1"
    socks_port: int = 1055
    socket: str = "/var/run/tailscale/tailscaled.sock"
    enrollment_file: str = "/bootstrap/enrollment.json"
    auth_timeout: float = 5
    renewal_interval: float = 5
    probe_interval: float = 5
    idle_timeout: float = 300
    total_connections: int = 100
    per_user_connections: int = 5
    unauthenticated_connections: int = 20
    frame_max: int = 65536
    ca_file: str | None = None

    def __post_init__(self):
        from .auth import validate_keyring
        validate_keyring(self.verification_keys)
        if len(self.gateway_secret) < 32 or self.socks_host != "127.0.0.1":
            raise ValueError("gateway requires secret and a loopback-only SOCKS listener")
        if not self.management_url.startswith(("http://", "https://")) or not 1 <= self.socks_port <= 65535:
            raise ValueError("invalid internal management or SOCKS address")
        if not 0 < self.auth_timeout <= 5 or not 0 < self.renewal_interval <= 5 or not 0 < self.probe_interval <= 5:
            raise ValueError("invalid authentication/renewal timing")
        if (not 1 <= self.frame_max <= 65536 or not 0 < self.idle_timeout <= 300
                or not 1 <= self.per_user_connections <= self.total_connections <= 1000
                or not 1 <= self.unauthenticated_connections <= 1000):
            raise ValueError("invalid bounded connection configuration")
        if not self.origins and not self.allow_cli:
            raise ValueError("configure browser Origins or explicit CLI mode")
        if any(not origin.startswith("https://") for origin in self.origins):
            raise ValueError("browser Origins require HTTPS")

    @classmethod
    def from_env(cls):
        return cls(management_url=os.environ["GW_MANAGEMENT_URL"].rstrip("/"), gateway_secret=secret("HM_GATEWAY_SECRET"),
            verification_keys=json.loads(Path(os.environ["GW_VERIFICATION_KEYS_FILE"]).read_text()),
            gateway_id=os.getenv("GATEWAY_ID", "gateway-main"), generation=os.getenv("GATEWAY_GENERATION", "gateway-v1"),
            origins=tuple(filter(None, os.getenv("GW_ORIGINS", "").split(","))), allow_cli=os.getenv("GW_ALLOW_CLI") == "1",
            socket=os.getenv("GW_TAILSCALE_SOCKET", "/var/run/tailscale/tailscaled.sock"),
            enrollment_file=os.getenv("GW_ENROLLMENT_FILE", "/bootstrap/enrollment.json"), ca_file=os.getenv("GW_CA_FILE"),
            **{name: float(os.environ["GW_" + name.upper()]) for name in ("auth_timeout", "renewal_interval", "probe_interval", "idle_timeout")
               if "GW_" + name.upper() in os.environ})
