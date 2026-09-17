import pytest
from conftest import wait


@pytest.mark.asyncio
async def test_real_enrollment_binding_and_role_boundaries(controller, records):
    for name, record in records.items():
        status = await controller.internal("GET", "enrollments/" + record["enrollment"]["enrollment_id"])
        assert status["state"] == "CONFIRMED" and status["online"] and status["node_id"]
        assert status["ips"][0].startswith("100.") and "key" not in status
        assert status["headscale_key_id"] and status["tags"] == ["tag:interactive-endpoint"] and status["ephemeral"]
        await controller.internal("GET", "enrollments/" + record["enrollment"]["enrollment_id"], role="gateway", expected=403)
    await controller.internal("POST", "access-grants", {"user": "user-a", "resource_id": "resource-a", "generation": "g1",
        "service": "echo", "gateway_id": "gateway-main", "authorized": True}, role="gateway", expected=403)


@pytest.mark.asyncio
async def test_consumed_key_cannot_join_fresh_state(controller, records):
    # Fresh sidecar is deliberately unenrolled. An already consumed key must
    # fail LocalAPI enrollment; no hostname matching or ready DB injection.
    await controller.request("fixture/agent", {"name": "fresh", "path": "join", "body": records["a"]["enrollment"]}, expected=503)
    status = await controller.agent("fresh", "status", method="GET")
    assert status["BackendState"] != "Running"
