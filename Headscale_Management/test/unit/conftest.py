from pathlib import Path
import time
from types import SimpleNamespace
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest
from fastapi.testclient import TestClient
from headscale_management.config import Settings
from headscale_management.database import Database
from headscale_management.main import create_app


class Headscale:
    def __init__(self):
        self.keys, self.records, self.cleaned = [], [], []
        self.error = None
    async def create_key(self, tags, ephemeral, expiry):
        if self.error:
            raise self.error
        key_id = str(len(self.keys) + 1)
        self.keys.append({"id": key_id, "tags": tags, "ephemeral": ephemeral, "expiry": expiry})
        return key_id, "key-" + key_id + "x" * 40
    def join(self, key_id):
        key = next(k for k in self.keys if k["id"] == key_id)
        self.records.append({"id": key_id, "key_id": key_id, "tags": key["tags"],
            "ips": ["100.64.0." + key_id], "online": True, "expiry": None, "ephemeral": key["ephemeral"], "reusable": False})
    async def nodes(self):
        return self.records
    async def cleanup(self, key_id, nodes):
        self.cleaned.append((key_id, nodes))
        self.records = [r for r in self.records if r["id"] not in nodes]
    async def close(self):
        pass
    async def policy(self):
        return Path(__file__).resolve().parents[3].joinpath("test/interactive_e2e/policy.hujson").read_text()


@pytest.fixture
def system(tmp_path):
    private = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    settings = Settings(database_url="sqlite:///" + str(tmp_path / "db.sqlite"), headscale_url="https://headscale.invalid",
        login_server="https://headscale.invalid", headscale_key="h" * 40, controller_secret="c" * 40,
        gateway_secret="g" * 40, bootstrap_secret="b" * 40, encryption_key=Fernet.generate_key().decode(), signing_key=private,
        required_policy_file=str(Path(__file__).resolve().parents[3] / "test/interactive_e2e/policy.hujson"))
    database = Database(settings.database_url)
    database.migrate()
    adapter = Headscale()
    now = [time.time()]
    app = create_app(settings, adapter, database, lambda: now[0], background=False)
    with TestClient(app) as client:
        yield SimpleNamespace(settings=settings, database=database, headscale=adapter, now=now, app=app, client=client)


def headers(system, role="controller", idempotency=None):
    result = {"Authorization": "Bearer " + getattr(system.settings, role + "_secret")}
    if idempotency:
        result["Idempotency-Key"] = idempotency
    return result


def enroll(system, identity="resource-a", generation="g1", role="endpoint"):
    response = system.client.post("/internal/v1/enrollments", json={"role": role, "identity": identity, "generation": generation},
        headers=headers(system, idempotency=identity + generation))
    assert response.status_code == 200, response.text
    result = response.json()
    system.headscale.join(system.headscale.keys[-1]["id"])
    response = system.client.post("/internal/v1/enrollments/" + result["enrollment_id"] + "/confirm",
        json={"generation": generation}, headers=headers(system))
    assert response.status_code == 200, response.text
    return result


def ready(system, resource="resource-a", generation="g1", owner="user-a"):
    if not any(k["tags"] == ["tag:interactive-gateway"] for k in system.headscale.keys):
        enroll(system, "gateway-main", "gateway-v1", "gateway")
    enrollment = enroll(system, resource, generation)
    result = system.client.put("/internal/v1/resources/" + resource + "/endpoint", json={"owner": owner,
        "generation": generation, "enrollment_id": enrollment["enrollment_id"], "service": "echo",
        "protocol": "tcp-stream-v1", "port": 9000}, headers=headers(system))
    assert result.status_code == 200, result.text
    targets = system.client.get("/internal/v1/gateways/gateway-main/probe-targets", headers=headers(system, "gateway")).json()
    target = next(t for t in targets if t["resource_id"] == resource)
    result = system.client.post("/internal/v1/resources/" + resource + "/probe-result", json={
        key: target[key] for key in ("generation", "version", "probe_id")} | {"success": True}, headers=headers(system, "gateway"))
    assert result.status_code == 200, result.text
    return enrollment, target


def ticket(system, user="user-a", resource="resource-a", generation="g1"):
    return system.client.post("/internal/v1/access-grants", json={"user": user, "resource_id": resource,
        "generation": generation, "service": "echo", "gateway_id": "gateway-main", "authorized": True}, headers=headers(system))
