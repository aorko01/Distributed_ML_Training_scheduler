import asyncio
import os
import pytest
import websockets
from websockets.exceptions import ConnectionClosed


async def open_stream(ticket, resource="resource-a"):
    websocket = await websockets.connect("ws://tailscale:8030/v1/connect/" + resource + "/echo", max_size=65536)
    await websocket.send(__import__("json").dumps({"type": "authenticate", "ticket": ticket}))
    import json
    assert json.loads(await websocket.recv())["type"] == "ready"
    return websocket


async def exact(websocket, size):
    result = b""
    while len(result) < size:
        data = await asyncio.wait_for(websocket.recv(), 5)
        assert isinstance(data, bytes)
        result += data
    assert len(result) == size
    return result


@pytest.mark.asyncio
async def test_bidirectional_concurrent_binary_routing(controller, records):
    async def check(name):
        issued = await controller.ticket("user-" + name, "resource-" + name)
        ws = await open_stream(issued["ticket"], "resource-" + name)
        try:
            assert await exact(ws, 2) == name.upper().encode() + b":"
            payload = b"\x00\xff\x80" + os.urandom(131069)
            for offset in range(0, len(payload), 32768):
                await ws.send(payload[offset:offset + 32768])
            matches = await exact(ws, len(payload)) == payload
            assert matches, "binary stream mismatch"
        finally:
            await ws.close()
    await asyncio.gather(check("a"), check("b"), check("a"), check("b"))


@pytest.mark.asyncio
async def test_path_forgery_replay_and_owner_authorization(controller, records):
    await controller.ticket("user-a", "resource-b", expected=403)
    issued = await controller.ticket()
    with pytest.raises(ConnectionClosed):
        await open_stream(issued["ticket"], "resource-b")
    with pytest.raises(ConnectionClosed):
        await open_stream("forged")
    ws = await open_stream(issued["ticket"])
    await ws.close()
    with pytest.raises(ConnectionClosed):
        await open_stream(issued["ticket"])


def test_deliberate_failure_verifies_harness(request):
    if request.config.getoption("--deliberate-failure"):
        pytest.fail("deliberate harness failure; no credentials")
