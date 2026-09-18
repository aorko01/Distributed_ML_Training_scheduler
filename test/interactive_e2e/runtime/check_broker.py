"""Run as root on a separate Ubuntu Docker host; no GPU or real secrets needed.
Uses an existing immutable tiny fixture digest from a disposable registry. Never
counts this CPU fixture as production GPU/quotas/multi-host acceptance.
"""

import asyncio
import hmac
import os
from pathlib import Path
import re
import secrets
import sys
import time
import uuid
import docker

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "Worker"))
from interactive.broker import Broker
from Access_Container.interactive_access.protocol import (
    Type,
    read_record,
    write_record,
    json_bytes,
)


async def run():
    if os.geteuid() != 0:
        raise RuntimeError("Run on the Docker host as root for pidfd identity checks")
    image = os.environ["RUNTIME_FIXTURE_DIGEST"]
    if not re.search(r"@sha256:[0-9a-f]{64}$", image):
        raise RuntimeError("Digest fixture required")
    client = docker.from_env(timeout=10)
    client.images.pull(image)
    assignment = str(uuid.uuid4())
    directory = Path("/run/dml-interactive") / assignment
    directory.mkdir(mode=0o700, parents=True)
    sentinel = client.containers.create(
        image,
        entrypoint="/bin/sh",
        command=["-c", "sleep 600"],
        network_mode="none",
        labels={"dml.fixture": "sentinel"},
    )
    c = client.containers.create(
        image,
        entrypoint="/bin/sh",
        command=["-c", "while :; do sleep 3600; done"],
        working_dir="/workspace",
        user="1000:1000",
        network_mode="none",
        init=True,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        mem_limit="64m",
        pids_limit=64,
        labels={"dml.fixture": assignment},
    )
    failed = []
    token = secrets.token_bytes(48)
    b = Broker(
        directory / "broker.sock",
        token,
        client,
        c.id,
        "1000:1000",
        "/workspace",
        lambda: True,
        on_failure=lambda: failed.append(True),
    )

    async def session(command=None, hostile=False):
        r, w = await asyncio.open_unix_connection(str(b.path))
        try:
            _, challenge = await read_record(r)
            await write_record(
                w,
                Type.AUTH,
                hmac.digest(token, b"dml-broker-v1\0" + challenge, "sha256"),
            )
            assert (await read_record(r))[0] == Type.AUTHENTICATED
            await write_record(
                w,
                Type.OPEN,
                json_bytes({"shell": "default", "columns": 80, "rows": 24}),
            )
            assert (await read_record(r))[0] == Type.OPENED
            if command:
                await write_record(w, Type.STDIN, command)
                found = bytearray()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    kind, payload = await asyncio.wait_for(read_record(r), 3)
                    if kind == Type.STDOUT:
                        found.extend(payload)
                    if b"workload-fixture-v1" in found and b"/workspace" in found:
                        break
                assert b"workload-fixture-v1" in found and b"/workspace" in found
            await write_record(w, Type.RESIZE, json_bytes({"columns": 100, "rows": 30}))
            if hostile:
                await write_record(
                    w,
                    Type.STDIN,
                    b"trap '' HUP TERM; (trap '' HUP TERM; sleep 600) &\n",
                )
                await asyncio.sleep(0.3)
            await write_record(w, Type.CLOSE)
            async with asyncio.timeout(8):
                while True:
                    kind, _ = await read_record(r)
                    if kind == Type.EXIT:
                        break
                    assert kind == Type.STDOUT
        finally:
            w.close()
            await w.wait_closed()

    try:
        sentinel.start()
        c.start()
        await b.start()
        await session(b"cat identity.txt; pwd; id -u\n")
        for _ in range(10):
            await session()
        await session(hostile=True)
        c.reload()
        sentinel.reload()
        assert c.status == "running" and sentinel.status == "running" and not failed
        # Keepalive + init only; no leaked shells/descendants after repeated checks.
        processes = c.top()["Processes"]
        assert len(processes) <= 3
        inspect = client.api.inspect_container(c.id)
        assert not inspect["Mounts"] and inspect["HostConfig"]["NetworkMode"] == "none"
        print(
            "Real Docker PTY identity, resize, repeated CLOSE, hostile cleanup and unrelated sentinel passed (CPU fixture only)"
        )
    finally:
        with __import__("contextlib").suppress(Exception):
            await b.stop()
        c.remove(force=True)
        sentinel.remove(force=True)
        with __import__("contextlib").suppress(FileNotFoundError):
            directory.rmdir()


if __name__ == "__main__":
    asyncio.run(run())
