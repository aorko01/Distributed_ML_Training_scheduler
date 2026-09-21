"""WorkspaceSession: PTY lifecycle, send serialization, idempotent shutdown."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from Access_Container.interactive_access.protocol import Type
from Access_Container.interactive_access.workspace_protocol import metadata_bytes
from interactive.workspace_broker import WorkspaceSession


class FakeWriter:
    def __init__(self):
        self.records = []

    async def drain(self):
        return None


def make_session():
    broker = SimpleNamespace(client=MagicMock(), container_id="c", user="u", workdir="/w", healthy=lambda: True, authority=lambda: True, stopping=False)
    reader = MagicMock()
    writer = FakeWriter()
    session = WorkspaceSession(broker, reader, writer)
    return session, writer


def test_send_lock_serializes_file_response_and_pty_output():
    async def scenario():
        session, writer = make_session()
        order = []
        real_write = __import__("Access_Container.interactive_access.protocol", fromlist=["write_record"]).write_record
        async def slow_write(w, kind, payload=b"", timeout=5):
            order.append(("start", kind))
            await asyncio.sleep(0.01)
            order.append(("end", kind))
        with patch("interactive.workspace_broker.write_record", side_effect=slow_write):
            await asyncio.gather(session.send(Type.FILE_RESULT, b"a"), session.send(Type.PTY_STDOUT, b"b"))
        # Serialized: first record fully ends before the second starts.
        assert order[0] == ("start", Type.FILE_RESULT) and order[1] == ("end", Type.FILE_RESULT)
        assert order[2] == ("start", Type.PTY_STDOUT) and order[3] == ("end", Type.PTY_STDOUT)
    asyncio.run(scenario())


def test_close_pty_is_idempotent_single_exit():
    async def scenario():
        session, writer = make_session()
        sent = []
        async def fake_send(kind, value=b""):
            sent.append(kind)
        session.send = fake_send  # type: ignore[method-assign]
        calls = {"n": 0}
        class FakePty:
            def close(self):
                calls["n"] += 1
                return True
        session.pty = FakePty()
        await session.close_pty()
        await session.close_pty()
        assert calls["n"] == 1
        assert sent.count(Type.PTY_EXIT) == 1
    asyncio.run(scenario())


def test_close_is_idempotent_and_blocks_late_send():
    async def scenario():
        session, writer = make_session()
        session.pty = None
        await session.close()
        await session.close()
        assert session.closed is True
        try:
            await session.send(Type.PTY_STDOUT, b"late")
        except ConnectionError:
            return
        raise AssertionError("late send must fail after close")
    asyncio.run(scenario())


def test_duplicate_pty_open_rejected():
    async def scenario():
        session, writer = make_session()
        session.pty = object()
        try:
            await session.pty_open({"columns": 80, "rows": 24, "shell": "default"})
        except Exception as exc:
            assert exc.__class__.__name__ == "ProtocolError"
            return
        raise AssertionError("duplicate PTY_OPEN must raise")
    asyncio.run(scenario())


def test_pty_open_after_exit_allows_restart():
    async def scenario():
        session, writer = make_session()
        sent = []

        async def fake_send(kind, value=b""):
            sent.append(kind)

        session.send = fake_send  # type: ignore[method-assign]
        calls = {"n": 0}

        class FakePty:
            def close(self):
                calls["n"] += 1
                return True

        session.pty = FakePty()
        await session.close_pty()
        assert session.exited is True
        # Restart path: a fresh WorkspaceSession generation serves the next PTY.
        fresh, _ = make_session()
        fresh.send = fake_send  # type: ignore[method-assign]
        assert fresh.pty is None and fresh.exited is False
        assert sent.count(Type.PTY_EXIT) == 1

    asyncio.run(scenario())


def test_pty_write_failure_ends_terminal_not_session():
    async def scenario():
        session, _ = make_session()
        sent = []

        async def fake_send(kind, value=b""):
            sent.append(kind)

        session.send = fake_send  # type: ignore[method-assign]

        class DeadPty:
            def write(self, _data):
                raise OSError("broken shell")

            def close(self):
                return True

        session.pty = DeadPty()
        records = [(Type.PTY_STDIN, b"ls\n"), (Type.CLOSE, b"")]

        async def fake_read(_reader):
            if records:
                return records.pop(0)
            await asyncio.sleep(3600)

        with patch("interactive.workspace_broker.read_record", side_effect=fake_read):
            # Must not raise: the dead shell ends the terminal (PTY_EXIT)
            # while the session itself survives for files + editor.
            await session.run({"protocol": "workspace-stream-v1"})
        assert Type.PTY_EXIT in sent
        assert session.pty is None

    asyncio.run(scenario())


def test_pty_resize_failure_is_ignored():
    async def scenario():
        session, _ = make_session()
        sent = []

        async def fake_send(kind, value=b""):
            sent.append(kind)

        session.send = fake_send  # type: ignore[method-assign]

        class FlakyPty:
            def resize(self, _value):
                raise OSError("transient docker-API failure")

        pty = FlakyPty()
        session.pty = pty
        records = [
            (Type.PTY_RESIZE, metadata_bytes({"columns": 100, "rows": 30})),
            (Type.CLOSE, b""),
        ]

        async def fake_read(_reader):
            if records:
                return records.pop(0)
            await asyncio.sleep(3600)

        with patch("interactive.workspace_broker.read_record", side_effect=fake_read):
            await session.run({"protocol": "workspace-stream-v1"})
        # Resize failure is swallowed: no exit, shell handle intact.
        assert Type.PTY_EXIT not in sent
        assert session.pty is pty

    asyncio.run(scenario())
