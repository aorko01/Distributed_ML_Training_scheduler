import asyncio
import hashlib
import hmac
from .clock import Clock
from .protocol import Type, ProtocolError, read_record, write_record


async def close_writer(writer):
    if writer:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 1)
        except (OSError, TimeoutError):
            pass


async def _connect(config):
    writer = None
    try:
        token = config.credentials()
        reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=65536)
        writer.transport.set_write_buffer_limits(high=65536, low=16384)
        kind, challenge = await read_record(reader)
        if kind != Type.CHALLENGE or len(challenge) != 32:
            raise ProtocolError()
        proof = hmac.digest(token, b'dml-broker-v1\0' + challenge, hashlib.sha256)
        await write_record(writer, Type.AUTH, proof, config.write_timeout)
        if await read_record(reader) != (Type.AUTHENTICATED, b''):
            raise ProtocolError()
        return reader, writer
    except BaseException:
        await close_writer(writer)
        raise


async def connect(config, clock=None):
    return await (clock or Clock()).wait(_connect(config), config.broker_timeout)


async def ready(config, clock=None):
    clock = clock or Clock()
    async def probe():
        writer = None
        try:
            reader, writer = await connect(config, clock)
            await write_record(writer, Type.PROBE)
            return await read_record(reader) == (Type.READY, b'')
        finally:
            await close_writer(writer)
    try:
        return await clock.wait(probe(), config.broker_timeout)
    except (OSError, ValueError, ProtocolError, EOFError, TimeoutError):
        return False
