"""Safety and priority tests exercise actual durable claims, not queue mocks."""

from datetime import timedelta
import uuid
import pytest
from fastapi import HTTPException
from app.models.interactive_workspace_model import (
    InteractiveWorkspace as Workspace,
    InteractiveImageRevision as Revision,
)
from app.models.interactive_runtime_model import (
    InteractiveRuntime as Runtime,
    WorkerAssignment as Assignment,
)
from app.models.job_model import JobStatus
from app.schemas.worker_execution_schema import Claim, Fence, Result, Start, Event
from app.services import interactive_runtime_service as runtimes
from app.services.scheduling import claims
from app.services.scheduling.config import Settings
from app.services.scheduling.types import now, Kind, Candidate
from test.helpers import make_user, make_worker, make_job

SETTINGS = Settings(admission=True, interactive=True)


def identifier():
    return str(uuid.uuid4())


def inventory():
    return {
        "complete": True,
        "observed_at": now().timestamp(),
        "mode": "AVAILABLE",
        "available_slots": 2,
        "local_assignments": [],
        "free_vram_gb": 16.0,
        "free_ram_gb": 64.0,
        "free_disk_gb": 200.0,
        "cpu_cores": 16,
        "platform": "linux/amd64",
        "nvidia_runtime": True,
        "quota_supported": True,
        "interactive_ready": True,
        "gpus": [
            {
                "uuid": "GPU-test",
                "model": "Test",
                "memory_gb": 16.0,
                "busy": False,
                "processes": [],
            }
        ],
    }


def worker(db, **changes):
    value = make_worker(db, worker_id=identifier())
    value.protocol_version = 1
    value.instance_id = identifier()
    value.authenticated_heartbeat_at = now()
    value.inventory = inventory()
    value.execution_reconciling = False
    value.execution_mode = "AVAILABLE"
    for key, value2 in changes.items():
        setattr(value, key, value2)
    db.commit()
    return value


def workspace(db, owner, monkeypatch):
    monkeypatch.setenv("INTERACTIVE_RUNTIME_ENABLED", "1")
    w = Workspace(
        id=identifier(),
        owner_user_id=owner,
        name="Test",
        source_type="UPLOAD",
        request_key=identifier(),
        request_hash="a" * 64,
    )
    db.add(w)
    db.commit()
    r = Revision(
        id=identifier(),
        workspace_id=w.id,
        revision_number=1,
        origin="UPLOAD",
        state="IMAGE_READY",
        source_object_key="test.zip",
        requested_base_image="test",
        resolved_base_digest="repo@sha256:" + "a" * 64,
        image_tag="repo:test",
        image_digest_ref="repo/workload@sha256:" + "a" * 64,
    )
    db.add(r)
    db.commit()
    w.current_revision_id = r.id
    db.commit()
    runtime = runtimes.start(db, owner, w.id, identifier(), Start())
    return w, db.get(Runtime, runtime["id"])


def pull(db, w, request=None, policy=None):
    return claims.claim(
        db,
        w.worker_id,
        request or Claim(instance_id=w.instance_id, request_id=identifier()),
        SETTINGS,
        policy,
    )["assignment"]


def fence(assignment):
    return Fence(
        **{
            k: assignment[k]
            for k in ("instance_id", "assignment_id", "attempt_token", "generation")
        }
    )


