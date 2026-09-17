import asyncio
from types import SimpleNamespace
from interactive_access.clock import Clock
from interactive_access.protocol import Type, Parser
from interactive_access.session import run_session, Capacity
from interactive_access.broker_client import connect
import pytest


class ManualClock(Clock):
    def __init__(self):
        self.value = 0
        self.timers = []

    def now(self):
        return self.value

    async def sleep(self, seconds):
        future = asyncio.get_running_loop().create_future()
        self.timers.append((self.value + seconds, future))
        await future

    def advance(self, seconds):
        self.value += seconds
        for deadline, future in self.timers:
            if deadline <= self.value and not future.done():
                future.set_result(None)


async def timer_started(clock):
    while not clock.timers:
        await asyncio.sleep(0)


class Writer:
    def __init__(self):
        self.transport = SimpleNamespace(set_write_buffer_limits=lambda **kwargs: None)
        self.output = bytearray()
        self.closed = False
    def write(self, data):
        self.output.extend(data)
    async def drain(self):
        pass
    def close(self):
        self.closed = True
    async def wait_closed(self):
        pass


async def test_open_deadline_with_injected_clock():
    clock = ManualClock()
    writer = Writer()
    capacity = Capacity(1)
    config = SimpleNamespace(open_timeout=5, write_timeout=5, runtime_id='test')
    task = asyncio.create_task(run_session(asyncio.StreamReader(), writer, config, capacity, clock))
    await timer_started(clock)
    clock.advance(5)
    await task
    assert Parser().feed(writer.output) == [(Type.ERROR, b'{"code":"TIMEOUT"}')]
    assert writer.closed and capacity.active == 0


async def test_broker_connection_deadline_with_injected_clock(monkeypatch):
    from interactive_access import broker_client
    clock = ManualClock()
    cancelled = asyncio.Event()
    async def blocked_connection(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr(broker_client.asyncio, 'open_unix_connection', blocked_connection)
    config = SimpleNamespace(credentials=lambda: b'test', socket_path='/test/socket', broker_timeout=3)
    task = asyncio.create_task(connect(config, clock))
    await timer_started(clock)
    clock.advance(3)
    with pytest.raises(TimeoutError):
        await task
    assert cancelled.is_set()
