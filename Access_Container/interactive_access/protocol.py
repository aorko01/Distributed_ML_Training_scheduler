import asyncio
import json
import struct
from enum import IntEnum

HEADER = struct.Struct('!BBI')
MAX_PAYLOAD = 65530
JSON_LIMIT = 1024


class Type(IntEnum):
    OPEN = 1
    STDIN = 2
    RESIZE = 3
    CLOSE = 4
    OPENED = 5
    STDOUT = 6
    EXIT = 7
    ERROR = 8
    CHALLENGE = 16
    AUTH = 17
    AUTHENTICATED = 18
    PROBE = 19
    READY = 20


class ProtocolError(Exception):
    pass


def encode(kind, payload=b''):
    if not isinstance(kind, Type) or len(payload) > MAX_PAYLOAD:
        raise ProtocolError()
    return HEADER.pack(1, kind, len(payload)) + payload


def json_bytes(value):
    result = json.dumps(value, separators=(',', ':')).encode()
    if len(result) > JSON_LIMIT:
        raise ProtocolError()
    return result


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError()
        result[key] = value
    return result


def parse_json(payload):
    if len(payload) > JSON_LIMIT:
        raise ProtocolError()
    try:
        result = json.loads(payload, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError() from None
    if not isinstance(result, dict):
        raise ProtocolError()
    return result


def dimensions(payload, opening=False):
    value = parse_json(payload)
    if set(value) != ({'columns', 'rows', 'shell'} if opening else {'columns', 'rows'}):
        raise ProtocolError()
    if opening and value['shell'] != 'default':
        raise ProtocolError()
    for key, maximum in (('columns', 500), ('rows', 300)):
        if type(value[key]) is not int or not 1 <= value[key] <= maximum:
            raise ProtocolError()
    return value


def decode_header(header):
    version, kind, length = HEADER.unpack(header)
    if version != 1 or length > MAX_PAYLOAD:
        raise ProtocolError()
    try:
        kind = Type(kind)
    except ValueError:
        raise ProtocolError() from None
    return kind, length


class Parser:
    """One bounded partial record; arbitrary split/coalesced stream input."""
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        records = []
        view = memoryview(data)
        while view:
            target = HEADER.size
            if len(self.buffer) >= HEADER.size:
                _, length = decode_header(self.buffer[:HEADER.size])
                target += length
            take = min(target - len(self.buffer), len(view))
            self.buffer.extend(view[:take])
            view = view[take:]
            if len(self.buffer) >= HEADER.size:
                kind, length = decode_header(self.buffer[:HEADER.size])
                if len(self.buffer) == HEADER.size + length:
                    records.append((kind, bytes(self.buffer[HEADER.size:])))
                    self.buffer.clear()
        return records

    def eof(self):
        if self.buffer:
            raise ProtocolError()


async def read_record(reader):
    try:
        header = await reader.readexactly(HEADER.size)
    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            raise EOFError() from None
        raise ProtocolError() from None
    kind, length = decode_header(header)
    try:
        return kind, await reader.readexactly(length)
    except asyncio.IncompleteReadError:
        raise ProtocolError() from None


async def write_record(writer, kind, payload=b'', timeout=5):
    writer.write(encode(kind, payload))
    await asyncio.wait_for(writer.drain(), timeout)
