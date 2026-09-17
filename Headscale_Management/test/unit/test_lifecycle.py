import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4
from fastapi import HTTPException
import pytest
from sqlalchemy import select
from headscale_management.headscale_client import UnknownResult
from headscale_management.models import Enrollment
from headscale_management.reconciliation import Reconciler
from headscale_management.schemas import Claim
from conftest import enroll, headers, ready, ticket


def test_import_does_not_create_schema(tmp_path):
    from headscale_management.database import Database
    Database("sqlite:///" + str(tmp_path / "absent.sqlite"))
    assert not (tmp_path / "absent.sqlite").exists()


@pytest.mark.parametrize("changes", [{"ports": (22,)}, {"admission_ttl": 61}, {"enrollment_ttl": 0},
    {"session_lease": 1801}, {"ranges": ("127.0.0.0/8",)}, {"gateway_secret": "x"},
    {"headscale_url": "http://headscale.invalid"}, {"database_url": "sqlite:///:memory:"}])
def test_configuration_fail_closed(system, changes):
    with pytest.raises(ValueError):
        replace(system.settings, **changes)


def test_enrollment_replay_conflict_encryption_redaction(system):
    body = {"role": "endpoint", "identity": "resource-a", "generation": "g1"}
    first = system.client.post("/internal/v1/enrollments", json=body, headers=headers(system, idempotency="one"))
    replay = system.client.post("/internal/v1/enrollments", json=body, headers=headers(system, idempotency="one"))
    assert first.json() == replay.json()
    assert len(system.headscale.keys) == 1
    body["generation"] = "g2"
    assert system.client.post("/internal/v1/enrollments", json=body, headers=headers(system, idempotency="one")).status_code == 409
    enrollment_id = first.json()["enrollment_id"]
    with system.database.transaction() as db:
        record = db.get(Enrollment, enrollment_id)
        assert first.json()["key"] not in record.ciphertext
    status = system.client.get("/internal/v1/enrollments/" + enrollment_id, headers=headers(system)).json()
    assert "key" not in status and "ciphertext" not in status
    assert system.headscale.keys[0]["tags"] == ["tag:interactive-endpoint"]
    assert system.headscale.keys[0]["ephemeral"] is True


def test_membership_requires_key_tags_and_tailnet_address(system):
    response = system.client.post("/internal/v1/enrollments", json={"role": "endpoint", "identity": "r", "generation": "g"},
        headers=headers(system, idempotency="r")).json()
    system.headscale.join("1")
    record = system.headscale.records[0]
    endpoint = "/internal/v1/enrollments/" + response["enrollment_id"] + "/confirm"
    record["key_id"] = "999"
    assert system.client.post(endpoint, json={"generation": "g"}, headers=headers(system)).status_code == 409
    record["key_id"], record["tags"] = "1", ["tag:interactive-gateway"]
    assert system.client.post(endpoint, json={"generation": "g"}, headers=headers(system)).status_code == 409
    record["tags"], record["ips"] = ["tag:interactive-endpoint"], ["172.18.0.4"]
    assert system.client.post(endpoint, json={"generation": "g"}, headers=headers(system)).status_code == 422


def test_role_matrix_unknown_fields(system):
    for actor in ("gateway", "bootstrap"):
        response = system.client.post("/internal/v1/access-grants", json={"user": "u", "resource_id": "r", "generation": "g",
            "service": "echo", "gateway_id": "gateway-main", "authorized": True}, headers=headers(system, actor))
        assert response.status_code == 403
    assert system.client.post("/internal/v1/enrollments", json={"role": "endpoint", "identity": "r", "generation": "g",
        "tags": ["tag:interactive-gateway"]}, headers=headers(system, idempotency="extra")).status_code == 422
    enrollment = enroll(system)
    assert system.client.get("/internal/v1/enrollments/" + enrollment["enrollment_id"], headers=headers(system, "gateway")).status_code == 403
    assert system.client.delete("/internal/v1/enrollments/" + enrollment["enrollment_id"], headers=headers(system, "gateway")).status_code == 403


