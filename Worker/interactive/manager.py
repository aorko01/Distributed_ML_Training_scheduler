import asyncio
from contextlib import suppress
import logging
import os
from pathlib import Path
import secrets
import shutil
import threading
import time
from Access_Container.interactive_access.protocol import (
    Type,
    json_bytes,
    read_record,
    write_record,
)
from .broker import Broker
from .docker_ops import RuntimeFailure
from .endpoint import Endpoint
from .failure_diagnostics import (
    collect_failure_diagnostics,
    dump_dir,
    short_detail,
)

logger = logging.getLogger("managed_worker")

#: Default interactive session cap (10 minutes). Overridable via
#: ``INTERACTIVE_MAX_DURATION_SECONDS`` in the Worker ``.env``. Read on every
#: check (not cached at import) so a lowered value applies to running
#: runtimes without a Worker restart. ``0`` disables the local cap (the
#: Scheduler ``INTERACTIVE_LIFETIME_SECONDS`` deadline still applies).
DEFAULT_MAX_DURATION_SECONDS = 600


def max_duration_seconds() -> int:
    try:
        value = int(os.getenv("INTERACTIVE_MAX_DURATION_SECONDS", str(DEFAULT_MAX_DURATION_SECONDS)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_DURATION_SECONDS
    return value if value > 0 else 0


def time_up_detail(limit_seconds: int) -> str:
    minutes = max(1, round(limit_seconds / 60))
    return (
        "Interactive session time up after %d minute(s); "
        "containers stopped." % minutes
    )[:256]


def notify_time_up(workload_container, limit_seconds: int) -> None:
    """Best-effort wall broadcast so live PTY users see why they disconnect.

    Killing the containers alone drops the connection silently; a short
    ``wall`` message gives the user an explicit time-up notice first.
    Never raises.
    """
    if workload_container is None:
        return
    try:
        message = "Interactive session time up after %d minute(s); closing connection." % max(
            1, round(limit_seconds / 60)
        )
        workload_container.exec_run(
            ["sh", "-c", "echo %s | wall 2>/dev/null || true" % repr(message)],
            demux=False,
        )
    except Exception:
        pass


class Manager:
    def __init__(self, coordinator, api, ops):
        self.coordinator, self.api, self.ops = coordinator, api, ops
        self.health = {}
        self.cancel = threading.Event()
        self.broker = None
        self.endpoint = None
        self.failed = False

    def stop(self):
        self.cancel.set()

    def authority(self, assignment_id):
        return (
            not self.cancel.is_set()
            and not self.failed
            and self.coordinator.authoritative(assignment_id)
        )

    def progress(self, record, phase, health=None, code=None):
        record = self.coordinator.update(
            record["assignment_id"], event_sequence=record.get("event_sequence", 0) + 1
        )
        self.api.event(record, phase, health, code)
        return record

    def run(self, record):
        asyncio.run(self.execute(record))

    async def execute(self, record):
        assignment_id = record["assignment_id"]
        root = Path("/run/dml-interactive") / assignment_id
        endpoint_dir = Path("/run/dml-interactive-endpoints") / assignment_id
        stage = "reserve"
        workload = None
        try:
            root.mkdir(mode=0o750, parents=True, exist_ok=False)
            os.chown(root, 0, 10001)
            # mkdir's mode is masked by the process umask (systemd UMask=0077
            # turns 0750 into 0700, locking out the access sidecar running as
            # 10001:10001 with EACCES on broker.sock). Enforce exact perms.
            os.chmod(root, 0o750)
            endpoint_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
            os.chmod(endpoint_dir, 0o700)
            token = secrets.token_urlsafe(48).encode()
            token_file = root / "broker.token"
            token_file.write_bytes(token)
            token_file.chmod(0o640)
            os.chown(token_file, 0, 10001)
            stage = "pull"
            record = await asyncio.to_thread(self.progress, record, "PULLING")
            image, user, workdir = await asyncio.to_thread(self.ops.pull, record)
            stage = "workload-start"
            record = await asyncio.to_thread(self.progress, record, "STARTING")
            workload = await asyncio.to_thread(
                self.ops.workload, record, image, user, workdir
            )
            authority = lambda: self.authority(assignment_id)
            self.broker = Broker(
                root / "broker.sock",
                token,
                self.ops.client,
                workload.id,
                user,
                workdir,
                authority,
                on_failure=lambda: setattr(self, "failed", True),
            )
            await self.broker.start()
            # Smoke a real default-shell session before Access or Serve starts.
            stage = "broker-smoke"
            reader, writer = await asyncio.open_unix_connection(
                str(root / "broker.sock")
            )
            try:
                import hmac

                async with asyncio.timeout(8):
                    kind, challenge = await read_record(reader)
                    if kind != Type.CHALLENGE:
                        raise RuntimeFailure("START_FAILED")
                    await write_record(
                        writer,
                        Type.AUTH,
                        hmac.digest(token, b"dml-broker-v1\0" + challenge, "sha256"),
                    )
                    if (await read_record(reader))[0] != Type.AUTHENTICATED:
                        raise RuntimeFailure("START_FAILED")
                    await write_record(
                        writer,
                        Type.OPEN,
                        json_bytes({"shell": "default", "columns": 80, "rows": 24}),
                    )
                    if (await read_record(reader))[0] != Type.OPENED:
                        raise RuntimeFailure("START_FAILED")
                    await write_record(writer, Type.CLOSE)
                    while True:
                        kind, _ = await read_record(reader)
                        if kind == Type.EXIT:
                            break
                        if kind != Type.STDOUT:
                            raise RuntimeFailure("START_FAILED")
            finally:
                writer.close()
                await writer.wait_closed()
            stage = "unit-start"
            sidecar, access = await asyncio.to_thread(
                self.ops.unit, record, root, endpoint_dir
            )
            stage = "endpoint-wait"
            deadline = time.monotonic() + 120
            while not (endpoint_dir / "tailscaled.sock").exists():
                if not authority() or time.monotonic() >= deadline:
                    raise RuntimeFailure("START_FAILED")
                await asyncio.sleep(0.2)
            self.endpoint = Endpoint(
                endpoint_dir / "tailscaled.sock", lambda: self.ops.authority(record)
            )
            stage = "endpoint-join"
            while True:
                try:
                    bootstrap = await asyncio.to_thread(self.api.bootstrap, record)
                    break
                except RuntimeError:
                    if not authority() or time.monotonic() >= deadline:
                        raise RuntimeFailure("START_FAILED")
                    await asyncio.sleep(1)
            await asyncio.to_thread(self.endpoint.join, bootstrap)

            async def access_health():
                # Fixed loopback target in the endpoint namespace; host performs
                # a trusted sidecar exec, never exposes the health port publicly.
                result = await asyncio.to_thread(
                    sidecar.exec_run,
                    ["/usr/local/bin/tailscale", "serve", "status", "--json"],
                )
                access.reload()
                return access.status == "running" and result.exit_code == 0

            # Probe the Access backend via the sidecar namespace. wget is included
            # in the pinned Alpine Tailscale image; no user-selected destination.
            async def backend_health():
                result = await asyncio.to_thread(
                    sidecar.exec_run,
                    [
                        "wget",
                        "-q",
                        "-T",
                        "3",
                        "-O",
                        "/dev/null",
                        "http://127.0.0.1:9002/health/ready",
                    ],
                )
                return result.exit_code == 0

            stage = "backend-wait"
            # Join/bootstrap may consume the earlier budget; the backend wait
            # gets its own. Lease fencing still comes from authority().
            deadline = time.monotonic() + 120
            while not await backend_health():
                if not authority() or time.monotonic() >= deadline:
                    raise RuntimeFailure("START_FAILED")
                await asyncio.sleep(0.2)
            stage = "serve"
            await asyncio.to_thread(self.endpoint.serve, True)
            self.health[assignment_id] = {
                "workload": True,
                "broker": True,
                "access": True,
                "endpoint": True,
            }
            stage = "connecting"
            record = await asyncio.to_thread(
                self.progress, record, "CONNECTING", self.health[assignment_id]
            )
            self.coordinator.mode = "INTERACTIVE_ACTIVE"
            stage = "healthy"
            session_start = time.monotonic()
            while authority():
                limit = max_duration_seconds()
                if limit and time.monotonic() - session_start >= limit:
                    logger.warning(
                        "Interactive runtime time up assignment_id=%s limit=%ds",
                        assignment_id,
                        limit,
                    )
                    with suppress(Exception):
                        await asyncio.to_thread(
                            notify_time_up, workload, limit
                        )
                    # Brief grace so live PTY users receive the wall
                    # broadcast / disconnect reason before containers stop.
                    await asyncio.sleep(2)
                    raise RuntimeFailure("TIME_UP")
                healthy = (
                    await asyncio.to_thread(self.broker.healthy)
                    and await backend_health()
                    and await asyncio.to_thread(self.endpoint.health)
                )
                if not healthy:
                    raise RuntimeFailure("HEALTH_FAILED")
                self.health[assignment_id] = {
                    "workload": True,
                    "broker": True,
                    "access": True,
                    "endpoint": True,
                }
                await asyncio.sleep(1)
        except Exception as exc:
            code = exc.code if isinstance(exc, RuntimeFailure) else "START_FAILED"
            if code == "TIME_UP":
                limit = max_duration_seconds() or DEFAULT_MAX_DURATION_SECONDS
                detail = time_up_detail(limit)
                diagnostics = {"dump_dir": None}
                record = self.coordinator.update(
                    assignment_id,
                    runtime_failure_code=code,
                    runtime_failure_stage=stage,
                    runtime_failure_detail=detail,
                )
                logger.warning(
                    "Interactive runtime time up assignment_id=%s stage=%s detail=%s",
                    assignment_id,
                    stage,
                    detail,
                )
            else:
                # Snapshot container logs/inspect to the state dir before STOPPING
                # and cleanup. Containers alone are weak evidence: a later prune
                # or INTERACTIVE_CLEANUP_ON_FAILURE=1 removes them, and the
                # /run/dml-interactive dir is always deleted below.
                try:
                    journaled = self.coordinator.get(assignment_id)
                except Exception:
                    journaled = {"assignment_id": assignment_id, "containers": {}}
                diagnostics = await asyncio.to_thread(
                    collect_failure_diagnostics,
                    journaled,
                    self.ops.client,
                    getattr(self.coordinator, "path", None),
                    stage,
                    code,
                )
                detail = short_detail(stage, diagnostics)
                # Persist before the event request. Cleanup carries the same fenced
                # code so a transient callback failure cannot downgrade a failed
                # runtime to an ordinary stop.
                record = self.coordinator.update(
                    assignment_id,
                    runtime_failure_code=code,
                    runtime_failure_stage=stage,
                    runtime_failure_detail=detail,
                )
                logger.error(
                    "Interactive runtime failed assignment_id=%s stage=%s code=%s "
                    "detail=%s diagnostics=%s",
                    assignment_id,
                    stage,
                    code,
                    detail,
                    diagnostics.get("dump_dir")
                    or dump_dir(getattr(self.coordinator, "path", "?"), assignment_id),
                    exc_info=True,
                )
            with suppress(Exception):
                record = await asyncio.to_thread(
                    self.progress, record, "STOPPING", {}, code
                )
        finally:
            self.health[assignment_id] = {
                "workload": False,
                "broker": False,
                "access": False,
                "endpoint": False,
            }
            self.coordinator.mode = "CLEANING"
            if self.endpoint:
                with suppress(Exception):
                    await asyncio.to_thread(self.endpoint.serve, False)
            if self.broker:
                with suppress(Exception):
                    await self.broker.stop()
            try:
                await asyncio.to_thread(
                    self.ops.cleanup, self.coordinator.get(assignment_id)
                )
                # No launch remains outstanding here. Docker create is awaited;
                # startup reconciliation handles process death mid-create.
                shutil.rmtree(root, ignore_errors=False) if root.exists() else None
                (
                    shutil.rmtree(endpoint_dir, ignore_errors=False)
                    if endpoint_dir.exists()
                    else None
                )
                self.coordinator.mark_clean(assignment_id)
            except Exception:
                self.coordinator.update(assignment_id, uncertain=True)
                self.coordinator.mode = "UNCERTAIN"
            if self.endpoint:
                self.endpoint.close()
