import asyncio
from contextlib import suppress
import time

from starlette.websockets import WebSocketDisconnect

from .management_client import ManagementError
from .schemas import timestamp


class RelayClosed(Exception):
    def __init__(self, code):
        self.code = code


async def relay(websocket, reader, writer, record, management, settings, clock=time.time):
    """Copy bytes between the browser WebSocket and the tailnet TCP stream.

    Returns ``(code, counts, reason)``: the WebSocket close code, byte
    counters, and a short machine-readable close reason (``client-disconnect``,
    ``backend-eof``, ``idle-timeout``, ``lease-expired``, ``version-mismatch``,
    ``renew-denied``, ``oversize-frame``, ``text-frame``, ``transport-error``,
    ``cancelled``). The reason is what the raw code cannot tell you: 1000
    covers clean closes, idle timeouts, and browser navigations alike, while
    4410 covers lease expiry, version changes, and denied renewals.
    """
    activity = time.monotonic()
    lease, deadline = timestamp(record["lease_expires_at"]), timestamp(record["deadline"])
    counts = {"sent": 0, "received": 0}
    reason = "unknown"

    async def upstream():
        nonlocal activity, reason
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                reason = "client-disconnect"
                return
            data = message.get("bytes")
            if data is None:
                reason = "text-frame"
                raise RelayClosed(4403)
            if len(data) > settings.frame_max:
                reason = f"oversize-frame:{len(data)}"
                raise RelayClosed(4403)
            writer.write(data)
            await writer.drain()  # no unbounded producer queue
            counts["sent"] += len(data)
            activity = time.monotonic()

    async def downstream():
        nonlocal activity, reason
        while data := await reader.read(settings.frame_max):
            await websocket.send_bytes(data)
            counts["received"] += len(data)
            activity = time.monotonic()
        reason = "backend-eof"

    async def authorization():
        nonlocal lease, reason
        next_renewal = time.monotonic() + settings.renewal_interval
        while True:
            remaining = min(lease, deadline) - clock()
            idle = settings.idle_timeout - (time.monotonic() - activity)
            if remaining <= 0:
                reason = "lease-expired"
                raise RelayClosed(4410)
            if idle <= 0:
                reason = "idle-timeout"
                raise RelayClosed(1000)
            delay = min(remaining, idle, max(0, next_renewal - time.monotonic()), 1)
            await asyncio.sleep(delay)
            if time.monotonic() < next_renewal:
                continue
            try:
                # A hung renewal cannot run beyond the existing lease.
                async with asyncio.timeout(max(0.001, min(2, lease - clock(), deadline - clock()))):
                    renewed = await management.renew(record["session_id"])
                if renewed["version"] != record["version"]:
                    reason = f"version-mismatch:{renewed['version']}"
                    raise RelayClosed(4410)
                lease = timestamp(renewed["lease_expires_at"])
            except ManagementError as error:
                if error.status in (401, 403, 404, 409, 410):
                    reason = f"renew-denied:{error.status}"
                    raise RelayClosed(4410) from None
            except (TimeoutError, OSError):
                pass
            next_renewal = time.monotonic() + settings.renewal_interval

    tasks = [asyncio.create_task(fn()) for fn in (upstream, downstream, authorization)]
    code = 1000
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                task.result()
            except RelayClosed as error:
                code = error.code
            except (OSError, WebSocketDisconnect, RuntimeError) as error:
                code = 1011
                reason = f"transport-error:{type(error).__name__}"
            except asyncio.CancelledError:
                reason = "cancelled"
                raise
    except asyncio.CancelledError:
        reason = "cancelled"
        raise
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return code, counts, reason