def test_ambiguous_creation_never_retries(system):
    system.headscale.error = UnknownResult()
    body = {"role": "endpoint", "identity": "r", "generation": "g"}
    for _ in range(2):
        assert system.client.post("/internal/v1/enrollments", json=body, headers=headers(system, idempotency="unknown")).status_code == 503
    with system.database.transaction() as db:
        assert db.scalar(select(Enrollment)).state == "UNKNOWN_RESULT"
    system.now[0] += 301
    asyncio.run(Reconciler(system.app.state.enrollments, system.app.state.endpoints).once())
    with system.database.transaction() as db:
        assert db.scalar(select(Enrollment)).state == "EXPIRED"


def test_owner_fencing_local_revocation_exact_cleanup(system):
    enrollment, _ = ready(system)
    assert ticket(system, user="user-b").status_code == 403
    assert ticket(system, generation="old").status_code == 409
    issued = ticket(system).json()
    session = system.app.state.grants.claim(Claim(ticket=issued["ticket"], request_id=str(uuid4())), "gateway")
    assert system.client.delete("/internal/v1/resources/resource-a", headers=headers(system)).status_code == 200
    assert ticket(system).status_code == 410
    assert system.headscale.cleaned == []
    with pytest.raises(HTTPException) as error:
        system.app.state.grants.renew(session["session_id"], "gateway")
    assert error.value.status_code == 410
    asyncio.run(Reconciler(system.app.state.enrollments, system.app.state.endpoints).once())
    assert system.headscale.cleaned[-1][1] == ["2"]
    assert system.headscale.records[0]["id"] == "1"
    with system.database.transaction() as db:
        assert db.get(Enrollment, enrollment["enrollment_id"]).ciphertext is None


def test_replacement_fences_old_confirm_probe_live_session(system):
    old, target = ready(system)
    issued = ticket(system).json()
    session = system.app.state.grants.claim(Claim(ticket=issued["ticket"], request_id=str(uuid4())), "gateway")
    ready(system, generation="g2")
    assert system.client.post("/internal/v1/enrollments/" + old["enrollment_id"] + "/confirm",
        json={"generation": "g1"}, headers=headers(system)).status_code == 409
    assert system.client.post("/internal/v1/resources/resource-a/probe-result", json={key: target[key]
        for key in ("generation", "version", "probe_id")} | {"success": True}, headers=headers(system, "gateway")).status_code == 409
    assert ticket(system, generation="g2").status_code == 200
    with pytest.raises(HTTPException):
        system.app.state.grants.renew(session["session_id"], "gateway")


def test_atomic_claim_retry_restart(system):
    ready(system)
    issued = ticket(system).json()
    def claim(request_id):
        try:
            return system.app.state.grants.claim(Claim(ticket=issued["ticket"], request_id=request_id), "gateway")
        except HTTPException as error:
            return error.status_code
    ids = [str(uuid4()) for _ in range(12)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(claim, ids))
    winners = [value for value in results if isinstance(value, dict)]
    assert len(winners) == 1
    winner_index = next(i for i, value in enumerate(results) if isinstance(value, dict))
    assert claim(ids[winner_index])["session_id"] == winners[0]["session_id"]
    from headscale_management.database import Database
    from headscale_management.enrollment_service import EnrollmentService
    from headscale_management.endpoint_service import EndpointService
    from headscale_management.grant_service import GrantService
    restarted = GrantService(EndpointService(EnrollmentService(system.settings, Database(system.settings.database_url), system.headscale, lambda: system.now[0])))
    assert restarted.claim(Claim(ticket=issued["ticket"], request_id=ids[winner_index]), "gateway")["session_id"] == winners[0]["session_id"]
    with pytest.raises(HTTPException):
        restarted.claim(Claim(ticket=issued["ticket"], request_id=str(uuid4())), "gateway")
