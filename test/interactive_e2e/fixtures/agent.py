"""Disposable fixture control agent, reachable only by the test controller."""
import asyncio
import os
import subprocess
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict
import secrets

from interactive_gateway.tailscale_api import TailscaleAPI
from interactive_gateway.tailnet_dialer import TailnetDialer

app = FastAPI()
local = TailscaleAPI("/var/run/tailscale/tailscaled.sock")


@app.middleware("http")
async def authenticate(request: Request, call_next):
    if not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + os.environ["FIXTURE_SECRET"]):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "denied"}, status_code=401)
    return await call_next(request)


@app.get("/status")
async def status():
    return await local.status()


@app.post("/join")
async def join(request: Request):
    body = await request.json()
    await local.start(body["login_server"], body["hostname"], body["key"])
    deadline = asyncio.get_running_loop().time() + 5
    while asyncio.get_running_loop().time() < deadline:
        if (await local.status()).get("BackendState") == "Running":
            for port in (9000, 9001):
                process = await asyncio.create_subprocess_exec("tailscale", "serve", "--bg", "--tcp=" + str(port),
                    "tcp://127.0.0.1:" + str(port), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                if await process.wait():
                    raise HTTPException(503, "fixture Serve failed")
            return {"state": "Running"}
        await asyncio.sleep(0.25)
    raise HTTPException(503, "fixture enrollment timed out")


@app.post("/tcp")
async def tcp(request: Request):
    """Policy attacks use real SOCKS and tailnet IP; not bridge destinations."""
    body = await request.json()
    writer = None
    try:
        async with asyncio.timeout(3):
            # Inline SOCKS support for denied canary port, fixture only.
            import socket, struct
            reader, writer = await asyncio.open_connection("127.0.0.1", 1055)
            writer.write(b"\x05\x01\x00")
            await writer.drain()
            assert await reader.readexactly(2) == b"\x05\x00"
            writer.write(b"\x05\x01\x00\x01" + socket.inet_aton(body["ip"]) + struct.pack("!H", body["port"]))
            await writer.drain()
            header = await reader.readexactly(4)
            if header[1] != 0:
                return {"connected": False}
            await reader.readexactly((4 if header[3] == 1 else 16) + 2)
            return {"connected": True, "prefix": (await reader.read(128)).decode()}
    except (OSError, TimeoutError, asyncio.IncompleteReadError):
        return {"connected": False}
    finally:
        if writer:
            writer.close()
            await writer.wait_closed()


@app.get("/listeners")
async def listeners():
    for port in (9000, 9001):
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        await writer.wait_closed()
    import socket
    response = await local.client.get("serve-config")
    configuration = response.json() or {}
    tcp = configuration.get("TCP", {})
    return {"tcp_ports": sorted(int(port) for port in tcp), "ports": [9000, 9001], "bind": "127.0.0.1", "bridge_ip": socket.gethostbyname(socket.gethostname())}


@app.post("/offline")
async def offline():
    result = await local.client.patch("prefs", json={"WantRunning": False, "WantRunningSet": True})
    result.raise_for_status()
    return {"state": "offline"}


@app.post("/online")
async def online():
    result = await local.client.patch("prefs", json={"WantRunning": True, "WantRunningSet": True})
    result.raise_for_status()
    return {"state": "online"}


@app.post("/serve")
async def serve():
    for port in (9000, 9001):
        process = await asyncio.create_subprocess_exec("tailscale", "serve", "--bg", "--tcp=" + str(port),
            "tcp://127.0.0.1:" + str(port), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        if await process.wait():
            raise HTTPException(503, "fixture Serve failed")
    return {"state": "published"}


@app.get("/pty-status")
async def pty_status():
    from pathlib import Path
    try:
        count = int(Path('/run/dml-interactive/terminal/children').read_text())
    except (OSError, ValueError):
        raise HTTPException(503, "broker fixture unavailable")
    return {"children": count}