def test_order_and_cleanup_hold(db, monkeypatch):
    owner = make_user(db)
    w = worker(db)
    workspace_id, runtime = workspace(db, owner.user_id, monkeypatch)
    estimate = make_job(db, owner.user_id, status=JobStatus.VRAM_ESTIMATION_PENDING)
    retry = make_job(db, owner.user_id, status=JobStatus.RETRY_NEEDED)
    make_job(db, owner.user_id, status=JobStatus.RUNNABLE)
    first = pull(db, w)
    assert first["kind"] == "vram_estimation"
    claims.result(
        db,
        w.worker_id,
        Result(
            **fence(first).model_dump(),
            outcome="estimation",
            vram_required=2.0,
            ram_required=2.0,
            step_time=1.0
        ),
    )
    assert db.get(Assignment, first["assignment_id"]).released_at is None
    assert pull(db, w) is None  # Cleaning prevents an idle window.
    claims.cleanup(db, w.worker_id, fence(first))
    assert estimate.status == JobStatus.RUNNABLE
    second = pull(db, w)
    assert second["kind"] == "interactive_access"
    assert retry.status == JobStatus.RETRY_NEEDED
    for phase in ("PULLING", "STARTING", "CONNECTING"):
        seq = ("PULLING", "STARTING", "CONNECTING").index(phase) + 1
        claims.event(
            db,
            w.worker_id,
            Event(**fence(second).model_dump(), sequence=seq, phase=phase),
        )
        assert pull(db, w) is None
    runtimes.stop(db, owner.user_id, runtime.id)
    assert claims.cleanup(db, w.worker_id, fence(second)) == {"released": False}
    assert pull(db, w) is None
    runtime.management_revoked = True
    db.commit()
    assert claims.cleanup(db, w.worker_id, fence(second)) == {"released": True}
    third = pull(db, w)
    assert third["payload"]["id"] == retry.id


def test_incompatible_interactive_does_not_block_batch(db, monkeypatch):
    owner = make_user(db)
    w = worker(db)
    _, runtime = workspace(db, owner.user_id, monkeypatch)
    runtime.launch_spec = {**runtime.launch_spec, "platform": "linux/arm64"}
    db.commit()
    job = make_job(db, owner.user_id, status=JobStatus.RUNNABLE)
    assert pull(db, w)["payload"]["id"] == job.id


def test_batch_reservation_prevents_interactive_on_free_second_gpu(db, monkeypatch):
    owner = make_user(db)
    w = worker(db)
    make_job(db, owner.user_id, status=JobStatus.RUNNABLE)
    first = pull(db, w)
    _, runtime = workspace(db, owner.user_id, monkeypatch)
    assert pull(db, w) is None
    assert runtime.state == "QUEUED" and first["kind"] == "batch_training"


def test_estimation_keeps_other_worker_slots_idle_until_cleanup(db):
    owner = make_user(db)
    w = worker(db)
    w.inventory = {**w.inventory, "available_slots": 3}
    db.commit()
    first_estimate = make_job(
        db, owner.user_id, status=JobStatus.VRAM_ESTIMATION_PENDING
    )
    second_estimate = make_job(
        db, owner.user_id, status=JobStatus.VRAM_ESTIMATION_PENDING
    )
    first_batch = make_job(db, owner.user_id, status=JobStatus.RUNNABLE)
    second_batch = make_job(db, owner.user_id, status=JobStatus.RUNNABLE)

    estimation = pull(db, w)

    assert estimation["kind"] == "vram_estimation"
    assert estimation["payload"]["id"] in {
        first_estimate.id,
        second_estimate.id,
    }
    assert pull(db, w) is None
    claims.result(
        db,
        w.worker_id,
        Result(
            **fence(estimation).model_dump(),
            outcome="estimation",
            vram_required=2.0,
            ram_required=2.0,
            step_time=1.0,
        ),
    )
    assert pull(db, w) is None
    claims.cleanup(db, w.worker_id, fence(estimation))

    next_estimation = pull(db, w)
    assert next_estimation["kind"] == "vram_estimation"
    assert next_estimation["payload"]["id"] in {
        first_estimate.id,
        second_estimate.id,
    } - {estimation["payload"]["id"]}
    assert pull(db, w) is None
    assert first_batch.status == JobStatus.RUNNABLE
    assert second_batch.status == JobStatus.RUNNABLE


def test_estimation_waits_for_existing_batch_to_finish(db):
    owner = make_user(db)
    w = worker(db)
    batch = make_job(db, owner.user_id, status=JobStatus.RUNNABLE)
    running = pull(db, w)
    estimate = make_job(db, owner.user_id, status=JobStatus.VRAM_ESTIMATION_PENDING)

    assert running["payload"]["id"] == batch.id
    assert pull(db, w) is None
    assert estimate.status == JobStatus.VRAM_ESTIMATION_PENDING


