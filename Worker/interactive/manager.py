import asyncio
from contextlib import suppress
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
        try:
            root.mkdir(mode=0o750, parents=True, exist_ok=False)
            os.chown(root, 0, 10001)
            endpoint_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
            token = secrets.token_urlsafe(48).encode()
            token_file = root / "broker.token"
            token_file.write_bytes(token)
            token_file.chmod(0o640)
            os.chown(token_file, 0, 10001)
            record = await asyncio.to_thread(self.progress, record, "PULLING")
            image, user, workdir = await asyncio.to_thread(self.ops.pull, record)
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
            sidecar, access = await asyncio.to_thread(
                self.ops.unit, record, root, endpoint_dir
            )
            deadline = time.monotonic() + 120
            while not (endpoint_dir / "tailscaled.sock").exists():
                if not authority() or time.monotonic() >= deadline:
                    raise RuntimeFailure("START_FAILED")
                await asyncio.sleep(0.2)
            self.endpoint = Endpoint(
                endpoint_dir / "tailscaled.sock", lambda: self.ops.authority(record)
            )
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

            while not await backend_health():
                if not authority() or time.monotonic() >= deadline:
                    raise RuntimeFailure("START_FAILED")
                await asyncio.sleep(0.2)
            await asyncio.to_thread(self.endpoint.serve, True)
            self.health[assignment_id] = {
                "workload": True,
                "broker": True,
                "access": True,
                "endpoint": True,
            }
            record = await asyncio.to_thread(
                self.progress, record, "CONNECTING", self.health[assignment_id]
            )
            self.coordinator.mode = "INTERACTIVE_ACTIVE"
            while authority():
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
