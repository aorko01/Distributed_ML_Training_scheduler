"""Authenticated runtime-bound Unix broker with real Docker exec PTYs."""

import asyncio
from contextlib import suppress
import hmac
import logging
import os
from pathlib import Path
import secrets
import signal
import socket
import time
import psutil
from Access_Container.interactive_access.protocol import (
    Type,
    ProtocolError,
    read_record,
    write_record,
    json_bytes,
    dimensions,
)


log = logging.getLogger("broker")


class UnsafeSession(Exception):
    pass


class DockerSession:
    def __init__(self, client, container_id, user, workdir, columns, rows):
        self.client, self.container_id = client, container_id
        self.handles = {}
        init_pid = client.api.inspect_container(container_id)["State"]["Pid"]
        self.cgroup = Path("/proc", str(init_pid), "cgroup").read_text()
        if self.cgroup == Path("/proc/self/cgroup").read_text():
            raise UnsafeSession()
        # One terminal per runtime. Record the fixed keepalive's process set
        # before launch; new cgroup processes belong to this exec, including
        # children that double-fork/reparent or create a different session.
        self.baseline = self.members()
        try:
            self.exec_id = client.api.exec_create(
                container_id,
                cmd=["/bin/sh"],
                stdin=True,
                stdout=True,
                stderr=True,
                tty=True,
                privileged=False,
                user=user,
                workdir=workdir,
                environment={"TERM": "xterm", "HOME": "/tmp"},
            )["Id"]
            self.stream = client.api.exec_start(self.exec_id, tty=True, socket=True)
            self.sock = self.stream._sock
            self.sock.settimeout(3)
            client.api.exec_resize(self.exec_id, height=rows, width=columns)
            inspected = client.api.exec_inspect(self.exec_id)
            if not inspected.get("Running") or not inspected.get("Pid"):
                raise UnsafeSession()
            self.pid = inspected["Pid"]
            # Workers run on the Docker host. Reject inability to establish exact
            # process/cgroup identity rather than signalling an unverified host PID.
            container = client.api.inspect_container(container_id)
            init_pid = container["State"]["Pid"]
            self.cgroup = Path("/proc", str(init_pid), "cgroup").read_text()
            if (
                self.cgroup != Path("/proc", str(self.pid), "cgroup").read_text()
                or self.cgroup == Path("/proc/self/cgroup").read_text()
            ):
                raise UnsafeSession()
            self.sid = os.getsid(self.pid)
            if self.sid != self.pid:
                raise UnsafeSession()
            self.capture()

        except BaseException:
            # Launch may have succeeded before a resize/inspect/identity error.
            # Without a verified session handle, stop only this workload.
            with suppress(Exception):
                if hasattr(self, "stream"):
                    self.stream.close()
            with suppress(Exception):
                client.api.stop(container_id, timeout=3)
            for fd in self.handles.values():
                os.close(fd)
            raise

    def members(self):
        result = {}
        for process in psutil.process_iter(["pid"]):
            try:
                if Path("/proc", str(process.pid), "cgroup").read_text() == self.cgroup:
                    result[process.pid] = process.create_time()
            except (FileNotFoundError, ProcessLookupError, psutil.NoSuchProcess):
                continue
        return result

    def capture(self):
        for pid, birth in self.members().items():
            if self.baseline.get(pid) == birth:
                continue
            fd = None
            try:
                process = psutil.Process(pid)
                fd = os.pidfd_open(pid)
                if (
                    process.create_time() != birth
                    or Path("/proc", str(pid), "cgroup").read_text() != self.cgroup
                ):
                    os.close(fd)
                    raise UnsafeSession()
                if pid in self.handles:
                    os.close(fd)
                else:
                    self.handles[pid] = fd
            except (FileNotFoundError, ProcessLookupError, psutil.NoSuchProcess):
                if fd is not None:
                    os.close(fd)

    def read(self):
        try:
            return self.sock.recv(32768)
        except (TimeoutError, socket.timeout):
            return None

    def write(self, data):
        self.sock.sendall(data)

    def resize(self, value):
        self.client.api.exec_resize(
            self.exec_id, height=value["rows"], width=value["columns"]
        )

    def close(self):
        try:
            self.capture()
            self.stream.close()
            for fd in self.handles.values():
                with suppress(ProcessLookupError):
                    signal.pidfd_send_signal(fd, signal.SIGHUP)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                self.capture()
                alive = []
                for pid, fd in self.handles.items():
                    with suppress(psutil.NoSuchProcess):
                        if psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                            alive.append(fd)
                if (
                    not self.client.api.exec_inspect(self.exec_id)["Running"]
                    and not alive
                ):
                    return True
                time.sleep(0.05)
            for fd in self.handles.values():
                with suppress(ProcessLookupError):
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
            time.sleep(0.1)
            if self.client.api.exec_inspect(self.exec_id)["Running"]:
                raise UnsafeSession()
            self.capture()
            for pid in self.handles:
                with suppress(psutil.NoSuchProcess):
                    if psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                        raise UnsafeSession()
            return True
        except Exception:
            # Docker has no exec-kill API. If exit cannot be proved, bounded
            # fallback stops the exact workload and marks the runtime unhealthy.
            self.client.api.stop(self.container_id, timeout=3)
            return False
        finally:
            for fd in self.handles.values():
                os.close(fd)


