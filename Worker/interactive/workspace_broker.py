"""workspace-stream-v1 endpoint behind the authenticated Unix broker."""
import asyncio
from contextlib import suppress
import hashlib

from Access_Container.interactive_access.protocol import Type, ProtocolError, read_record, write_record
from Access_Container.interactive_access.workspace_protocol import (
    CONTENT_CHUNK_LIMIT, TEXT_FILE_LIMIT, metadata, metadata_bytes, pack_chunk,
    path, request_id, require_exact, unpack_chunk,
)
from .broker import DockerSession, UnsafeSession
from .file_service import FileService, FileServiceError


SAFE_OPERATIONS = {"list", "stat", "read", "create_file", "mkdir", "write", "rename", "delete"}


class WorkspaceSession:
    # Serial request model: the broker serves one file operation at a time.
    # The browser client serializes mutations and caps concurrent reads, so
    # the session enforces the same bound rather than advertising task
    # concurrency it does not implement.
    def __init__(self, broker, reader, writer):
        self.broker, self.reader, self.writer = broker, reader, writer
        self.files = FileService(broker.client, broker.container_id, broker.user, broker.workdir)
        self.pty = None
        self.output = None
        self.pending = None
        self.read_only = False
        self.send_lock = asyncio.Lock()
        self.closed = False
        self.exited = False

    async def send(self, kind, value=b""):
        async with self.send_lock:
            if self.closed:
                raise ConnectionError("workspace session closed")
            await write_record(self.writer, kind, value)

    async def run(self, hello):
        require_exact(hello, {"protocol"})
        if hello["protocol"] != "workspace-stream-v1" or not self.broker.healthy():
            raise ProtocolError()
        await self.send(Type.WORKSPACE_READY, metadata_bytes({
            "protocol": "workspace-stream-v1", "root": self.broker.workdir,
            "capabilities": ["files", "pty"], "text_file_limit": TEXT_FILE_LIMIT,
            "chunk_limit": CONTENT_CHUNK_LIMIT,
        }))
        while self.broker.authority() and not self.broker.stopping:
            kind, payload = await read_record(self.reader)
            if kind == Type.FILE_REQUEST:
                await self.file_request(metadata(payload))
            elif kind == Type.FILE_CHUNK:
                await self.file_chunk(unpack_chunk(payload))
            elif kind == Type.FILE_END:
                await self.file_end(metadata(payload))
            elif kind == Type.CANCEL:
                await self.cancel(metadata(payload))
            elif kind == Type.PTY_OPEN:
                await self.pty_open(metadata(payload))
            elif kind == Type.PTY_STDIN:
                if not self.pty or self.read_only:
                    raise ProtocolError()
                await asyncio.to_thread(self.pty.write, payload)
            elif kind == Type.PTY_RESIZE:
                if not self.pty:
                    raise ProtocolError()
                value = metadata(payload)
                require_exact(value, {"columns", "rows"})
                if type(value["columns"]) is not int or type(value["rows"]) is not int or not 1 <= value["columns"] <= 500 or not 1 <= value["rows"] <= 300:
                    raise ProtocolError()
                await asyncio.to_thread(self.pty.resize, value)
            elif kind == Type.PTY_CLOSE and not payload:
                await self.close_pty()
            elif kind == Type.CLOSE and not payload:
                return
            else:
                raise ProtocolError()

    async def result(self, identifier, **value):
        await self.send(Type.FILE_RESULT, metadata_bytes({"id": identifier, **value}))

    async def file_request(self, value):
        required = {"id", "operation", "path"}
        if not required <= set(value) or set(value) - {"id", "operation", "path", "target", "expected_version", "cursor", "size"}:
            raise ProtocolError()
        identifier = request_id(value["id"])
        operation = value["operation"]
        if operation not in SAFE_OPERATIONS:
            raise ProtocolError()
        if value["path"] == "" and operation == "list":
            pass
        else:
            path(value["path"])
        if operation == "rename":
            path(value.get("target"))
        if operation == "write":
            if self.read_only or self.pending is not None or type(value.get("size")) is not int or not 0 <= value["size"] <= TEXT_FILE_LIMIT:
                raise ProtocolError()
            self.pending = {"id": identifier, "request": value, "sequence": 0, "data": bytearray()}
            await self.result(identifier, state="receiving")
            return
        if self.read_only and operation in {"create_file", "mkdir", "rename", "delete"}:
            await self.result(identifier, error="READ_ONLY")
            return
        await self.perform(identifier, operation, value)

    async def perform(self, identifier, operation, value):
        args = {key: value[key] for key in ("path", "target", "expected_version", "cursor", "content") if key in value}
        try:
            result = await asyncio.to_thread(self.files.call, operation, **args)
        except FileServiceError as exc:
            await self.result(identifier, error=exc.code)
            return
        if operation != "read":
            await self.result(identifier, state="ok", **result)
            return
        content = result.pop("content").encode("utf-8")
        await self.result(identifier, state="streaming", **result)
        for sequence, start in enumerate(range(0, len(content), CONTENT_CHUNK_LIMIT)):
            await self.send(Type.FILE_CHUNK, pack_chunk(identifier, sequence, content[start:start + CONTENT_CHUNK_LIMIT]))
        await self.send(Type.FILE_END, metadata_bytes({"id": identifier, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}))

    async def file_chunk(self, value):
        identifier, sequence, data = value
        if not self.pending or identifier != self.pending["id"] or sequence != self.pending["sequence"]:
            raise ProtocolError()
        pending = self.pending
        if len(pending["data"]) + len(data) > pending["request"]["size"]:
            raise ProtocolError()
        pending["data"].extend(data)
        pending["sequence"] += 1

    async def file_end(self, value):
        require_exact(value, {"id", "size", "sha256"})
        identifier = request_id(value["id"])
        pending = self.pending
        if not pending or identifier != pending["id"] or type(value["size"]) is not int or value["size"] != len(pending["data"]) or not isinstance(value["sha256"], str):
            raise ProtocolError()
        data = bytes(pending["data"])
        self.pending = None
        if hashlib.sha256(data).hexdigest() != value["sha256"]:
            raise ProtocolError()
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            await self.result(identifier, error="UNSUPPORTED_FILE")
            return
        request = pending["request"]
        request["content"] = content
        await self.perform(identifier, "write", request)

    async def cancel(self, value):
        require_exact(value, {"id"})
        identifier = request_id(value["id"])
        if self.pending and self.pending["id"] == identifier:
            self.pending = None
            await self.result(identifier, error="CANCELLED")
        else:
            await self.result(identifier, error="NOT_FOUND")

    async def pty_open(self, value):
        require_exact(value, {"columns", "rows", "shell"})
        if self.read_only or self.pty or value["shell"] != "default" or type(value["columns"]) is not int or type(value["rows"]) is not int:
            raise ProtocolError()
        try:
            self.pty = await asyncio.to_thread(DockerSession, self.broker.client, self.broker.container_id, self.broker.user, self.broker.workdir, value["columns"], value["rows"])
        except Exception:
            await self.send(Type.ERROR, metadata_bytes({"code": "UNAVAILABLE"}))
            return
        await self.send(Type.PTY_OPENED, metadata_bytes({"protocol": "workspace-stream-v1"}))
        self.output = asyncio.create_task(self.pump_pty())

    async def pump_pty(self):
        try:
            while self.pty and self.broker.authority() and not self.broker.stopping:
                data = await asyncio.to_thread(self.pty.read)
                if data is None:
                    continue
                if not data:
                    break
                await self.send(Type.PTY_STDOUT, data)
        finally:
            await self.close_pty()

    async def close_pty(self):
        if self.output and self.output is not asyncio.current_task():
            self.output.cancel()
            with suppress(BaseException):
                await self.output
        self.output = None
        session, self.pty = self.pty, None
        if session is None or self.exited:
            return
        closed = await asyncio.to_thread(session.close)
        self.exited = True
        with suppress(ConnectionError):
            await self.send(Type.PTY_EXIT, metadata_bytes({"code": 0 if closed else 1, "reason": "closed" if closed else "unavailable"}))

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.pending = None
        await self.close_pty()
