import asyncio
import os
import struct
from pathlib import Path
import pytest
from interactive_access.protocol import Parser, Type, encode, ProtocolError, dimensions, json_bytes, MAX_PAYLOAD
from interactive_access.config import Config, read_secret
from interactive_access.session import Capacity


def test_split_and_coalesced():
    records = [(Type.OPEN, json_bytes({'columns': 80, 'rows': 24, 'shell': 'default'})), (Type.STDIN, b'\x00\xff')]
    stream = b''.join(encode(*record) for record in records)
    parser = Parser()
    result = []
    for byte in stream:
        result += parser.feed(bytes([byte]))
        assert len(parser.buffer) <= 65536
    assert result == records
    assert Parser().feed(stream * 4) == records * 4
    parser.eof()


@pytest.mark.parametrize('header', [struct.pack('!BBI', 2, 1, 0), struct.pack('!BBI', 1, 255, 0), struct.pack('!BBI', 1, 1, MAX_PAYLOAD + 1)])
def test_invalid_headers(header):
    with pytest.raises(ProtocolError):
        Parser().feed(header)


def test_incomplete_and_bounded_partial():
    parser = Parser()
    parser.feed(encode(Type.STDIN, b'x' * MAX_PAYLOAD)[:-1])
    assert len(parser.buffer) == 65535
    with pytest.raises(ProtocolError):
        parser.eof()


@pytest.mark.parametrize('value', [{'rows': 1, 'columns': True}, {'rows': 301, 'columns': 80}, {'rows': 0, 'columns': 80}, {'rows': 20, 'columns': 80, 'container': 'other'}])
def test_invalid_dimensions(value):
    with pytest.raises(ProtocolError):
        dimensions(json_bytes(value))


def test_duplicate_keys():
    with pytest.raises(ProtocolError):
        dimensions(b'{"rows":1,"rows":2,"columns":80}')


async def test_capacity_race_and_exact_release():
    capacity = Capacity(1)
    async def contender():
        await asyncio.sleep(0)
        return capacity.acquire()
    results = await asyncio.gather(*(contender() for _ in range(10)))
    assert sum(results) == 1
    assert not capacity.acquire()
    capacity.release()
    assert capacity.active == 0
    with pytest.raises(AssertionError):
        capacity.release()


@pytest.mark.parametrize('changes', [{'host': '0.0.0.0'}, {'socket_path': '/var/run/docker.sock'}, {'token_file': '/etc/token'}, {'capacity': 17}])
def test_config_rejects_unsafe(changes):
    values = dict(runtime_id='test', socket_path='/run/dml-interactive/test/broker.sock', token_file='/run/dml-interactive/test/token')
    values.update(changes)
    with pytest.raises(ValueError):
        Config(**values).validate()


def test_token_permissions_and_defaults(tmp_path):
    path = tmp_path / 'token'
    path.write_bytes(os.urandom(32).hex().encode())
    path.chmod(0o600)
    assert len(read_secret(path)) == 64
    path.chmod(0o644)
    with pytest.raises(ValueError):
        read_secret(path)
    path.chmod(0o600)
    path.write_bytes(b'x' * 64)
    with pytest.raises(ValueError):
        read_secret(path)
    link = tmp_path / 'link'
    link.symlink_to(path)
    with pytest.raises(OSError):
        read_secret(link)


def test_image_source_isolated():
    dockerfile = (Path(__file__).resolve().parents[2] / 'Dockerfile').read_text()
    assert 'USER 10001:10001' in dockerfile
    assert '@sha256:' in dockerfile
    assert 'COPY test' not in dockerfile
    assert all(word not in dockerfile.lower() for word in ('docker.sock', 'openssh', 'tailscale', 'docker-cli', 'password'))
