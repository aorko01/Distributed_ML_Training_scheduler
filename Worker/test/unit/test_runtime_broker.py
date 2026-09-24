import asyncio
import hmac
from unittest.mock import MagicMock
import pytest
from interactive.broker import Broker, DockerSession
from Access_Container.interactive_access.protocol import (
    Type,
    read_record,
    write_record,
    json_bytes,
)


class Session:
    active = 0
    closes = 0

    def __init__(self, *args):
        type(self).active += 1
        self.closed = False

    def read(self):
        return None

    def write(self, data):
        pass

    def resize(self, value):
        pass

    def close(self):
        self.closed = True
        type(self).active -= 1
        type(self).closes += 1
        return True


async def authenticate(path, token):
    r, w = await asyncio.open_unix_connection(str(path))
    kind, challenge = await read_record(r)
    assert kind == Type.CHALLENGE and len(challenge) == 32
    await write_record(
        w, Type.AUTH, hmac.digest(token, b"dml-broker-v1\0" + challenge, "sha256")
    )
    assert await read_record(r) == (Type.AUTHENTICATED, b"")
    return r, w


@pytest.mark.asyncio()
async def test_probe_never_launches_and_close_reaps_session(tmp_path, monkeypatch):
    # File permissions are verified in Docker gate; fake host ownership only.
    monkeypatch.setattr("interactive.broker.os.chown", lambda *args: None)
    Session.active = Session.closes = 0
    client = MagicMock()
    client.api.inspect_container.return_value = {"State": {"Running": True}}
    path = tmp_path / "broker.sock"
    token = b"opaque-credential-123456789abcdefghi"
    b = Broker(
        path,
        token,
        client,
        "exact",
        "1000",
        "/workspace",
        lambda: True,
        session_factory=Session,
    )
    await b.start()
    try:
        r, w = await authenticate(path, token)
        await write_record(w, Type.PROBE)
        assert await read_record(r) == (Type.READY, b"")
        assert Session.active == 0
        w.close()
        await w.wait_closed()
        r, w = await authenticate(path, token)
        await write_record(
            w, Type.OPEN, json_bytes({"shell": "default", "columns": 80, "rows": 24})
        )
        assert (await read_record(r))[0] == Type.OPENED and Session.active == 1
        await write_record(w, Type.CLOSE)
        assert (await read_record(r))[0] == Type.EXIT and Session.active == 0
        w.close()
        await w.wait_closed()
        assert Session.closes == 1
    finally:
        await b.stop()


@pytest.mark.asyncio()
async def test_fresh_challenge_blocks_replay(tmp_path, monkeypatch):
    monkeypatch.setattr("interactive.broker.os.chown", lambda *args: None)
    b = Broker(
        tmp_path / "broker.sock",
        b"credential",
        MagicMock(),
        "exact",
        "1000",
        "/workspace",
        lambda: True,
        session_factory=Session,
    )
    await b.start()
    try:
        r, w = await asyncio.open_unix_connection(str(b.path))
        _, challenge = await read_record(r)
        proof = hmac.digest(b.token, b"dml-broker-v1\0" + challenge, "sha256")
        w.close()
        await w.wait_closed()
        r, w = await asyncio.open_unix_connection(str(b.path))
        _, new = await read_record(r)
        assert new != challenge
        await write_record(w, Type.AUTH, proof)
        assert (await read_record(r))[0] == Type.ERROR
        w.close()
        await w.wait_closed()
    finally:
        await b.stop()


def test_error_after_exec_launch_stops_only_exact_workload(monkeypatch):
    monkeypatch.setattr(DockerSession, "members", lambda self: {})
    monkeypatch.setattr(
        "interactive.broker.Path.read_text",
        lambda path: "host" if str(path) == "/proc/self/cgroup" else "workload",
    )
    client = MagicMock()
    client.api.inspect_container.return_value = {"State": {"Pid": 123}}
    client.api.exec_create.return_value = {"Id": "exact-exec"}
    client.api.exec_resize.side_effect = RuntimeError("Docker resize unavailable")
    with pytest.raises(RuntimeError):
        DockerSession(client, "exact-workload", "1000", "/workspace", 80, 24)
    client.api.exec_start.return_value.close.assert_called_once()
    client.api.stop.assert_called_once_with("exact-workload", timeout=3)


def test_workload_env_uses_home_and_venv_not_tmp():
    from interactive.broker import workload_env

    client = MagicMock()
    client.api.inspect_container.return_value = {
        "State": {"Pid": 1},
        "Config": {"Env": ["PATH=/opt/dml-venv/bin:/usr/bin:/bin", "VIRTUAL_ENV=/opt/dml-venv", "HOME=/home/dml"]},
    }
    env = workload_env(client, "workload")
    assert env["HOME"] == "/home/dml"
    assert env["VIRTUAL_ENV"] == "/opt/dml-venv"
    assert env["PATH"].startswith("/opt/dml-venv/bin")
    assert env["TERM"] == "xterm"
    # Missing config falls back to developer defaults, never /tmp.
    client.api.inspect_container.return_value = {"State": {"Pid": 1}}
    fallback = workload_env(client, "workload")
    assert fallback["HOME"] == "/home/dml"
    assert "/opt/dml-venv/bin" in fallback["PATH"]
