"""Workspace relay disconnect tracing: one summary per session, no payload bytes."""
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import patch

from interactive_access.clock import Clock
from interactive_access.protocol import Type, encode, decode_header, HEADER
from interactive_access.session import run_workspace_session


def hello_payload():
    return b'{"protocol":"workspace-stream-v1"}'


def ready_payload():
    return b'{"protocol":"workspace-stream-v1","root":"/workspace","capabilities":["files","pty"],"text_file_limit":1,"chunk_limit":1}'


class FakeWriter:
    def __init__(self):
        self.records = []

    def write(self, data):
        self.records.append(bytes(data))

    async def drain(self):
        return None

    def close(self):
        pass

    async def wait_closed(self):
        return None

    def kinds(self):
        out = []
        for data in self.records:
            kind, _ = decode_header(data[:HEADER.size])
            out.append(kind)
        return out


def feed(reader, *records):
    for kind, payload in records:
        reader.feed_data(encode(kind, payload))


def config():
    return SimpleNamespace(runtime_id='test-runtime', open_timeout=5, broker_timeout=3, write_timeout=5)


async def test_clean_close_logs_summary_with_counts(caplog):
    client_reader = asyncio.StreamReader()
    client_writer = FakeWriter()
    broker_reader = asyncio.StreamReader()
    broker_writer = FakeWriter()
    feed(broker_reader, (Type.WORKSPACE_READY, ready_payload()))

    async def close_after_ready():
        # Let the READY forward land first so byte counts are deterministic;
        # production has no such race (both legs run until one side closes).
        for _ in range(1000):
            if Type.WORKSPACE_READY in client_writer.kinds():
                break
            await asyncio.sleep(0.001)
        client_reader.feed_data(encode(Type.CLOSE, b''))

    async def fake_connect(config, clock=None):
        return broker_reader, broker_writer

    with caplog.at_level(logging.INFO, logger='interactive_access'):
        with patch('interactive_access.session.connect', side_effect=fake_connect):
            closer = asyncio.create_task(close_after_ready())
            try:
                outcome, detail, counts = await run_workspace_session(
                    client_reader, client_writer, config(), Clock(), hello_payload(), 'sess-1')
            finally:
                closer.cancel()

    assert outcome == 'WORKSPACE_EXIT'
    assert Type.WORKSPACE_READY in client_writer.kinds()
    # The broker leg received the forwarded CLOSE.
    assert Type.CLOSE in broker_writer.kinds()
    assert counts['input'] >= 0 and counts['output'] > 0
    assert 'sess-1' in caplog.text and 'outcome=WORKSPACE_EXIT' in caplog.text


async def test_unknown_client_record_logs_reject_and_outcome(caplog):
    client_reader = asyncio.StreamReader()
    client_writer = FakeWriter()
    broker_reader = asyncio.StreamReader()
    broker_writer = FakeWriter()
    feed(broker_reader, (Type.WORKSPACE_READY, ready_payload()))
    # STDOUT is a server-only record: the client must never send it.
    feed(client_reader, (Type.STDOUT, b'x'))

    async def fake_connect(config, clock=None):
        return broker_reader, broker_writer

    with caplog.at_level(logging.INFO, logger='interactive_access'):
        with patch('interactive_access.session.connect', side_effect=fake_connect):
            outcome, detail, counts = await run_workspace_session(
                client_reader, client_writer, config(), Clock(), hello_payload(), 'sess-2')

    assert outcome == 'WORKSPACE_PROTOCOL_ERROR'
    assert Type.ERROR in client_writer.kinds()
    assert 'workspace_reject' in caplog.text and 'kind=STDOUT' in caplog.text
    assert 'outcome=WORKSPACE_PROTOCOL_ERROR' in caplog.text


async def test_bad_hello_protocol_is_logged(caplog):
    client_reader = asyncio.StreamReader()
    client_writer = FakeWriter()

    async def no_connect(config, clock=None):
        raise AssertionError('broker must not be dialled for a bad HELLO')

    with caplog.at_level(logging.INFO, logger='interactive_access'):
        with patch('interactive_access.session.connect', side_effect=no_connect):
            outcome, detail, counts = await run_workspace_session(
                client_reader, client_writer, config(), Clock(), b'{"protocol":"nope"}', 'sess-3')

    assert outcome == 'WORKSPACE_PROTOCOL_ERROR'
    assert 'bad HELLO' in detail
    assert Type.ERROR in client_writer.kinds()


async def test_broker_eof_without_client_close_is_backend_eof(caplog):
    client_reader = asyncio.StreamReader()
    client_writer = FakeWriter()
    broker_reader = asyncio.StreamReader()
    broker_writer = FakeWriter()
    feed(broker_reader, (Type.WORKSPACE_READY, ready_payload()))
    broker_reader.feed_eof()

    async def fake_connect(config, clock=None):
        return broker_reader, broker_writer

    with caplog.at_level(logging.INFO, logger='interactive_access'):
        with patch('interactive_access.session.connect', side_effect=fake_connect):
            outcome, detail, counts = await asyncio.wait_for(
                run_workspace_session(
                    client_reader, client_writer, config(), Clock(), hello_payload(), 'sess-4'),
                timeout=5)

    assert outcome == 'WORKSPACE_BACKEND_EOF'
    assert 'outcome=WORKSPACE_BACKEND_EOF' in caplog.text
