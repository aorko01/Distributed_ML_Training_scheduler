import asyncio
import pytest
from websockets.exceptions import ConnectionClosed
from conftest import wait
from test_gateway import open_stream, exact


@pytest.mark.asyncio
async def test_generation_replacement_revokes_old_stream_and_tickets(controller, records):
    issued = await controller.ticket()
    old = await open_stream(issued["ticket"])
    assert await exact(old, 2) == b"A:"
    unused = await controller.ticket()
    replacement = await controller.request("fixture/prepare", {"resource": "resource-a", "generation": "g2", "agent": "replacement", "owner": "user-a"})
    await wait(lambda: controller.ticket(generation="g2"))
    with pytest.raises(ConnectionClosed):
        await asyncio.wait_for(old.recv(), 8)
    with pytest.raises(ConnectionClosed):
        await open_stream(unused["ticket"])
    await controller.internal("POST", "enrollments/" + records["a"]["enrollment"]["enrollment_id"] + "/confirm", {"generation": "g1"}, expected=409)
    fresh = await controller.ticket(generation="g2")
    stream = await open_stream(fresh["ticket"])
    assert await exact(stream, 6) == b"NEW-A:"
    await stream.close()
    records["replacement"] = replacement


@pytest.mark.asyncio
async def test_revocation_denies_immediately_closes_live_stream_and_cleans_exact_node(controller, records):
    issued = await controller.ticket("user-b", "resource-b")
    stream = await open_stream(issued["ticket"], "resource-b")
    assert await exact(stream, 2) == b"B:"
    await controller.internal("DELETE", "resources/resource-b")
    await controller.ticket("user-b", "resource-b", expected=410)
    with pytest.raises(ConnectionClosed):
        await asyncio.wait_for(stream.recv(), 8)
    async def cleaned():
        status = await controller.internal("GET", "enrollments/" + records["b"]["enrollment"]["enrollment_id"])
        return status["state"] == "REVOKED"
    await wait(cleaned)
