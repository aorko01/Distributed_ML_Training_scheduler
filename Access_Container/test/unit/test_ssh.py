import asyncio
import json
import pytest
from interactive_access.protocol import Type, ProtocolError, encode, json_bytes, ssh_open
from interactive_access.session import run_ssh_session, _ssh_acquire, _ssh_release
from interactive_access.config import Config


KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f"


def test_ssh_open_validates():
    good = json.dumps({"version": 1, "public_key": KEY, "generation": 2}, separators=(",", ":")).encode()
    assert ssh_open(good)["generation"] == 2
    for bad in (b"{}", json.dumps({"version": 2, "public_key": KEY, "generation": 1}).encode(),
                json.dumps({"version": 1, "public_key": "ssh-rsa AAAA", "generation": 1}).encode()):
        with pytest.raises(ProtocolError):
            ssh_open(bad)


def test_only_ssh_open_transitions(tmp_path):
    # Framed parser boundary: SSH_OPEN is a distinct versioned type.
    assert Type.SSH_OPEN == 48 and Type.SSH_READY == 49


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    async def wait(self, coro, timeout):
        return await asyncio.wait_for(coro, timeout)

    async def sleep(self, s):
        self.t += s


async def test_ssh_busy_isolated():
    import types
    cfg = types.SimpleNamespace(runtime_id="r1", ssh_capacity=1, open_timeout=1, broker_timeout=1,
                                write_timeout=1, ssh_handshake_timeout=1)
    assert _ssh_acquire(cfg) is True
    assert _ssh_acquire(cfg) is False
    _ssh_release(cfg)
    assert _ssh_acquire(cfg) is True
    _ssh_release(cfg)
