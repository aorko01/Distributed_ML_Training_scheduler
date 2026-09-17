import asyncio
import logging
import signal
from .clock import Clock
from .config import Config
from .broker_client import ready, close_writer
from .session import Capacity, run_session


class Service:
    def __init__(self, config, clock=None):
        self.clock = clock or Clock()
        config.validate()
        self.config = config
        self.capacity = Capacity(config.capacity)
        self.tasks = set()
        self.servers = []
        self.terminal = None
        self.monitor = None
        self.stopping = False
        self.session_tasks = set()

    def accept(self, reader, writer):
        task = asyncio.create_task(run_session(reader, writer, self.config, self.capacity, self.clock))
        self.tasks.add(task)
        self.session_tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(self.session_tasks.discard)

    async def health(self, reader, writer):
        try:
            async with asyncio.timeout(3):
                request = await reader.readuntil(b'\r\n\r\n')
            first = request.split(b'\r\n', 1)[0]
            if first == b'GET /health/live HTTP/1.1':
                status = 200
            elif first == b'GET /health/ready HTTP/1.1':
                status = 200 if await ready(self.config, self.clock) else 503
            else:
                status = 404
            body = b'{"status":"ok"}' if status == 200 else b'{"status":"unavailable"}'
            writer.write(f'HTTP/1.1 {status} Status\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode() + body)
            async with asyncio.timeout(self.config.write_timeout):
                await writer.drain()
        except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            await close_writer(writer)

    def accept_health(self, reader, writer):
        task = asyncio.create_task(self.health(reader, writer))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def update_readiness(self):
        healthy = await ready(self.config, self.clock)
        if self.stopping:
            return
        if healthy and self.terminal is None:
            self.terminal = await asyncio.start_server(self.accept, self.config.host, self.config.port, limit=65536)
            self.servers.append(self.terminal)
        elif not healthy and self.terminal is not None:
            previous = self.terminal
            previous.close()
            self.terminal = None
            tasks = list(self.session_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await previous.wait_closed()
            self.servers.remove(previous)

    async def watch_readiness(self):
        while True:
            await self.clock.sleep(1)
            await self.update_readiness()

    async def start(self):
        await self.update_readiness()
        self.servers.append(await asyncio.start_server(self.accept_health, self.config.host, self.config.health_port, limit=4096))
        self.monitor = asyncio.create_task(self.watch_readiness())
        return self

    async def stop(self):
        self.stopping = True
        if self.monitor:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        for server in self.servers:
            server.close()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
        for server in self.servers:
            await server.wait_closed()


async def main():
    service = await Service(Config.from_env()).start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        await service.stop()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
