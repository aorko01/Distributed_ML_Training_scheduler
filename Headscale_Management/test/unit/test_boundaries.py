import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from headscale_management.models import Enrollment
from headscale_management.headscale_client import ControlUnavailable
from headscale_management.reconciliation import Reconciler, verify_policy
from headscale_management.schemas import Claim, Enroll, Probe
from conftest import headers, ready, ticket


def test_required_policy_refuses_broader_role_grants(system):
    actual = Path(system.settings.required_policy_file).read_text()
    verify_policy(actual, system.settings.required_policy_file)
    import json
    policy = json.loads(actual)
    policy["grants"].append({"src": ["tag:interactive-endpoint"], "dst": ["tag:interactive-gateway"], "ip": ["*"]})
    with pytest.raises(ControlUnavailable):
        verify_policy(policy, system.settings.required_policy_file)
    assert system.client.get("/health/ready").status_code == 200


@pytest.mark.asyncio
async def test_concurrent_enrollment_reserves_one_remote_mutation(system):
    service = system.app.state.enrollments
    original = system.headscale.create_key
    calls = []
    async def slow(*args):
        calls.append(True)
        await asyncio.sleep(0.02)
        return await original(*args)
    system.headscale.create_key = slow
    body = Enroll(role="endpoint", identity="r", generation="g")
    result = await asyncio.gather(*(service.enroll(body, "controller", "one") for _ in range(8)), return_exceptions=True)
    assert sum(isinstance(item, dict) for item in result) == 1 and len(calls) == 1
    replay = await service.enroll(body, "controller", "one")
    assert replay["enrollment_id"] == next(item["enrollment_id"] for item in result if isinstance(item, dict))


def test_no_ready_without_fresh_membership_probe_lease(system):
    _, target = ready(system)
    assert ticket(system).status_code == 200
    system.now[0] += 16
    assert ticket(system).status_code == 404
    asyncio.run(Reconciler(system.app.state.enrollments, system.app.state.endpoints).once())
    assert ticket(system).status_code == 404  # membership refresh cannot replace probe
    targets = system.app.state.endpoints.targets("gateway-main", "gateway")
    current = targets[0]
    system.app.state.endpoints.probe("resource-a", Probe(**{k: current[k] for k in ("generation", "version", "probe_id")}, success=True), "gateway")
    assert ticket(system).status_code == 200
    system.now[0] += 61
    assert system.client.post("/internal/v1/resources/resource-a/lease", json={"generation": "g1"}, headers=headers(system)).status_code == 410


def test_admission_expiry_distinct_from_active_deadline(system):
    ready(system)
    request = Claim(ticket=ticket(system).json()["ticket"], request_id=str(uuid4()))
    session = system.app.state.grants.claim(request, "gateway")
    for _ in range(7):
        system.now[0] += 10
        asyncio.run(Reconciler(system.app.state.enrollments, system.app.state.endpoints).once())
        system.app.state.endpoints.lease("resource-a", "g1", "controller")
        target = system.app.state.endpoints.targets("gateway-main", "gateway")[0]
        system.app.state.endpoints.probe("resource-a", Probe(**{k: target[k] for k in ("generation", "version", "probe_id")}, success=True), "gateway")
        system.app.state.grants.renew(session["session_id"], "gateway")
    assert system.app.state.grants.claim(request, "gateway")["session_id"] == session["session_id"]


def test_cleanup_failure_retries_and_late_node_is_removed(system):
    record, _ = ready(system)
    service = system.app.state.enrollments
    system.client.delete("/internal/v1/resources/resource-a", headers=headers(system))
    original = system.headscale.cleanup
    async def fail(*args):
        raise ControlUnavailable()
    system.headscale.cleanup = fail
    reconciler = Reconciler(service, system.app.state.endpoints)
    asyncio.run(reconciler.once())
    with system.database.transaction() as db:
        value = db.get(Enrollment, record["enrollment_id"])
        assert value.state == "REVOKING" and value.retries == 1
    system.headscale.cleanup = original
    system.now[0] += 5
    asyncio.run(reconciler.once())
    system.headscale.join("2")  # late node corresponding to exact revoked key
    system.now[0] += 6
    asyncio.run(reconciler.once())
    assert all(node["key_id"] != "2" for node in system.headscale.records)


@pytest.mark.parametrize("kind", ["grants", "acls"])
def test_ordinary_member_policy_preserves_legacy_connectivity(system, kind):
    import json
    policy = json.loads(Path(system.settings.required_policy_file).read_text())
    policy.setdefault(kind, []).append({"src": ["autogroup:member"],
        "dst": ["autogroup:member:*" if kind == "acls" else "autogroup:member"],
        **({"action": "accept"} if kind == "acls" else {"ip": ["*"]})})
    verify_policy(policy, system.settings.required_policy_file)


@pytest.mark.parametrize("selector", ["*", "100.64.0.0/10", "fd7a:115c:a1e0::/48", "all-nodes", "autogroup:tagged"])
def test_ambiguous_alias_or_address_rule_cannot_broaden_role_policy(system, selector):
    import json
    policy = json.loads(Path(system.settings.required_policy_file).read_text())
    policy["grants"].append({"src": [selector], "dst": [selector], "ip": ["*"]})
    with pytest.raises(ControlUnavailable):
        verify_policy(policy, system.settings.required_policy_file)
