"""Actual host acceptance: real Scheduler, Worker, management, Gateway and UI.
Run on the host-installed Ubuntu Worker with a disposable owned ready workspace.
Provisioning/secrets remain host/operator concerns; no fake broker or endpoint.
"""

import argparse
import asyncio
from contextlib import suppress
import json
from pathlib import Path
import sqlite3
import sys
import time
import uuid
import httpx
import docker
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "Worker"))
from Access_Container.interactive_access.protocol import (
    Type,
    Parser,
    encode,
    json_bytes,
    parse_json,
)


def counters(state_dir):
    with sqlite3.connect(Path(state_dir) / "assignments.sqlite") as db:
        return dict(
            db.execute(
                "SELECT key,value FROM metadata WHERE key IN ('claims_sent','heartbeats_sent')"
            )
        )


async def main(args):
    token = Path(args.owner_token_file).read_text().strip()
    if not args.scheduler.startswith("https://"):
        raise ValueError("Scheduler HTTPS required")
    client = httpx.AsyncClient(
        base_url=args.scheduler,
        headers={"Authorization": "Bearer " + token},
        verify=args.ca_file or True,
        timeout=15,
    )

    async def request(method, path, body=None, headers=None):
        response = await client.request(method, path, json=body, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(
                "Scheduler rejected acceptance operation (%d)" % response.status_code
            )
        return response.json()

    runtime = None

    async def wait_state(states, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = await request(
                "GET", f"/interactive/workspaces/{args.workspace_id}/runtime"
            )
            if value and value["state"] in states:
                return value
            if value and value["state"] in ("FAILED", "LOST"):
                raise RuntimeError("Runtime unavailable during acceptance")
            await asyncio.sleep(1)
        raise RuntimeError("Acceptance deadline exceeded")

    try:
        image = await request("GET", "/interactive/workspaces/" + args.workspace_id)
        assert image["revision"]["state"] == "IMAGE_READY"
        runtime = await request(
            "POST",
            f"/interactive/workspaces/{args.workspace_id}/runtimes",
            {},
            {"Idempotency-Key": str(uuid.uuid4())},
        )
        assert runtime["state"] == "QUEUED"
        runtime = await wait_state({"READY"}, 2100)
        d = docker.from_env()
        objects = d.containers.list(filters={"label": ["dml.runtime=" + runtime["id"]]})
        assert len(objects) == 3
        workload = next(c for c in objects if c.labels["dml.component"] == "workload")
        access = next(c for c in objects if c.labels["dml.component"] == "access")
        sidecar = next(c for c in objects if c.labels["dml.component"] == "sidecar")
        inspect = d.api.inspect_container(workload.id)
        assert not inspect["Mounts"] and inspect["HostConfig"]["NetworkMode"] == "none"
        devices = inspect["HostConfig"]["DeviceRequests"]
        assert len(devices) == 1 and len(devices[0]["DeviceIDs"]) == 1
        assert devices[0]["DeviceIDs"][0].startswith("GPU-") and inspect["HostConfig"][
            "StorageOpt"
        ].get("size")
        assert (
            d.api.inspect_container(access.id)["HostConfig"]["NetworkMode"]
            == "container:" + sidecar.id
        )
        assert all(
            m["Destination"] != "/var/run/docker.sock"
            for m in d.api.inspect_container(access.id)["Mounts"]
        )
        gpu = workload.exec_run(
            ["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader"]
        )
        assert (
            gpu.exit_code == 0
            and gpu.output.decode().strip() == devices[0]["DeviceIDs"][0]
        )
        before = counters(args.state_dir)
        for _ in range(3):
            grant = await request(
                "POST", "/interactive/runtimes/" + runtime["id"] + "/connection"
            )
            async with websockets.connect(
                grant["wss_url"], origin=args.origin, max_size=65536
            ) as ws:
                await ws.send(
                    json.dumps({"type": "authenticate", "ticket": grant["ticket"]})
                )
                ready = json.loads(await ws.recv())
                assert ready == {"type": "ready", "protocol": "tcp-stream-v1"}
                await ws.send(
                    encode(
                        Type.OPEN,
                        json_bytes({"columns": 80, "rows": 24, "shell": "default"}),
                    )
                )
                parser = Parser()
                opened = False
                exited = False
                async with asyncio.timeout(15):
                    while not exited:
                        data = await ws.recv()
                        assert isinstance(data, bytes)
                        for kind, payload in parser.feed(data):
                            if kind == Type.OPENED:
                                assert (
                                    not opened
                                    and parse_json(payload)["protocol"]
                                    == "terminal-stream-v1"
                                )
                                opened = True
                                await ws.send(encode(Type.CLOSE))
                            elif kind == Type.EXIT:
                                assert opened
                                exited = True
                            else:
                                assert kind == Type.STDOUT and opened
                parser.eof()
            await asyncio.sleep(6)
        workload.reload()
        assert workload.status == "running"
        after = counters(args.state_dir)
        assert before["claims_sent"] == after["claims_sent"]
        assert int(after["heartbeats_sent"]) > int(before["heartbeats_sent"])
        if args.ui_url:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                # User login token follows existing app storage, never the ticket.
                await page.goto(args.ui_url)
                await page.evaluate(
                    '(token) => localStorage.setItem("auth_token", token)', token
                )
                await page.goto(args.ui_url + "/interactive/" + args.workspace_id)
                await page.get_by_role("button", name="Connect", exact=True).click()
                await page.get_by_text("Connected successfully", exact=True).wait_for(
                    timeout=20000
                )
                await browser.close()
        print(
            "Real runtime Start/READY, WSS OPENED/CLOSE, assigned GPU, exclusive polling and heartbeat checks passed"
        )
        await request("POST", "/interactive/runtimes/" + runtime["id"] + "/stop")
        await wait_state({"STOPPED"}, 180)
        assert not d.containers.list(
            all=True, filters={"label": ["dml.runtime=" + runtime["id"]]}
        )
        deadline = time.monotonic() + 60
        while True:
            with sqlite3.connect(Path(args.state_dir) / "assignments.sqlite") as db:
                records = [
                    json.loads(data)
                    for (data,) in db.execute("SELECT data FROM journal")
                ]
            record = next(
                r for r in records if r["payload"].get("runtime_id") == runtime["id"]
            )
            if (
                record.get("local_clean")
                and record.get("released")
                and int(counters(args.state_dir)["claims_sent"])
                > int(after["claims_sent"])
            ):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Worker did not resume normal polling after release")
            await asyncio.sleep(1)
        print(
            "Stop exact-container cleanup, durable release and resumed polling passed"
        )
    finally:
        if runtime:
            with suppress(Exception):
                await request(
                    "POST", "/interactive/runtimes/" + runtime["id"] + "/stop"
                )
        await client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("scheduler", "workspace-id", "owner-token-file", "origin"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--ca-file")
    parser.add_argument("--ui-url")
    parser.add_argument("--state-dir", default="/var/lib/dml-worker")
    asyncio.run(main(parser.parse_args()))
