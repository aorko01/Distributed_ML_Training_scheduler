import asyncio
from dataclasses import replace
import hmac
import os
from pathlib import Path
import pytest
from interactive_access.config import Config, read_secret
from interactive_access.main import Service
from interactive_access.broker_client import ready, close_writer
from interactive_access.protocol import Type, read_record, write_record, json_bytes, encode
from fake_broker import FakeBroker


class TestConfig(Config):
    __test__ = False
    def validate(self):
        pass  # Test sockets are in pytest's private temp directory.


@pytest.fixture
async def endpoint(tmp_path):
    token = os.urandom(32).hex().encode()
    (tmp_path / 'token').write_bytes(token)
    (tmp_path / 'token').chmod(0o600)
    broker = await FakeBroker(str(tmp_path / 'broker.sock'), token, str(tmp_path)).start()
    config = TestConfig(str(tmp_path / 'broker.sock'), str(tmp_path / 'token'), 'test', port=0, health_port=0, open_timeout=.1)
    service = await Service(config).start()
    port = service.servers[0].sockets[0].getsockname()[1]
    yield service, broker, config, port, tmp_path
    await service.stop()
    await broker.stop()
    assert not broker.children
    assert service.capacity.active == 0


async def open_terminal(port):
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    await write_record(writer, Type.OPEN, json_bytes({'columns': 80, 'rows': 24, 'shell': 'default'}))
    assert (await read_record(reader))[0] == Type.OPENED
    return reader, writer


async def output_until(reader, needle):
    output = b''
    async with asyncio.timeout(5):
        while needle not in output:
            kind, payload = await read_record(reader)
            assert kind == Type.STDOUT
            output += payload
    return output


async def wait_cleanup(service, broker):
    async with asyncio.timeout(3):
        while service.capacity.active or broker.children:
            await asyncio.sleep(.01)


async def test_real_pty_workload_environment_resize_fragmentation_exit(endpoint):
    service, broker, config, port, root = endpoint
    assert await ready(config)
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    record = encode(Type.OPEN, json_bytes({'columns': 80, 'rows': 24, 'shell': 'default'}))
    for byte in record:
        writer.write(bytes([byte]))
        await writer.drain()
    assert (await read_record(reader))[0] == Type.OPENED
    await write_record(writer, Type.STDIN, b'pwd; printf "probe:%s\\n" "$FAKE_WORKLOAD"\n')
    output = await output_until(reader, b'probe:yes')
    assert str(root).encode() in output
    await write_record(writer, Type.RESIZE, json_bytes({'columns': 120, 'rows': 40}))
    await write_record(writer, Type.STDIN, b'stty size\n')
    assert b'40 120' in await output_until(reader, b'40 120')
    await write_record(writer, Type.STDIN, 'printf "héllo\\n"\n'.encode())
    await output_until(reader, 'héllo'.encode())
    await write_record(writer, Type.STDIN, b'exit 7\n')
    async with asyncio.timeout(3):
        while True:
            kind, payload = await read_record(reader)
            if kind == Type.EXIT:
                assert b'"code":7' in payload
                break
    await close_writer(writer)
    await wait_cleanup(service, broker)


@pytest.mark.parametrize('failure', ['disconnect', 'shutdown', 'broker-crash', 'invalid'])
async def test_cleanup(endpoint, failure):
    service, broker, _, port, _ = endpoint
    reader, writer = await open_terminal(port)
    if failure == 'disconnect':
        await close_writer(writer)
    elif failure == 'shutdown':
        await service.stop()
    elif failure == 'broker-crash':
        await broker.stop()
    else:
        await write_record(writer, Type.OPEN, b'{}')
    await wait_cleanup(service, broker)
    await close_writer(writer)


async def test_capacity_and_open_timeout(endpoint):
    service, broker, _, port, _ = endpoint
    reader, writer = await open_terminal(port)
    r2, w2 = await asyncio.open_connection('127.0.0.1', port)
    assert (await read_record(r2)) == (Type.ERROR, b'{"code":"BUSY"}')
    await close_writer(w2)
    await close_writer(writer)
    await wait_cleanup(service, broker)
    r3, w3 = await asyncio.open_connection('127.0.0.1', port)
    assert await read_record(r3) == (Type.ERROR, b'{"code":"TIMEOUT"}')
    await close_writer(w3)
    await wait_cleanup(service, broker)


