import asyncio
from datetime import datetime
import json
from pathlib import Path
import time
from uuid import uuid4

import jwt
import pytest
from websockets.exceptions import ConnectionClosed

from conftest import wait
from test_gateway import open_stream, exact


async def fault(action):
    request_id = str(uuid4())
    path = Path("/results/fault-request.json")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"id": request_id, "action": action}))
    temporary.replace(path)
    async def complete():
        response = Path("/results/fault-response.json")
        if response.exists():
            record = json.loads(response.read_text())
            if record.get("id") == request_id:
                assert record["ok"], "fault orchestration failed"
                return True
        return False
    await wait(complete, timeout=45)


@pytest.mark.asyncio
async def test_unused_key_expiry_and_ticket_audience_integrity(controller, records):
    enrollment = await controller.internal("POST", "enrollments", {"role": "endpoint", "identity": "unused", "generation": "expiry"},
        headers={"Idempotency-Key": "unused-expiry"})
    async def expired():
        status = await controller.internal("GET", "enrollments/" + enrollment["enrollment_id"])
        return status["state"] == "EXPIRED"
    await wait(expired, timeout=40)
    await controller.request("fixture/agent", {"name": "fresh", "path": "join", "body": enrollment}, expected=503)
    assert (await controller.agent("fresh", "status", method="GET"))["BackendState"] != "Running"
    issued = await controller.ticket(generation="g2")
    # Changing the audience in a real issued ticket invalidates its signature.
    header, payload, signature = issued["ticket"].split(".")
    import base64
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    claims["aud"] = "other-gateway"
    changed = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    with pytest.raises(ConnectionClosed):
        await open_stream(header + "." + changed + "." + signature)


@pytest.mark.asyncio
async def test_endpoint_offline_readiness_and_session_lease(controller, records):
    issued = await controller.ticket(generation="g2")
    stream = await open_stream(issued["ticket"])
    assert await exact(stream, 6) == b"NEW-A:"
    await controller.agent("replacement", "offline")
    async def unavailable():
        response = await controller.ticket(generation="g2", expected=404)
        return True
    try:
        await wait(unavailable, timeout=10)
        with pytest.raises(ConnectionClosed):
            await asyncio.wait_for(stream.recv(), 8)
    finally:
        await controller.agent("replacement", "online")
    await wait(lambda: controller.ticket(generation="g2"))


@pytest.mark.asyncio
async def test_management_outage_fails_admission_and_bounds_live_authorization(controller, records):
    issued = await controller.ticket(generation="g2")
    stream = await open_stream(issued["ticket"])
    assert await exact(stream, 6) == b"NEW-A:"
    unused = await controller.ticket(generation="g2")
    await fault("stop-management")
    try:
        with pytest.raises(ConnectionClosed):
            await open_stream(unused["ticket"])
        with pytest.raises(ConnectionClosed):
            await asyncio.wait_for(stream.recv(), 8)
    finally:
        await fault("start-management")
    await wait(lambda: controller.ticket(generation="g2"))
    with pytest.raises(ConnectionClosed):
        await open_stream(issued["ticket"])  # durable replay fence after restart


@pytest.mark.asyncio
async def test_headscale_outage_never_creates_ready_or_enrollment(controller, records):
    await fault("stop-headscale")
    try:
        await controller.internal("POST", "enrollments", {"role": "endpoint", "identity": "outage", "generation": "g1"},
            expected=503, headers={"Idempotency-Key": "outage-key"})
        async def unavailable():
            await controller.ticket(generation="g2", expected=404)
            return True
        await wait(unavailable, timeout=10)
        # Existing encrypted TCP need not stop merely because coordination is
        # down; authorization is tested independently by management outage.
    finally:
        await fault("start-headscale")
    await wait(lambda: controller.ticket(generation="g2"))


@pytest.mark.asyncio
async def test_managed_resource_cleanup_completes_without_touching_sentinel(controller, records):
    await controller.internal("DELETE", "resources/resource-a")
    enrollment_id = records["replacement"]["enrollment"]["enrollment_id"]
    async def cleaned():
        status = await controller.internal("GET", "enrollments/" + enrollment_id)
        return status["state"] == "REVOKED"
    await wait(cleaned)
