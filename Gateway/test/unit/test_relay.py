import asyncio
from datetime import datetime, timezone
import time
from types import SimpleNamespace

import pytest

from interactive_gateway.management_client import ManagementError
from interactive_gateway.relay import relay


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


class Websocket:
    def __init__(self):
        self.input = asyncio.Queue(maxsize=1)
        self.output = []
    async def receive(self):
        return await self.input.get()
    async def send_bytes(self, data):
        self.output.append(data)


class Writer:
    def __init__(self):
        self.output = []
        self.drained = 0
    def write(self, data):
        self.output.append(data)
    async def drain(self):
        self.drained += 1


class Management:
    def __init__(self, error=None, hang=False):
        self.error, self.hang = error, hang
    async def renew(self, session):
        if self.hang:
            await asyncio.Future()
        raise self.error or ManagementError()


@pytest.mark.asyncio
async def test_binary_stream_eof_and_backpressure():
    ws, reader, writer = Websocket(), asyncio.StreamReader(), Writer()
    settings = SimpleNamespace(frame_max=65536, renewal_interval=0.1, idle_timeout=1)
    record = {"session_id": "s", "version": "v", "lease_expires_at": utc(time.time() + 1), "deadline": utc(time.time() + 5)}
    task = asyncio.create_task(relay(ws, reader, writer, record, Management(), settings))
    payload = b"\x00\xff\x80" * 1000
    await ws.input.put({"type": "websocket.receive", "bytes": payload})
    reader.feed_data(payload)
    await asyncio.sleep(0.02)
    reader.feed_eof()
    code, counts = await task
    assert code == 1000 and writer.output == [payload] and writer.drained == 1
    assert b"".join(ws.output) == payload
    assert counts == {"sent": len(payload), "received": len(payload)}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["outage", "hung", "revoked", "idle", "absolute", "text", "oversize", "disconnect"])
async def test_deadlines_and_failure_cancel_siblings(case):
    ws, reader, writer = Websocket(), asyncio.StreamReader(), Writer()
    settings = SimpleNamespace(frame_max=65536, renewal_interval=0.01, idle_timeout=0.03 if case == "idle" else 5)
    record = {"session_id": "s", "version": "v", "lease_expires_at": utc(time.time() + 0.1),
              "deadline": utc(time.time() + (0.03 if case == "absolute" else 5))}
    management = Management(ManagementError(410) if case == "revoked" else None, hang=case == "hung")
    task = asyncio.create_task(relay(ws, reader, writer, record, management, settings))
    if case in ("text", "oversize", "disconnect"):
        await ws.input.put({"type": "websocket.disconnect"} if case == "disconnect" else
                           {"type": "websocket.receive", "text": "secret"} if case == "text" else
                           {"type": "websocket.receive", "bytes": b"x" * 65537})
    started = time.monotonic()
    code, _ = await asyncio.wait_for(task, 0.5)
    assert time.monotonic() - started < 0.3
    assert code == (1000 if case in ("idle", "disconnect") else 4403 if case in ("text", "oversize") else 4410)


@pytest.mark.asyncio
async def test_cancellation_cancels_all_relay_tasks():
    ws, reader, writer = Websocket(), asyncio.StreamReader(), Writer()
    settings = SimpleNamespace(frame_max=65536, renewal_interval=1, idle_timeout=5)
    record = {"session_id": "s", "version": "v", "lease_expires_at": utc(time.time() + 5), "deadline": utc(time.time() + 5)}
    before = set(asyncio.all_tasks())
    task = asyncio.create_task(relay(ws, reader, writer, record, Management(), settings))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (set(asyncio.all_tasks()) - before)
