import asyncio
import logging
import uuid
from .clock import Clock
from .broker_client import connect, close_writer
from .protocol import Type, ProtocolError, dimensions, read_record, write_record, json_bytes, parse_json
from .workspace_protocol import metadata as workspace_metadata, unpack_chunk

log = logging.getLogger('interactive_access')


class Capacity:
    def __init__(self, maximum):
        self.maximum = maximum
        self.active = 0

    def acquire(self):
        # No await: atomic on the listener's event loop.
        if self.active >= self.maximum:
            return False
        self.active += 1
        return True

    def release(self):
        assert self.active > 0
        self.active -= 1


async def run_workspace_session(reader, writer, config, clock, hello=None):
    """Relay workspace-stream-v1 after validating its application handshake.

    Access remains a narrow authenticated proxy: paths and Docker identifiers
    are never interpreted here, and WorkspaceSession on the Worker remains the
    policy enforcement point.
    """
    broker = None
    tasks = []
    try:
        if hello is None:
            kind, hello = await clock.wait(read_record(reader), config.open_timeout)
            if kind != Type.HELLO:
                raise ProtocolError()
        value = workspace_metadata(hello)
        if set(value) != {"protocol"} or value["protocol"] != "workspace-stream-v1":
            raise ProtocolError()
        broker_reader, broker = await connect(config, clock)
        await write_record(broker, Type.HELLO, hello, config.write_timeout)
        kind, ready = await clock.wait(read_record(broker_reader), config.broker_timeout)
        if kind != Type.WORKSPACE_READY:
            raise OSError()
        workspace_metadata(ready)
        await write_record(writer, kind, ready, config.write_timeout)

        def client_record(kind, payload):
            if kind in (Type.FILE_REQUEST, Type.FILE_END, Type.CANCEL, Type.PTY_OPEN, Type.PTY_RESIZE):
                workspace_metadata(payload)
            elif kind == Type.FILE_CHUNK:
                unpack_chunk(payload)
            elif kind in (Type.PTY_STDIN,):
                if len(payload) > 65530:
                    raise ProtocolError()
            elif kind in (Type.PTY_CLOSE, Type.CLOSE):
                if payload:
                    raise ProtocolError()
            else:
                raise ProtocolError()

        def server_record(kind, payload):
            if kind in (Type.FILE_RESULT, Type.FILE_END, Type.PTY_OPENED, Type.PTY_EXIT, Type.WORKSPACE_READY, Type.WORKSPACE_STATE, Type.ERROR):
                workspace_metadata(payload)
            elif kind == Type.FILE_CHUNK:
                unpack_chunk(payload)
            elif kind == Type.PTY_STDOUT:
                if len(payload) > 65530:
                    raise ProtocolError()
            else:
                raise ProtocolError()

        async def forward(source, target, validate):
            while True:
                kind, payload = await read_record(source)
                validate(kind, payload)
                await write_record(target, kind, payload, config.write_timeout)
                if kind == Type.CLOSE:
                    return

        tasks = [
            asyncio.create_task(forward(reader, broker, client_record)),
            asyncio.create_task(forward(broker_reader, writer, server_record)),
        ]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if broker:
            try:
                await write_record(broker, Type.CLOSE, timeout=1)
            except (OSError, TimeoutError):
                pass
        await close_writer(broker)


async def run_session(reader, writer, config, capacity, clock=None):
    clock = clock or Clock()
    session_id = str(uuid.uuid4())
    started = clock.now()
    broker = None
    tasks = []
    acquired = capacity.acquire()
    counts = {'input': 0, 'output': 0}
    outcome = 'EXIT'
    try:
        writer.transport.set_write_buffer_limits(high=65536, low=16384)
        if not acquired:
            outcome = 'BUSY'
            await write_record(writer, Type.ERROR, json_bytes({'code': outcome}))
            return
        kind, payload = await clock.wait(read_record(reader), config.open_timeout)
        if kind == Type.HELLO:
            await run_workspace_session(reader, writer, config, clock, payload)
            return
        if kind != Type.OPEN:
            raise ProtocolError()
        dimensions(payload, opening=True)
        broker_reader, broker = await connect(config, clock)
        await write_record(broker, Type.OPEN, payload, config.write_timeout)
        kind, metadata = await clock.wait(read_record(broker_reader), config.broker_timeout)
        if kind != Type.OPENED:
            raise OSError()
        parse_json(metadata)
        await write_record(writer, Type.OPENED, json_bytes({'session_id': session_id, 'protocol': 'terminal-stream-v1'}))

        async def incoming():
            while True:
                kind, payload = await read_record(reader)
                if kind == Type.RESIZE:
                    dimensions(payload)
                elif kind == Type.CLOSE:
                    if payload:
                        raise ProtocolError()
                    await write_record(broker, kind, payload, config.write_timeout)
                    # The broker answers CLOSE with EXIT (or EOF); outgoing()
                    # owns the broker stream and forwards it. Wait for it
                    # instead of returning here: returning would let the
                    # session teardown win the race, cancel outgoing(), and
                    # drop EXIT, leaving a client that follows the
                    # OPEN/OPENED/CLOSE/EXIT handshake (e.g. connection
                    # verification) stuck waiting for a record that never
                    # arrives; it then reports the clean close as a failure.
                    # The shield keeps a drain timeout from cancelling the
                    # relay itself; teardown still owns cancellation after.
                    await clock.wait(asyncio.shield(outgoing_task), config.broker_timeout)
                    return
                elif kind != Type.STDIN:
                    raise ProtocolError()
                counts['input'] += len(payload)
                await write_record(broker, kind, payload, config.write_timeout)

        async def outgoing():
            while True:
                kind, payload = await read_record(broker_reader)
                if kind == Type.EXIT:
                    value = parse_json(payload)
                    if set(value) != {'code', 'reason'} or type(value['code']) is not int or not -255 <= value['code'] <= 255:
                        raise OSError()
                    await write_record(writer, kind, json_bytes({'code': value['code'], 'reason': 'exited'}))
                    return
                if kind != Type.STDOUT:
                    raise OSError()
                counts['output'] += len(payload)
                await write_record(writer, kind, payload, config.write_timeout)

        # Outgoing is created first so incoming() can wait for the broker's
        # CLOSE answer (see above) without ever sharing the broker stream.
        outgoing_task = asyncio.create_task(outgoing())
        tasks = [asyncio.create_task(incoming()), outgoing_task]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except asyncio.CancelledError:
        outcome = 'SHUTDOWN'
        raise
    except EOFError:
        outcome = 'DISCONNECTED'
    except Exception as exc:
        outcome = 'PROTOCOL_ERROR' if isinstance(exc, ProtocolError) else 'TIMEOUT' if isinstance(exc, TimeoutError) else 'UNAVAILABLE'
        try:
            await write_record(writer, Type.ERROR, json_bytes({'code': outcome}), config.write_timeout)
        except (OSError, TimeoutError):
            pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if broker:
            try:
                await write_record(broker, Type.CLOSE, timeout=1)
            except (OSError, TimeoutError):
                pass
        await close_writer(broker)
        await close_writer(writer)
        if acquired:
            capacity.release()
        log.info('runtime=%s session=%s outcome=%s duration=%.3f input=%d output=%d',
                 config.runtime_id, session_id, outcome, clock.now() - started, counts['input'], counts['output'])
