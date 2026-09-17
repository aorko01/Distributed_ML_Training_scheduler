"""Disposable test PTY broker. Never included in production Access image."""
import asyncio
import os
from pathlib import Path
import signal
from fake_broker import FakeBroker


async def main():
    directory = Path('/run/dml-interactive/terminal')
    directory.mkdir(parents=True, exist_ok=True)
    os.chown(directory, 10001, 10001)
    token = Path('/secrets/broker').read_bytes().strip()
    (directory / 'token').write_bytes(token)
    os.chown(directory / 'token', 10001, 10001)
    (directory / 'token').chmod(0o440)
    broker = await FakeBroker(str(directory / 'broker.sock'), token, '/fake-workload').start()
    os.chown(directory / 'broker.sock', 10001, 10001)
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    async def stats():
        while not stop.is_set():
            (directory / 'children').write_text(str(len(broker.children)))
            await asyncio.sleep(.1)
    task = asyncio.create_task(stats())
    try:
        await stop.wait()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await broker.stop()
        (directory / 'children').write_text('0')


asyncio.run(main())
