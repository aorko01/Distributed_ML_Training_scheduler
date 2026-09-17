"""TEST ONLY: local PTY substitute for a future runtime-bound Worker broker."""
import asyncio
import fcntl
import hmac
import os
import pty
import signal
import struct
import subprocess
import termios
from pathlib import Path
from interactive_access.protocol import Type, ProtocolError, read_record, write_record, dimensions, json_bytes
from interactive_access.broker_client import close_writer


class FakeBroker:
    def __init__(self, socket, token, cwd, environment=None):
        self.socket = socket
        self.token = token
        self.cwd = cwd
        self.environment = environment or {'PATH': '/usr/bin:/bin', 'TERM': 'xterm-256color', 'FAKE_WORKLOAD': 'yes'}
        self.children = set()
        self.tasks = set()
        self.server = None

    async def start(self):
        self.server = await asyncio.start_unix_server(self.accept, self.socket, limit=65536)
        os.chmod(self.socket, 0o660)
        return self

    def accept(self, reader, writer):
        task = asyncio.create_task(self.handle(reader, writer))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def stop(self):
        self.server.close()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.server.wait_closed()
        Path(self.socket).unlink(missing_ok=True)

    async def handle(self, reader, writer):
        master = slave = None
        child = None
        tasks = []
        try:
            async with asyncio.timeout(3):
                challenge = os.urandom(32)
                await write_record(writer, Type.CHALLENGE, challenge)
                kind, proof = await read_record(reader)
                expected = hmac.digest(self.token, b'dml-broker-v1\0' + challenge, 'sha256')
                if kind != Type.AUTH or not hmac.compare_digest(proof, expected):
                    raise ProtocolError()
                await write_record(writer, Type.AUTHENTICATED)
                kind, payload = await read_record(reader)
                if kind == Type.PROBE and not payload:
                    await write_record(writer, Type.READY)
                    return
                if kind != Type.OPEN:
                    raise ProtocolError()
                size = dimensions(payload, True)
            master, slave = pty.openpty()
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', size['rows'], size['columns'], 0, 0))
            def child_setup():
                os.setsid()
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)
            child = subprocess.Popen(['/bin/sh', '-i'], stdin=slave, stdout=slave, stderr=slave,
                                     cwd=self.cwd, env=self.environment, preexec_fn=child_setup)
            self.children.add(child)
            os.close(slave)
            slave = None
            os.set_blocking(master, False)
            await write_record(writer, Type.OPENED, json_bytes({'session_id': 'fake'}))
            loop = asyncio.get_running_loop()

            async def wait_fd(writing=False):
                future = loop.create_future()
                callback = lambda: None if future.done() else future.set_result(None)
                (loop.add_writer if writing else loop.add_reader)(master, callback)
                try:
                    await future
                finally:
                    (loop.remove_writer if writing else loop.remove_reader)(master)

            async def output():
                while True:
                    try:
                        chunk = os.read(master, 16384)
                        if not chunk:
                            break
                    except BlockingIOError:
                        await wait_fd()
                        continue
                    except OSError:
                        break
                    await write_record(writer, Type.STDOUT, chunk)
                code = await asyncio.to_thread(child.wait)
                await write_record(writer, Type.EXIT, json_bytes({'code': code, 'reason': 'exited'}))

            async def inputs():
                while True:
                    kind, payload = await read_record(reader)
                    if kind == Type.CLOSE and not payload:
                        return
                    if kind == Type.RESIZE:
                        size = dimensions(payload)
                        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', size['rows'], size['columns'], 0, 0))
                    elif kind == Type.STDIN:
                        remaining = memoryview(payload)
                        while remaining:
                            try:
                                remaining = remaining[os.write(master, remaining):]
                            except BlockingIOError:
                                await wait_fd(True)
                    else:
                        raise ProtocolError()
            tasks = [asyncio.create_task(inputs()), asyncio.create_task(output())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (EOFError, OSError, ProtocolError, TimeoutError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if child:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await asyncio.to_thread(child.wait)
                self.children.discard(child)
            for descriptor in (master, slave):
                if descriptor is not None:
                    os.close(descriptor)
            await close_writer(writer)