async def test_auth_readiness_replay_and_arbitrary_fields(endpoint):
    _, broker, config, _, root = endpoint
    assert not await ready(replace(config, socket_path=str(root / 'missing.sock')))
    (root / 'wrong').write_bytes(os.urandom(32).hex().encode())
    (root / 'wrong').chmod(0o600)
    assert not await ready(replace(config, token_file=str(root / 'wrong')))
    reader, writer = await asyncio.open_unix_connection(broker.socket)
    _, challenge = await read_record(reader)
    proof = hmac.digest(broker.token, b'dml-broker-v1\0' + challenge, 'sha256')
    await write_record(writer, Type.AUTH, proof)
    assert await read_record(reader) == (Type.AUTHENTICATED, b'')
    await write_record(writer, Type.OPEN, json_bytes({'columns': 80, 'rows': 24, 'shell': 'default', 'command': 'id'}))
    with pytest.raises(EOFError):
        await read_record(reader)
    await close_writer(writer)
    reader, writer = await asyncio.open_unix_connection(broker.socket)
    await read_record(reader)
    await write_record(writer, Type.AUTH, proof)
    with pytest.raises(EOFError):
        await read_record(reader)
    await close_writer(writer)
    assert not broker.children


async def test_large_output_backpressure_and_control_c(endpoint):
    service, broker, _, port, _ = endpoint
    reader, writer = await open_terminal(port)
    await write_record(writer, Type.STDIN, b'yes terminal_output\n')
    await asyncio.sleep(.1)  # Deliberately slow consumer; no application queue.
    assert writer.transport.get_write_buffer_size() <= 65536
    await write_record(writer, Type.STDIN, b'\x03')
    await write_record(writer, Type.STDIN, b'printf "control_ok\\n"\n')
    await output_until(reader, b'control_ok')
    await close_writer(writer)
    await wait_cleanup(service, broker)


async def test_health_and_unready_listener(endpoint):
    service, broker, config, port, root = endpoint
    health_port = service.servers[1].sockets[0].getsockname()[1]
    async def health(path):
        reader, writer = await asyncio.open_connection('127.0.0.1', health_port)
        writer.write(f'GET /health/{path} HTTP/1.1\r\nHost: localhost\r\n\r\n'.encode())
        await writer.drain()
        response = await reader.read()
        await close_writer(writer)
        return response
    assert b'200 Status' in await health('ready')
    await broker.stop()
    assert b'200 Status' in await health('live')
    response = await health('ready')
    assert b'503 Status' in response
    assert str(root).encode() not in response and broker.token not in response
    await service.update_readiness()
    with pytest.raises(OSError):
        await asyncio.open_connection('127.0.0.1', port)


@pytest.mark.parametrize('kind,payload', [(Type.STDIN, b'sensitive-terminal-bytes'), (Type.RESIZE, b'{}'), (Type.OPEN, b'{"shell":"/bin/bash","columns":80,"rows":24}'), (Type.OPEN, b'{"shell":"default","columns":80,"rows":24,"container":"other"}')])
async def test_invalid_first_records_do_not_leak(endpoint, caplog, kind, payload):
    service, broker, _, port, _ = endpoint
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    await write_record(writer, kind, payload)
    assert await read_record(reader) == (Type.ERROR, b'{"code":"PROTOCOL_ERROR"}')
    await close_writer(writer)
    await wait_cleanup(service, broker)
    assert 'sensitive-terminal-bytes' not in caplog.text


@pytest.mark.parametrize('kind,payload', [(Type.OPEN, b'{}'), (Type.RESIZE, b'{"columns":0,"rows":24}'), (Type.CLOSE, b'not-empty'), (Type.AUTH, b'replay')])
async def test_invalid_records_after_open_cleanup(endpoint, kind, payload):
    service, broker, _, port, _ = endpoint
    reader, writer = await open_terminal(port)
    await write_record(writer, kind, payload)
    async with asyncio.timeout(3):
        while True:
            record, value = await read_record(reader)
            if record == Type.ERROR:
                assert value == b'{"code":"PROTOCOL_ERROR"}'
                break
    await close_writer(writer)
    await wait_cleanup(service, broker)


async def test_broker_auth_deadline(endpoint):
    service, broker, config, _, root = endpoint
    await broker.stop()
    async def stalled(reader, writer):
        try:
            await reader.read()
        finally:
            await close_writer(writer)
    server = await asyncio.start_unix_server(stalled, broker.socket)
    try:
        config = replace(config, broker_timeout=.01)
        assert not await ready(config)
    finally:
        server.close()
        await server.wait_closed()
