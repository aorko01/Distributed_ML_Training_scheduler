"""Injectable deadline clock; tests can expire waits without wall-clock sleeps."""
import asyncio
import time


class Clock:
    def now(self):
        return time.monotonic()

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)

    async def wait(self, awaitable, seconds):
        operation = asyncio.ensure_future(awaitable)
        timer = asyncio.create_task(self.sleep(seconds))
        try:
            done, _ = await asyncio.wait((operation, timer), return_when=asyncio.FIRST_COMPLETED)
            if operation in done:
                return operation.result()
            raise TimeoutError()
        finally:
            operation.cancel()
            timer.cancel()
            await asyncio.gather(operation, timer, return_exceptions=True)
