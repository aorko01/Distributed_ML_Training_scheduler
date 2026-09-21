import asyncio
import logging
import uuid
from contextlib import suppress as _suppress
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


async def run_workspace_session(reader, writer, config, clock, hello=None, session_id=None):
    """Relay workspace-stream-v1 after validating its application handshake.

    Access remains a narrow authenticated proxy: paths and Docker identifiers
    are never interpreted here, and WorkspaceSession on the Worker remains the
    policy enforcement point.

    Returns ``(outcome, detail, counts)`` so the caller can log a
    disconnect-traceable summary: byte counts per direction, the last record
    kind seen on each leg, and which side closed first. Payload bytes are
    never logged, only kinds and sizes.
    """
    session_id = session_id or str(uuid.uuid4())
    started = clock.now()
    broker = None
    tasks = []
    counts = {'input': 0, 'output': 0, 'file_ops': 0, 'pty_events': 0}
    last_client = 'HELLO'
    last_server = '-'
    outcome = 'WORKSPACE_EXIT'
    detail = 'closed'
    close_side = None
    try:
        if hello is None:
            try:
                kind, hello = await clock.wait(read_record(reader), config.open_timeout)
            except TimeoutError:
                outcome, detail = 'WORKSPACE_TIMEOUT', 'waiting for HELLO'
                raise
            if kind != Type.HELLO:
                outcome, detail = 'WORKSPACE_PROTOCOL_ERROR', f'expected HELLO got kind={int(kind)}'
                raise ProtocolError()
        value = workspace_metadata(hello)
        if set(value) != {"protocol"} or value["protocol"] != "workspace-stream-v1":
            outcome, detail = 'WORKSPACE_PROTOCOL_ERROR', f"bad HELLO protocol={value.get('protocol')!r}"
            raise ProtocolError()
        try:
            broker_reader, broker = await connect(config, clock)
        except (OSError, TimeoutError) as exc:
            outcome, detail = 'WORKSPACE_UNAVAILABLE', f'broker connect failed: {type(exc).__name__}'
            raise
        await write_record(broker, Type.HELLO, hello, config.write_timeout)
        try:
            kind, ready = await clock.wait(read_record(broker_reader), config.broker_timeout)
        except TimeoutError:
            outcome, detail = 'WORKSPACE_TIMEOUT', 'waiting for WORKSPACE_READY'
            raise
        if kind != Type.WORKSPACE_READY:
            outcome, detail = 'WORKSPACE_UNAVAILABLE', f'expected WORKSPACE_READY got kind={int(kind)}'
            raise OSError()
        workspace_metadata(ready)
        await write_record(writer, kind, ready, config.write_timeout)
        last_server = 'WORKSPACE_READY'
        # The handshake itself bypasses the relay loop below; count it so
        # byte totals cover the whole session lifetime.
        counts['input'] += len(hello)
        counts['output'] += len(ready)

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

        async def forward(source, target, validate, direction):
            nonlocal last_client, last_server, close_side
            while True:
                try:
                    kind, payload = await read_record(source)
                except EOFError:
                    if close_side is None:
                        close_side = direction
                    return
                name = getattr(kind, 'name', str(kind))
                if direction == 'client':
                    last_client = name
                    counts['input'] += len(payload)
                    if kind in (Type.FILE_REQUEST, Type.PTY_OPEN):
                        counts['file_ops' if kind == Type.FILE_REQUEST else 'pty_events'] += 1
                    elif kind in (Type.PTY_STDIN, Type.PTY_RESIZE, Type.PTY_CLOSE):
                        counts['pty_events'] += 1
                else:
                    last_server = name
                    counts['output'] += len(payload)
                try:
                    validate(kind, payload)
                except ProtocolError:
                    log.warning('runtime=%s session=%s workspace_reject side=%s kind=%s size=%d last_client=%s last_server=%s',
                                config.runtime_id, session_id, direction, name, len(payload), last_client, last_server)
                    raise
                try:
                    await write_record(target, kind, payload, config.write_timeout)
                except (OSError, TimeoutError) as exc:
                    log.warning('runtime=%s session=%s workspace_drain_failed side=%s kind=%s size=%d error=%s',
                                config.runtime_id, session_id, direction, name, len(payload), type(exc).__name__)
                    raise
                if kind == Type.CLOSE:
                    if close_side is None:
                        close_side = direction
                    return

        tasks = [
            asyncio.create_task(forward(reader, broker, client_record, 'client')),
            asyncio.create_task(forward(broker_reader, writer, server_record, 'broker')),
        ]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        if close_side == 'broker' and last_client != 'CLOSE':
            # The Worker went away without the client closing first: the
            # container died, the broker lost authority, or the Worker
            # restarted. The UI will see a bare disconnect for this.
            outcome, detail = 'WORKSPACE_BACKEND_EOF', f'last_client={last_client} last_server={last_server}'
    except asyncio.CancelledError:
        outcome, detail = 'WORKSPACE_SHUTDOWN', 'cancelled'
        raise
    except EOFError:
        outcome, detail = 'WORKSPACE_DISCONNECTED', f'{close_side or "peer"} EOF last_client={last_client} last_server={last_server}'
    except ProtocolError:
        if outcome == 'WORKSPACE_EXIT':
            outcome, detail = 'WORKSPACE_PROTOCOL_ERROR', f'last_client={last_client} last_server={last_server}'
        with _suppress(OSError, TimeoutError):
            await write_record(writer, Type.ERROR, json_bytes({'code': 'PROTOCOL_ERROR'}), config.write_timeout)
    except TimeoutError:
        if outcome == 'WORKSPACE_EXIT':
            outcome, detail = 'WORKSPACE_TIMEOUT', f'last_client={last_client} last_server={last_server}'
        with _suppress(OSError, TimeoutError):
            await write_record(writer, Type.ERROR, json_bytes({'code': 'TIMEOUT'}), config.write_timeout)
    except OSError as exc:
        if outcome == 'WORKSPACE_EXIT':
            outcome, detail = 'WORKSPACE_UNAVAILABLE', f'{type(exc).__name__} last_client={last_client} last_server={last_server}'
        with _suppress(OSError, TimeoutError):
            await write_record(writer, Type.ERROR, json_bytes({'code': 'UNAVAILABLE'}), config.write_timeout)
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
        level = log.warning if outcome != 'WORKSPACE_EXIT' else log.info
        level('runtime=%s session=%s outcome=%s detail=%s duration=%.3f input=%d output=%d file_ops=%d pty_events=%d',
              config.runtime_id, session_id, outcome, detail, clock.now() - started,
              counts['input'], counts['output'], counts['file_ops'], counts['pty_events'])
    return outcome, detail, counts


async def run_session(reader, writer, config, capacity, clock=None):
    clock = clock or Clock()
    session_id = str(uuid.uuid4())
    started = clock.now()
    broker = None
    tasks = []
    acquired = capacity.acquire()
    counts = {'input': 0, 'output': 0}
    outcome = 'EXIT'
    detail = ''
    workspace_logged = False
    try:
        writer.transport.set_write_buffer_limits(high=65536, low=16384)
        if not acquired:
            outcome = 'BUSY'
            await write_record(writer, Type.ERROR, json_bytes({'code': outcome}))
            return
        kind, payload = await clock.wait(read_record(reader), config.open_timeout)
        if kind == Type.HELLO:
            # The workspace relay logs its own disconnect-traceable summary
            # (byte counts, last record kinds, close side); skip the generic
            # line below so one session emits exactly one summary.
            outcome, detail, counts = await run_workspace_session(reader, writer, config, clock, payload, session_id)
            workspace_logged = True
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
        if not workspace_logged:
            log.info('runtime=%s session=%s outcome=%s duration=%.3f input=%d output=%d',
                     config.runtime_id, session_id, outcome, clock.now() - started, counts['input'], counts['output'])