def test_active_estimation_worker_does_not_block_another_worker(db):
    owner = make_user(db)
    high_vram_worker = worker(db)
    high_vram_worker.inventory = {
        **high_vram_worker.inventory,
        "free_vram_gb": 80.0,
    }
    db.commit()
    first = make_job(db, owner.user_id, status=JobStatus.VRAM_ESTIMATION_PENDING)
    second = make_job(db, owner.user_id, status=JobStatus.VRAM_ESTIMATION_PENDING)

    first_assignment = pull(db, high_vram_worker)
    assert first_assignment["payload"]["id"] in {first.id, second.id}

    other_worker = worker(db)
    assigned = pull(db, other_worker)
    assert assigned["kind"] == "vram_estimation"
    assert {first_assignment["payload"]["id"], assigned["payload"]["id"]} == {
        first.id,
        second.id,
    }


@pytest.mark.parametrize("unavailable", ["full", "cleanup", "stale_inventory"])
def test_unavailable_high_vram_worker_does_not_block_estimation(db, unavailable):
    owner = make_user(db)
    high = worker(db)
    make_job(db, owner.user_id, status=JobStatus.RUNNABLE)
    held = pull(db, high)
    high.inventory = {**high.inventory, "free_vram_gb": 80.0}
    if unavailable == "full":
        high.inventory = {**high.inventory, "available_slots": 1}
    elif unavailable == "cleanup":
        db.get(Assignment, held["assignment_id"]).state = "CLEANING"
    else:
        high.inventory = {**high.inventory, "observed_at": now().timestamp() - 60}
    db.commit()
    available = worker(db)
    job = make_job(db, owner.user_id, status=JobStatus.VRAM_ESTIMATION_PENDING)
    assigned = pull(db, available)
    assert assigned["kind"] == "vram_estimation" and assigned["payload"]["id"] == job.id


def test_lost_claim_and_released_key_never_mints_replacement(db):
    owner = make_user(db)
    w = worker(db)
    make_job(db, owner.user_id, status=JobStatus.RUNNABLE)
    key = Claim(instance_id=w.instance_id, request_id=identifier())
    first = pull(db, w, key)
    assert pull(db, w, key)["assignment_id"] == first["assignment_id"]
    claims.cleanup(db, w.worker_id, fence(first))
    make_job(db, owner.user_id, status=JobStatus.RUNNABLE)
    assert pull(db, w, key) is None
    assert db.query(Assignment).count() == 1


def test_expired_lease_holds_capacity_and_old_instance_can_only_cleanup(
    db, monkeypatch
):
    owner = make_user(db)
    w = worker(db)
    _, runtime = workspace(db, owner.user_id, monkeypatch)
    assigned = pull(db, w)
    a = db.get(Assignment, assigned["assignment_id"])
    a.lease_until = now() - timedelta(seconds=1)
    db.commit()
    claims.expire(db)
    assert a.state == "LOST" and runtime.state == "LOST"
    assert pull(db, w) is None
    with pytest.raises(HTTPException):
        claims.event(
            db,
            w.worker_id,
            Event(**fence(assigned).model_dump(), sequence=1, phase="STARTING"),
        )
    w.instance_id = identifier()
    db.commit()
    assert claims.cleanup(db, w.worker_id, fence(assigned)) == {"released": False}
    runtime.management_revoked = True
    db.commit()
    assert claims.cleanup(db, w.worker_id, fence(assigned)) == {"released": True}


