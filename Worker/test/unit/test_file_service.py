"""FileService: fixed-helper exec transport, docker-py 7.2.0 semantics."""
import socket as stdlib_socket
from unittest.mock import MagicMock, call

from interactive.file_service import HELPER, FileService, FileServiceError


def _result_bytes(payload: dict):
    import json
    import struct

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return struct.pack(">BxxxL", 1, len(body)) + body


def _client_for(response: dict, stream=None):
    client = MagicMock()
    client.api.exec_create.return_value = {"Id": "exec-1"}
    sent = {}
    chunks = []

    body = _result_bytes(response)
    # Split header/body so the reader must loop (covers partial recv).
    chunks.extend([body[:4], body[4:8], body[8:]])

    class FakeRaw:
        def __init__(self, parts):
            self.sent = b""
            self.parts = list(parts)
            self.closed = False
            self.shutdown_how = None

        def sendall(self, data):
            self.sent += data
            sent["payload"] = self.sent

        def shutdown(self, how):
            self.shutdown_how = how
            assert how == stdlib_socket.SHUT_WR

        def recv(self, n):
            if not self.parts:
                return b""
            part = self.parts.pop(0)
            if len(part) > n:
                self.parts.insert(0, part[n:])
                return part[:n]
            return part

        def close(self):
            self.closed = True

    raw = FakeRaw(chunks)
    fake_stream = stream if stream is not None else raw
    client.api.exec_start.return_value = fake_stream
    client.api.exec_inspect.return_value = {"ExitCode": 0}
    client.sent = sent
    client.raw = raw
    return client


def test_list_payload_is_accepted_by_helper_allowlist():
    """Regression: request carries root; HELPER allow-list must include it."""
    import json
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp())
    (tmp / "hello.txt").write_text("hi")
    req = {"operation": "list", "root": str(tmp), "path": ""}
    proc = subprocess.run(
        [sys.executable, "-c", HELPER],
        input=json.dumps(req).encode(),
        capture_output=True,
        timeout=15,
    )
    body = json.loads(proc.stdout.decode())
    assert body.get("ok") is True, body
    assert body["entries"] == [{"name": "hello.txt", "type": "file"}]


def test_call_sends_helper_all_operations_end_to_end():
    import json

    for operation, kwargs, expected in [
        ("list", {"path": ""}, {"entries": [], "next_cursor": None}),
        ("stat", {"path": "a.txt"}, {"type": "file"}),
    ]:
        client = _client_for({"ok": True, **expected})
        svc = FileService(client, "container-1", "10001", "/workspace")
        out = svc.call(operation, **kwargs)
        assert out == expected
        payload = json.loads(client.sent["payload"].decode())
        assert payload["operation"] == operation
        assert payload["root"] == "/workspace"


def test_exec_start_called_without_stdin():
    """docker-py 7.2.0 exec_start() has no stdin kwarg; must not be passed."""
    client = _client_for({"ok": True, "entries": []})
    FileService(client, "container-1", "10001", "/workspace").call("list", path="")
    assert client.api.exec_start.call_args == call("exec-1", socket=True, tty=False)
    assert "stdin" not in client.api.exec_start.call_args.kwargs
    create_kwargs = client.api.exec_create.call_args.kwargs
    assert create_kwargs["stdin"] is True  # stdin attaches at create time


def test_helper_protocol_error_surfaces_code():
    client = _client_for({"ok": False, "code": "NOT_FOUND"})
    try:
        FileService(client, "c", "10001", "/workspace").call("stat", path="nope")
    except FileServiceError as exc:
        assert exc.code == "NOT_FOUND"
    else:
        raise AssertionError("expected FileServiceError")


def test_socket_shape_variants_are_unwrapped():
    """docker-py returns the raw socket; wrappers exposing _sock also work."""
    import socket as stdlib_socket

    for wrap in (
        lambda raw: raw,
        lambda raw: type("W1", (), {"_sock": raw, "close": raw.close})(),
        lambda raw: type(
            "W2", (), {"_sock": type("W1", (), {"_sock": raw})(), "close": raw.close}
        )(),
    ):
        client = _client_for({"ok": True, "entries": []})
        raw = client.raw
        client.api.exec_start.return_value = wrap(raw)
        out = FileService(client, "c", "10001", "/workspace").call("list", path="")
        assert out == {"entries": []}
        assert raw.shutdown_how == stdlib_socket.SHUT_WR


def test_directory_stat_returns_version_and_guards_rename_delete():
    """Directory version semantics: stat returns a version; stale rename/delete conflict."""
    import json
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    def call(tmp, req):
        proc = subprocess.run(
            [sys.executable, "-c", HELPER],
            input=json.dumps(req).encode(),
            capture_output=True,
            timeout=15,
        )
        return json.loads(proc.stdout.decode())

    tmp = Path(tempfile.mkdtemp())
    (tmp / "d").mkdir()
    (tmp / "d" / "a.txt").write_text("hi")
    stat = call(tmp, {"operation": "stat", "root": str(tmp), "path": "d"})
    assert stat.get("ok") is True and stat.get("type") == "directory"
    assert isinstance(stat.get("version"), str) and stat["version"]
    # Stale versions conflict instead of silently overwriting.
    assert call(tmp, {"operation": "rename", "root": str(tmp), "path": "d", "target": "d2", "expected_version": "stale"}) == {"ok": False, "code": "CONFLICT"}
    good = call(tmp, {"operation": "rename", "root": str(tmp), "path": "d", "target": "d2", "expected_version": stat["version"]})
    assert good == {"ok": True}
    stat2 = call(tmp, {"operation": "stat", "root": str(tmp), "path": "d2"})
    assert stat2["ok"] is True
    assert call(tmp, {"operation": "delete", "root": str(tmp), "path": "d2", "expected_version": "stale"}) == {"ok": False, "code": "CONFLICT"}
