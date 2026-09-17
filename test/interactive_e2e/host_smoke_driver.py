"""Operator-invoked check against installed services; never run by portable CI.

Runs in a disposable endpoint's userspace network namespace. Only this driver
receives the controller credential; credentials and binary data are never logged.
"""
import asyncio
import json
import os
from pathlib import Path

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from interactive_gateway.tailscale_api import TailscaleAPI


async def main():
    resource = os.environ["SMOKE_RESOURCE"]
    generation = "v1"
    enrollment = None
    registered = False
    server = None
    local = TailscaleAPI("/var/run/tailscale/tailscaled.sock")
    credential = Path("/secrets/controller").read_text().strip()
    async with httpx.AsyncClient(base_url=os.environ["SMOKE_MANAGEMENT"], timeout=5,
            headers={"Authorization": "Bearer " + credential}) as client:
        async def request(method, path, body=None, headers=None):
            response = await client.request(method, path, json=body, headers=headers)
            if response.is_error:
                raise RuntimeError("management request failed: " + str(response.status_code))
            return response.json()

        async def echo(reader, writer):
            try:
                writer.write(b"VM:")
                await writer.drain()
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        try:
            enrollment = await request("POST", "enrollments", {"role": "endpoint",
                "identity": resource, "generation": generation}, {"Idempotency-Key": resource})
            await local.start(enrollment["login_server"], enrollment["hostname"], enrollment.pop("key"))
            async with asyncio.timeout(90):
                while (await local.status()).get("BackendState") != "Running":
                    await asyncio.sleep(1)
            await request("POST", "enrollments/" + enrollment["enrollment_id"] + "/confirm",
                {"generation": generation})
            server = await asyncio.start_server(echo, "127.0.0.1", 9000)
            process = await asyncio.create_subprocess_exec("tailscale", "serve", "--bg", "--tcp=9000",
                "tcp://127.0.0.1:9000", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            async with asyncio.timeout(15):
                if await process.wait():
                    raise RuntimeError("endpoint Serve failed")
            await request("PUT", "resources/" + resource + "/endpoint", {"owner": "host-validation",
                "generation": generation, "enrollment_id": enrollment["enrollment_id"],
                "service": "echo", "protocol": "tcp-stream-v1", "port": 9000})
            registered = True
            async with asyncio.timeout(90):
                while True:
                    status = await request("POST", "resources/" + resource + "/lease", {"generation": generation})
                    if status["state"] == "READY":
                        break
                    await asyncio.sleep(2)
            print("Real endpoint enrollment and Gateway probe passed", flush=True)
            grant = await request("POST", "access-grants", {"user": "host-validation", "resource_id": resource,
                "generation": generation, "service": "echo", "gateway_id": "gateway-main", "authorized": True})
            url = os.environ["SMOKE_PUBLIC_URL"].replace("https://", "wss://", 1) + "/v1/connect/" + resource + "/echo"
            async with connect(url, origin=os.environ["SMOKE_ORIGIN"], compression=None, open_timeout=10) as websocket:
                await websocket.send(json.dumps({"type": "authenticate", "ticket": grant["ticket"]}))
                async with asyncio.timeout(15):
                    if json.loads(await websocket.recv()).get("type") != "ready":
                        raise RuntimeError("Gateway did not admit the issued ticket")
                    payload = bytes(range(256)) * 128
                    await websocket.send(payload)
                    received = b""
                    while len(received) < len(payload) + 3:
                        frame = await websocket.recv()
                        if not isinstance(frame, bytes):
                            raise RuntimeError("nonbinary relay data")
                        received += frame
                    if received != b"VM:" + payload:
                        raise RuntimeError("binary relay mismatch")
            print("Trusted public Caddy WSS and exact binary relay passed", flush=True)
            async with connect(url, origin=os.environ["SMOKE_ORIGIN"], compression=None, open_timeout=10) as websocket:
                await websocket.send(json.dumps({"type": "authenticate", "ticket": grant["ticket"]}))
                try:
                    async with asyncio.timeout(10):
                        await websocket.recv()
                except ConnectionClosed as error:
                    if error.rcvd is None or error.rcvd.code != 4410:
                        raise RuntimeError("unexpected replay rejection") from None
                else:
                    raise RuntimeError("consumed ticket replay admitted")
            print("Consumed ticket replay denied", flush=True)
        finally:
            if enrollment:
                await request("DELETE", "resources/" + resource if registered else
                    "enrollments/" + enrollment["enrollment_id"])
                async with asyncio.timeout(60):
                    while (await request("GET", "enrollments/" + enrollment["enrollment_id"]))["state"] != "REVOKED":
                        await asyncio.sleep(2)
                print("Exact temporary endpoint cleanup passed", flush=True)
            if server:
                server.close()
                await server.wait_closed()
            await local.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        # Exception messages may include URLs, credentials or application data.
        print("Host smoke check failed (" + type(error).__name__ + "); no private response logged", flush=True)
        raise SystemExit(1)
