import asyncio
from contextlib import asynccontextmanager, suppress
import json
import logging
from pathlib import Path
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException, WebSocket
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from .auth import InvalidTicket, verify
from .config import Settings
from .management_client import ManagementClient, ManagementError
from .relay import relay
from .schemas import Authenticate, destination, timestamp
from .sessions import Capacity, CapacityError
from .tailnet_dialer import TailnetDialer
from .tailscale_api import TailscaleAPI

logger = logging.getLogger(__name__)
# Uvicorn only installs handlers on its own loggers; without this, records
# from this module propagate to a handler-less root and INFO outcomes
# (including every connection session=/outcome= line) are silently dropped.
logging.basicConfig(level=logging.INFO)


def create_app(settings=None, management=None, dialer=None, local=None, background=True):
    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings or Settings.from_env()
        configured = app.state.settings
        app.state.management = management or ManagementClient(configured)
        app.state.dialer = dialer or TailnetDialer(configured.socks_host, configured.socks_port)
        app.state.local = local or TailscaleAPI(configured.socket)
        app.state.capacity = Capacity(configured)
        async def probes():
            while True:
                try:
                    targets = await app.state.management.request("GET", "gateways/" + configured.gateway_id + "/probe-targets")
                    # Bound probe concurrency by sequential dialing and API cap.
                    for target in targets[:1000]:
                        writer = None
                        try:
                            ip, port = destination(target)
                            _, writer = await app.state.dialer.dial(ip, port)
                            success = True
                        except (OSError, ValueError, TimeoutError):
                            success = False
                        finally:
                            if writer:
                                writer.close()
                                with suppress(OSError, TimeoutError):
                                    async with asyncio.timeout(1):
                                        await writer.wait_closed()
                        await app.state.management.request("POST", "resources/" + target["resource_id"] + "/probe-result",
                            {key: target[key] for key in ("generation", "version", "probe_id")} | {"success": success})
                except ManagementError:
                    pass
                await asyncio.sleep(configured.probe_interval)
        task = asyncio.create_task(probes()) if background else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await app.state.capacity.shutdown()
            await app.state.management.close()
            await app.state.local.close()

    app = FastAPI(title="Interactive Gateway", lifespan=lifespan)
    @app.get("/health/live")
    async def live():
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready():
        try:
            async with asyncio.timeout(4):
                configured = app.state.settings
                state = json.loads(Path(configured.enrollment_file).read_text())
                status = await app.state.management.request("GET", "enrollments/" + state["enrollment_id"])
                local_status = await app.state.local.status()
                if (status["state"] != "CONFIRMED" or not status["online"]
                        or timestamp(status["observed_at"]) + 15 <= time.time()
                        or status["identity"] != configured.gateway_id or status["generation"] != configured.generation
                        or local_status.get("BackendState") != "Running" or not await app.state.management.ready() or not await app.state.dialer.ready()):
                    raise ValueError()
                return {"status": "ready"}
        except Exception:
            raise HTTPException(503, "unavailable") from None

    @app.websocket("/v1/connect/{resource_id}/{service}")
    async def connect(websocket: WebSocket, resource_id: str, service: str):
        configured = app.state.settings
        origin = websocket.headers.get("origin")
        started = time.monotonic()
        if websocket.query_params or (origin is None and not configured.allow_cli) or (origin is not None and origin not in configured.origins):
            # This pre-authentication reject carries no session, ticket or key
            # material; log the outcome like every other close so a silent
            # 4403 (origin/query mismatch) stays diagnosable from the log.
            reason = "query" if websocket.query_params else "origin"
            await websocket.close(code=4403)
            logger.info("connection session=None outcome=4403 reason=%s duration=%.3f sent=0 received=0",
                        reason, time.monotonic() - started)
            return
        capacity = app.state.capacity
        auth_reserved = reserved = False
        record = writer = None
        code, counts = 1000, {"sent": 0, "received": 0}
        try:
            await capacity.begin_auth()
            auth_reserved = True
            await websocket.accept()
            async with asyncio.timeout(configured.auth_timeout):
                message = await websocket.receive()
            text = message.get("text")
            if text is None or len(text.encode()) > 9216:
                raise InvalidTicket()
            authentication = Authenticate.model_validate_json(text)
            if authentication.type != "authenticate":
                raise InvalidTicket()
            claims = verify(authentication.ticket, configured, resource_id, service)
            record = await app.state.management.claim(authentication.ticket, str(uuid4()))
            if (record["resource_id"] != resource_id or record["service"] != service or record["user"] != claims["sub"]
                    or record["generation"] != claims["generation"] or record["protocol"] != "tcp-stream-v1"):
                raise InvalidTicket()
            ip, port = destination(record)
            await capacity.reserve(record["session_id"], claims["sub"], asyncio.current_task())
            reserved = True
            await capacity.end_auth()
            auth_reserved = False
            remaining = min(timestamp(record["lease_expires_at"]), timestamp(record["deadline"])) - time.time()
            if remaining <= 0:
                raise ManagementError(410)
            async with asyncio.timeout(min(3, remaining)):
                reader, writer = await app.state.dialer.dial(ip, port)
            await websocket.send_json({"type": "ready", "protocol": "tcp-stream-v1"})
            code, counts = await relay(websocket, reader, writer, record, app.state.management, configured)
        except (InvalidTicket, ValidationError, ValueError, KeyError, TypeError):
            code = 4401
        except TimeoutError:
            code = 1011 if record else 4408
        except CapacityError:
            code = 1013
        except ManagementError as error:
            code = 4410 if error.status in (404, 409, 410) else 4403 if error.status in (401, 403) else 1013
        except (OSError, RuntimeError, WebSocketDisconnect):
            code = 1011
        except asyncio.CancelledError:
            code = 1001
            raise
        finally:
            if writer:
                writer.close()
                with suppress(OSError, TimeoutError):
                    async with asyncio.timeout(1):
                        await writer.wait_closed()
            if auth_reserved:
                await capacity.end_auth()
            if record:
                if reserved:
                    await capacity.release(record["session_id"])
                # A duplicate reservation belongs to another live relay and
                # must not release that relay's management session.
                if reserved or record["session_id"] not in capacity.active:
                    with suppress(ManagementError, TimeoutError):
                        async with asyncio.timeout(2):
                            await app.state.management.release(record["session_id"])
            with suppress(RuntimeError, WebSocketDisconnect):
                await websocket.close(code=code)
            logger.info("connection session=%s outcome=%s duration=%.3f sent=%s received=%s", record.get("session_id") if record else None, code,
                        time.monotonic() - started, counts["sent"], counts["received"])
    return app


app = create_app()