def test_start_owner_idempotency_and_pinning(db, monkeypatch):
    owner = make_user(db)
    other = make_user(db)
    w, runtime = workspace(db, owner.user_id, monkeypatch)
    same = runtimes.start(db, owner.user_id, w.id, runtime.request_key, Start())
    assert same["id"] == runtime.id
    with pytest.raises(HTTPException) as exc:
        runtimes.start(
            db,
            owner.user_id,
            w.id,
            runtime.request_key,
            Start(revision_id=identifier()),
        )
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        runtimes.stop(db, other.user_id, runtime.id)
    assert exc.value.status_code == 404
    before = db.get(Revision, runtime.revision_id).image_digest_ref
    runtimes.stop(db, owner.user_id, runtime.id)
    assert (
        runtime.state == "STOPPED"
        and db.get(Revision, runtime.revision_id).image_digest_ref == before
    )
    next_runtime = runtimes.start(db, owner.user_id, w.id, identifier(), Start())
    assert next_runtime["generation"] == runtime.generation + 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_paused", True),
        ("execution_draining", True),
        ("execution_reconciling", True),
        ("is_testing", True),
        ("protocol_version", None),
    ],
)
def test_guards_run_before_estimation(db, field, value):
    owner = make_user(db)
    w = worker(db, **{field: value})
    make_job(db, owner.user_id, status=JobStatus.VRAM_ESTIMATION_PENDING)
    assert pull(db, w) is None


def test_custom_policy_keeps_reservation_and_exclusivity(db, monkeypatch):
    owner = make_user(db)
    w = worker(db)
    _, runtime = workspace(db, owner.user_id, monkeypatch)
    make_job(db, owner.user_id, status=JobStatus.VRAM_ESTIMATION_PENDING)

    class Policy:
        def choose(self, db, snapshot, settings):
            return Candidate(Kind.INTERACTIVE, runtime.id, gpu_uuid="GPU-test")

    assigned = pull(db, w, policy=Policy())
    assert assigned["kind"] == "interactive_access"

    class Bypass:
        def choose(self, *args):
            raise AssertionError("Exclusive guard must run first")

    assert pull(db, w, policy=Bypass()) is None


def test_legacy_results_and_placement_fail_closed(db):
    from app.services import job_service

    owner = make_user(db)
    w = worker(db)
    job = make_job(db, owner.user_id, status=JobStatus.RUNNABLE)
    assigned = pull(db, w)
    for function, args in [
        (job_service.set_to_completed, (db, job.id)),
        (job_service.save_vram_estimation, (db, job.id, 1, 1, 1)),
        (job_service.set_job_runnable, (db, job.id)),
        (job_service.mark_job_failed, (db, job.id, "system")),
    ]:
        with pytest.raises(HTTPException):
            function(*args)
    assert db.get(Assignment, assigned["assignment_id"]).released_at is None


def test_stop_during_grant_revokes_late_ticket(db, monkeypatch):
    owner = make_user(db)
    w = worker(db)
    _, runtime = workspace(db, owner.user_id, monkeypatch)
    pull(db, w)
    runtime.state = "READY"
    runtime.health_at = now()
    runtime.health = {k: True for k in ("workload", "broker", "access", "endpoint")}
    db.commit()
    monkeypatch.setenv("INTERACTIVE_GATEWAY_WSS_ORIGIN", "wss://gateway.example.test")
    monkeypatch.setenv("INTERACTIVE_GATEWAY_ID", "gateway")
    calls = []

    class Management:
        def call(self, method, path, body=None):
            calls.append((method, path))
            if method == "POST":
                runtimes.stop(db, owner.user_id, runtime.id)
                return {"grant_id": "grant", "ticket": "private", "expires_at": "later"}

    with pytest.raises(HTTPException):
        runtimes.connection(db, owner.user_id, runtime.id, Management())
    assert ("DELETE", "access-grants/grant") in calls


def test_controller_ready_requires_exact_management_ready_and_health(db, monkeypatch):
    from app.services import interactive_controller as controller

    owner = make_user(db)
    w = worker(db)
    _, runtime = workspace(db, owner.user_id, monkeypatch)
    pull(db, w)
    runtime.state = "CONNECTING"
    runtime.enrollment_id = "enrollment"
    runtime.enrollment_started = True
    runtime.health_at = now()
    runtime.health = {k: True for k in ("workload", "broker", "access", "endpoint")}
    db.commit()

    class Management:
        def call(self, method, path, body=None):
            return {"state": "READY", "version": "exact-version"}

    assert controller.reconcile_one(db, Management())
    assert runtime.state == "READY" and runtime.endpoint_version == "exact-version"
    assert runtime.ready_at


