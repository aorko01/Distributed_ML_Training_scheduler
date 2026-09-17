import asyncio
from dataclasses import replace
import json
import time
from types import SimpleNamespace
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
import jwt
import pytest
from starlette.websockets import WebSocketDisconnect

from interactive_gateway.auth import InvalidTicket, verify
from interactive_gateway.config import Settings
from interactive_gateway.main import create_app
from interactive_gateway.management_client import ManagementError
from interactive_gateway.schemas import destination
from interactive_gateway.sessions import Capacity, CapacityError
from interactive_gateway.tailnet_dialer import TailnetDialer


@pytest.fixture
def system():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    settings = Settings("http://management", "g" * 40, {"primary": {"pem": public}}, origins=("https://client.example",), allow_cli=True)
    now = int(time.time())
    claims = {"iss": settings.issuer, "aud": settings.gateway_id, "sub": "user-a", "jti": str(uuid4()),
        "iat": now, "nbf": now, "exp": now + 60, "resource_id": "resource-a", "generation": "g1",
        "service": "echo", "protocol": "tcp-stream-v1"}
    token = jwt.encode(claims, private, algorithm="EdDSA", headers={"kid": "primary"})
    return SimpleNamespace(private=private, settings=settings, claims=claims, token=token)


@pytest.mark.parametrize("mutation", [{"aud": "wrong"}, {"service": "other"}, {"resource_id": "resource-b"},
    {"protocol": "ssh"}, {"exp": 1}, {"nbf": 9999999999}])
def test_ticket_integrity_and_path(system, mutation):
    token = jwt.encode(system.claims | mutation, system.private, algorithm="EdDSA", headers={"kid": "primary"})
    with pytest.raises(InvalidTicket):
        verify(token, system.settings, "resource-a", "echo")


def test_signature_and_algorithm_fixed(system):
    assert verify(system.token, system.settings, "resource-a", "echo")["sub"] == "user-a"
    for token in (jwt.encode(system.claims, "secret", algorithm="HS256", headers={"kid": "primary"}),
                  jwt.encode(system.claims, Ed25519PrivateKey.generate(), algorithm="EdDSA", headers={"kid": "primary"})):
        with pytest.raises(InvalidTicket):
            verify(token, system.settings, "resource-a", "echo")


@pytest.mark.parametrize("record", [{"ips": ["127.0.0.1"], "port": 9000}, {"ips": ["172.18.0.2"], "port": 9000},
    {"ips": ["100.64.0.1"], "port": 22}, {"ips": ["100.64.0.1"], "port": 9000, "gateway_ips": ["100.64.0.1"]}])
def test_destination_validation(record):
    with pytest.raises(ValueError):
        destination(record)


class Management:
    def __init__(self, system, error=None):
        from datetime import datetime, timezone
        utc = lambda n: datetime.fromtimestamp(n, timezone.utc).isoformat()
        self.record = {"session_id": str(uuid4()), "user": "user-a", "resource_id": "resource-a", "service": "echo",
            "generation": "g1", "protocol": "tcp-stream-v1", "ips": ["100.64.0.2"], "port": 9000,
            "gateway_ips": ["100.64.0.1"], "version": "v1", "lease_expires_at": utc(time.time() + 15), "deadline": utc(time.time() + 1800)}
        self.error, self.released = error, []
    async def claim(self, *args):
        if self.error:
            raise self.error
        return self.record
    async def renew(self, *args):
        return self.record
    async def release(self, session):
        self.released.append(session)
    async def close(self):
        pass


class Local:
    async def close(self):
        pass


class Dialer:
    def __init__(self):
        self.calls = []
    async def dial(self, *args):
        self.calls.append(args)
        raise OSError("transport unavailable")


@pytest.mark.parametrize("case", ["origin", "timeout", "forged", "path", "claim", "query"])
def test_rejection_never_dials_and_capacity_released(system, case):
    settings = replace(system.settings, auth_timeout=0.05)
    management = Management(system, ManagementError() if case == "claim" else None)
    dialer = Dialer()
    app = create_app(settings, management, dialer, Local(), background=False)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            url = "/v1/connect/" + ("resource-b" if case == "path" else "resource-a") + "/echo"
            if case == "query":
                url += "?ticket=secret"
            with client.websocket_connect(url, headers={"Origin": "https://evil.example"} if case == "origin" else {}) as ws:
                if case != "timeout":
                    ws.send_json({"type": "authenticate", "ticket": "forged" if case == "forged" else system.token})
                ws.receive_json()
        assert dialer.calls == []
        assert app.state.capacity.unauthenticated == 0
        assert app.state.capacity.active == {}


def test_dial_failure_releases_claim_and_capacity(system):
    management, dialer = Management(system), Dialer()
    app = create_app(system.settings, management, dialer, Local(), background=False)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/v1/connect/resource-a/echo") as ws:
                ws.send_json({"type": "authenticate", "ticket": system.token})
                ws.receive_json()
        assert dialer.calls == [("100.64.0.2", 9000)]
        assert len(management.released) == 1 and not app.state.capacity.active


@pytest.mark.asyncio
async def test_duplicate_session_and_per_user_capacity(system):
    capacity = Capacity(replace(system.settings, total_connections=2, per_user_connections=1))
    await capacity.reserve("session", "user", asyncio.current_task())
    with pytest.raises(CapacityError):
        await capacity.reserve("session", "other", asyncio.current_task())
    with pytest.raises(CapacityError):
        await capacity.reserve("second", "user", asyncio.current_task())
    await capacity.release("session")
    assert not capacity.active and capacity.users["user"] == 0


@pytest.mark.asyncio
async def test_socks_failure_has_no_direct_fallback(monkeypatch):
    calls = []
    async def failing(host, port, **kwargs):
        calls.append((host, port))
        raise OSError("SOCKS down")
    monkeypatch.setattr(asyncio, "open_connection", failing)
    with pytest.raises(OSError):
        await TailnetDialer().dial("100.64.0.2", 9000)
    assert calls == [("127.0.0.1", 1055)]
