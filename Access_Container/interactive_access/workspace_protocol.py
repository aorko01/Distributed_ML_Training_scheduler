"""Strict metadata helpers for the workspace-stream-v1 application protocol.

The transport framing lives in :mod:`protocol`.  Keeping these checks separate
prevents a workspace client from changing terminal-stream-v1 semantics.
"""
import hashlib
import json
import struct

from .protocol import MAX_PAYLOAD, ProtocolError

METADATA_LIMIT = 16 * 1024
CONTENT_CHUNK_LIMIT = 32 * 1024
TEXT_FILE_LIMIT = 2 * 1024 * 1024
DIRECTORY_PAGE_LIMIT = 200


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ProtocolError()
        value[key] = item
    return value


def metadata(payload):
    """Parse one bounded object and reject duplicate members and non-UTF8."""
    if len(payload) > METADATA_LIMIT:
        raise ProtocolError()
    try:
        value = json.loads(payload, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError() from None
    if not isinstance(value, dict):
        raise ProtocolError()
    return value


def metadata_bytes(value):
    try:
        encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ProtocolError() from None
    if len(encoded) > METADATA_LIMIT:
        raise ProtocolError()
    return encoded


def request_id(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ProtocolError()
    if any(not (char.isascii() and (char.isalnum() or char in "_-")) for char in value):
        raise ProtocolError()
    return value


def path(value):
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 1024:
        raise ProtocolError()
    if value.startswith("/") or "\\" in value or "\x00" in value:
        raise ProtocolError()
    parts = value.split("/")
    if len(parts) > 64 or any(part in ("", ".", "..") for part in parts):
        raise ProtocolError()
    return value


def content_digest(data):
    return hashlib.sha256(data).hexdigest()


def require_exact(value, keys):
    if set(value) != set(keys):
        raise ProtocolError()
    return value


def valid_chunk(data):
    if len(data) > CONTENT_CHUNK_LIMIT or len(data) > MAX_PAYLOAD:
        raise ProtocolError()
    return data


def pack_chunk(identifier, sequence, data):
    """Encode request id + ordered sequence + content without JSON/base64."""
    identifier = request_id(identifier).encode("ascii")
    if type(sequence) is not int or not 0 <= sequence <= 2**32 - 1:
        raise ProtocolError()
    valid_chunk(data)
    return bytes((len(identifier),)) + identifier + struct.pack("!I", sequence) + data


def unpack_chunk(payload):
    if len(payload) < 6:
        raise ProtocolError()
    length = payload[0]
    if not 1 <= length <= 64 or len(payload) < 1 + length + 4:
        raise ProtocolError()
    try:
        identifier = request_id(payload[1:1 + length].decode("ascii"))
    except UnicodeDecodeError:
        raise ProtocolError() from None
    sequence = struct.unpack("!I", payload[1 + length:5 + length])[0]
    data = payload[5 + length:]
    valid_chunk(data)
    return identifier, sequence, data