class Broker:
    def __init__(
        self,
        path,
        token,
        client,
        container_id,
        user,
        workdir,
        authority,
        on_failure=lambda: None,
        session_factory=DockerSession,
    ):
        self.path, self.token, self.client, self.container_id = (
            Path(path),
            token,
            client,
            container_id,
        )
        self.user, self.workdir, self.authority = user, workdir, authority
        self.on_failure, self.session_factory = on_failure, session_factory
        self.busy = False
        self.stopping = False
        self.connections = set()

    def healthy(self):
        if self.stopping or not self.authority():
            return False
        try:
            return self.client.api.inspect_container(self.container_id)["State"][
                "Running"
            ]
        except Exception:
            return False

    async def start(self):
        self.server = await asyncio.start_unix_server(
            self.handle, path=str(self.path), limit=65536
        )
        os.chmod(self.path, 0o660)
        os.chown(self.path, 0, 10001)

    async def stop(self):
        self.stopping = True
        self.server.close()
        await self.server.wait_closed()
        tasks = list(self.connections)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.path.unlink(missing_ok=True)

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.connections.add(task)
        writer.transport.set_write_buffer_limits(high=65536, low=16384)
        session = None
        workspace = None
        output = None
        incoming = None
        reserved = False
        try:
            async with asyncio.timeout(3):
                challenge = secrets.token_bytes(32)
                await write_record(writer, Type.CHALLENGE, challenge)
                kind, payload = await read_record(reader)
                expected = hmac.digest(
                    self.token, b"dml-broker-v1\0" + challenge, "sha256"
                )
                if kind != Type.AUTH or not hmac.compare_digest(payload, expected):
                    raise ProtocolError()
                await write_record(writer, Type.AUTHENTICATED)
            async with asyncio.timeout(5):
                kind, payload = await read_record(reader)
                if not await asyncio.to_thread(self.healthy):
                    await write_record(
                        writer, Type.ERROR, json_bytes({"code": "UNAVAILABLE"})
                    )
                    return
                if kind == Type.PROBE and not payload:
                    await write_record(writer, Type.READY)
                    return
                if kind == Type.HELLO:
                    # Workspace clients have their own state machine.  Do not
                    # reinterpret a terminal OPEN/CLOSE as file operations.
                    if self.busy:
                        await write_record(writer, Type.ERROR, json_bytes({"code": "BUSY"}))
                        return
                    from .workspace_broker import WorkspaceSession
                    from Access_Container.interactive_access.workspace_protocol import metadata

                    self.busy, reserved = True, True
                    workspace = WorkspaceSession(self, reader, writer)
                    try:
                        await workspace.run(metadata(payload))
                    except (EOFError, ConnectionError):
                        log.debug("workspace peer went away container=%.12s", self.container_id)
                        raise
                    except ProtocolError:
                        log.warning("workspace protocol error container=%.12s", self.container_id)
                        raise
                    return
                if kind != Type.OPEN:
                    raise ProtocolError()
                value = dimensions(payload, opening=True)
                if self.busy:
                    await write_record(writer, Type.ERROR, json_bytes({"code": "BUSY"}))
                    return
                self.busy, reserved = True, True
                # Keep ownership of the launch future even after timeout/cancel;
                # its completion must be reaped, never leave a late shell alive.
                launch = asyncio.create_task(
                    asyncio.to_thread(
                        self.session_factory,
                        self.client,
                        self.container_id,
                        self.user,
                        self.workdir,
                        value["columns"],
                        value["rows"],
                    )
                )
                try:
                    session = await asyncio.shield(launch)
                except BaseException as exc:

                    def reap(future):
                        try:
                            late = future.result()
                            asyncio.create_task(asyncio.to_thread(late.close))
                        except BaseException:
                            self.on_failure()

                    launch.add_done_callback(reap)
                    log.error("terminal pty launch failed %s container=%.12s", type(exc).__name__, self.container_id)
                    self.on_failure()
                    raise
                if not self.authority():
                    raise UnsafeSession()
                await write_record(
                    writer,
                    Type.OPENED,
                    json_bytes(
                        {
                            "session_id": secrets.token_hex(16),
                            "protocol": "terminal-stream-v1",
                        }
                    ),
                )

            async def pump():
                while self.authority() and not self.stopping:
                    data = await asyncio.to_thread(session.read)
                    if data is None:
                        continue
                    if not data:
                        return
                    await write_record(writer, Type.STDOUT, data)

            output = asyncio.create_task(pump())
            while self.authority() and not self.stopping:
                incoming = asyncio.create_task(read_record(reader))
                done, _ = await asyncio.wait(
                    [incoming, output], return_when=asyncio.FIRST_COMPLETED
                )
                if output in done:
                    incoming.cancel()
                    with suppress(BaseException):
                        await incoming
                    await output
                    break
                kind, payload = incoming.result()
                if kind == Type.STDIN:
                    await asyncio.to_thread(session.write, payload)
                elif kind == Type.RESIZE:
                    await asyncio.to_thread(session.resize, dimensions(payload))
                elif kind == Type.CLOSE and not payload:
                    break
                else:
                    raise ProtocolError()
            if output:
                output.cancel()
                with suppress(BaseException):
                    await output
                output = None
            closed = await asyncio.to_thread(session.close)
            session = None
            if not closed:
                # Shell/exec teardown could not be proved; the workload was
                # stopped to guarantee no late shell survives. The runtime is
                # unhealthy from here on; the manager will tear it down.
                log.error("terminal UNCLEAN close; workload container stopped container=%.12s", self.container_id)
                self.on_failure()
                await write_record(
                    writer, Type.ERROR, json_bytes({"code": "UNAVAILABLE"})
                )
            else:
                await write_record(
                    writer, Type.EXIT, json_bytes({"code": 0, "reason": "closed"})
                )
        except (ProtocolError, TimeoutError) as exc:
            log.warning("broker handshake failed %s container=%.12s", type(exc).__name__, self.container_id)
            with suppress(Exception):
                await write_record(
                    writer, Type.ERROR, json_bytes({"code": "PROTOCOL_ERROR"})
                )
        except (EOFError, ConnectionError, asyncio.CancelledError):
            pass
        except Exception as exc:
            log.error("broker connection failed %s container=%.12s", type(exc).__name__, self.container_id, exc_info=True)
            self.on_failure()
            with suppress(Exception):
                await write_record(
                    writer, Type.ERROR, json_bytes({"code": "UNAVAILABLE"})
                )
        finally:
            if incoming and not incoming.done():
                incoming.cancel()
                with suppress(BaseException):
                    await incoming
            if output:
                output.cancel()
                with suppress(BaseException):
                    await output
            if workspace:
                with suppress(Exception):
                    await workspace.close()
            if session:
                if not await asyncio.to_thread(session.close):
                    log.error("terminal teardown UNCLEAN close; workload container stopped container=%.12s", self.container_id)
                    self.on_failure()
            if reserved:
                self.busy = False
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()
            self.connections.discard(task)