def test_management_outage_holds_cleanup_and_no_enrollment_needs_no_network(
    db, monkeypatch
):
    from app.services import interactive_controller as controller

    owner = make_user(db)
    w = worker(db)
    _, runtime = workspace(db, owner.user_id, monkeypatch)
    assigned = pull(db, w)
    runtimes.stop(db, owner.user_id, runtime.id)
    claims.cleanup(db, w.worker_id, fence(assigned))

    class Unavailable:
        def call(self, *args):
            raise HTTPException(503, "unavailable")

    assert controller.reconcile_one(db, Unavailable())
    assert (
        runtime.state == "STOPPED"
        and db.get(Assignment, assigned["assignment_id"]).released_at
    )


def test_enrolled_outage_never_releases_host(db, monkeypatch):
    from app.services import interactive_controller as controller

    owner = make_user(db)
    w = worker(db)
    _, runtime = workspace(db, owner.user_id, monkeypatch)
    assigned = pull(db, w)
    runtime.enrollment_started = True
    runtime.enrollment_id = "exact-enrollment"
    db.commit()
    runtimes.stop(db, owner.user_id, runtime.id)
    claims.cleanup(db, w.worker_id, fence(assigned))

    class Unavailable:
        def call(self, *args):
            raise HTTPException(503, "unavailable")

    controller.reconcile_one(db, Unavailable())
    assert (
        runtime.state == "STOPPING"
        and not db.get(Assignment, assigned["assignment_id"]).released_at
    )


def test_bootstrap_persists_id_and_does_not_store_key(db, monkeypatch):
    from app.services import interactive_controller as controller

    owner = make_user(db)
    w = worker(db)
    _, runtime = workspace(db, owner.user_id, monkeypatch)
    assigned = pull(db, w)
    claims.event(
        db,
        w.worker_id,
        Event(**fence(assigned).model_dump(), sequence=1, phase="STARTING"),
    )
    calls = []

    class Management:
        def call(self, method, path, body=None, key=None):
            calls.append((method, path, key))
            if method == "GET":
                return {"state": "CONFIRMED"}
            return {
                "enrollment_id": "exact-enrollment",
                "key": "private-enrollment-key",
            }

    response = controller.bootstrap(db, w.worker_id, fence(assigned), Management())
    assert (
        response["key"] == "private-enrollment-key"
        and runtime.enrollment_id == "exact-enrollment"
    )
    assert "private-enrollment-key" not in str(runtime.__dict__)
    assert controller.bootstrap(db, w.worker_id, fence(assigned), Management()) == {
        "already_enrolled": True
    }
    assert sum(c[0] == "POST" for c in calls) == 1


def test_stop_while_bootstrap_inflight_revokes_exact_enrollment(db, monkeypatch):
    from app.services import interactive_controller as controller

    owner = make_user(db)
    w = worker(db)
    _, runtime = workspace(db, owner.user_id, monkeypatch)
    assigned = pull(db, w)
    claims.event(
        db,
        w.worker_id,
        Event(**fence(assigned).model_dump(), sequence=1, phase="STARTING"),
    )
    calls = []

    class Management:
        def call(self, method, path, body=None, key=None):
            calls.append((method, path))
            if method == "POST":
                runtimes.stop(db, owner.user_id, runtime.id)
                return {"enrollment_id": "late-enrollment", "key": "private-key"}

    with pytest.raises(HTTPException):
        controller.bootstrap(db, w.worker_id, fence(assigned), Management())
    assert ("DELETE", "enrollments/late-enrollment") in calls
    assert runtime.desired_state == "STOPPED"
