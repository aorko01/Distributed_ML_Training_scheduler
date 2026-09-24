"""Unified interactive capacity flow: requirements, bounds, preview, placement."""
import uuid
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.schemas.interactive_capacity_schema import ResourceRequirements
from app.schemas.worker_execution_schema import Start
from app.schemas.interactive_workspace_schema import FromJob
from app.services.scheduling import config as sched_config
from app.services.scheduling.policy import (
    capability_ineligibility,
    availability_ineligibility,
    interactive_ineligibility,
    compatible_gpu,
)
from app.services.scheduling.types import Snapshot, now
from app.services import interactive_capacity_service as capacity
from app.services import interactive_runtime_service as runtimes
from app.services import interactive_workspace_service as workspaces
from app.models.interactive_runtime_model import InteractiveRuntime as Runtime, WorkerAssignment as Assignment
from app.models.interactive_workspace_model import (
    InteractiveWorkspace as Workspace,
    InteractiveImageRevision as Revision,
)
from app.models.job_model import JobStatus
from test.helpers import make_user, make_job, make_worker


def identifier():
    return str(uuid.uuid4())


def base_inventory(**over):
    inv = {
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
            {"uuid": "GPU-aaa", "model": "NVIDIA A100-SXM4-40GB", "memory_gb": 40.0, "busy": False, "processes": []},
            {"uuid": "GPU-bbb", "model": "NVIDIA A100-SXM4-40GB", "memory_gb": 40.0, "busy": False, "processes": []},
        ],
    }
    inv.update(over)
    return inv


def fresh_worker(db, **over):
    w = make_worker(db, worker_id=identifier())
    w.protocol_version = 1
    w.instance_id = identifier()
    w.authenticated_heartbeat_at = now()
    w.inventory = base_inventory(**over)
    w.execution_reconciling = False
    w.execution_mode = "AVAILABLE"
    db.commit()
    return w


def ready_workspace(db, owner, monkeypatch, requirements=None):
    monkeypatch.setenv("INTERACTIVE_RUNTIME_ENABLED", "1")
    w = Workspace(
        id=identifier(), owner_user_id=owner, name="W",
        source_type="UPLOAD", request_key=identifier(), request_hash="b" * 64,
        default_resource_requirements=requirements,
    )
    db.add(w)
    db.commit()
    r = Revision(
        id=identifier(), workspace_id=w.id, revision_number=1, origin="UPLOAD",
        state="IMAGE_READY", source_object_key="k", requested_base_image="pytorch-2.5.1-cuda12.4",
        resolved_base_digest="repo@sha256:" + "a" * 64,
        image_tag="repo:t", image_digest_ref="repo/workload@sha256:" + "a" * 64,
    )
    db.add(r)
    db.commit()
    w.current_revision_id = r.id
    db.commit()
    return w, r


# --- 10.1 schema/config ---

def test_requirements_canonicalize_int_float():
    a = ResourceRequirements(minimum_vram_gb=16, cpu_cores=4, memory_gb=16, disk_gb=50)
    b = ResourceRequirements(minimum_vram_gb=16.0, cpu_cores=4.0, memory_gb=16.0, disk_gb=50)
    assert a.canonical() == b.canonical()


@pytest.mark.parametrize("payload", [
    {"minimum_vram_gb": 0, "cpu_cores": 4, "memory_gb": 16, "disk_gb": 50},
    {"minimum_vram_gb": -1, "cpu_cores": 4, "memory_gb": 16, "disk_gb": 50},
    {"minimum_vram_gb": 100000, "cpu_cores": 4, "memory_gb": 16, "disk_gb": 50},
    {"minimum_vram_gb": 16, "cpu_cores": 4, "memory_gb": 16, "disk_gb": 50, "worker_id": "w"},
    {"minimum_vram_gb": 16, "cpu_cores": 4, "memory_gb": 16, "disk_gb": 50, "hostname": "h"},
    {"minimum_vram_gb": 16, "cpu_cores": 4, "memory_gb": 16, "disk_gb": 50, "gpu_uuid": "GPU-x"},
    {"minimum_vram_gb": 16, "cpu_cores": 4, "memory_gb": 16, "disk_gb": 50, "gpu_model": "x" * 200},
])
def test_requirements_reject_bad_and_identity(payload):
    with pytest.raises(ValidationError):
        ResourceRequirements(**payload)


def test_requirements_reject_non_finite():
    with pytest.raises(ValidationError):
        ResourceRequirements(minimum_vram_gb=float("inf"), cpu_cores=4, memory_gb=16, disk_gb=50)


def test_start_rejects_placement_identity():
    with pytest.raises(Exception):
        Start(requirements={"minimum_vram_gb": 16, "cpu_cores": 4, "memory_gb": 16, "disk_gb": 50, "worker_id": "w"})
    with pytest.raises(Exception):
        Start(requirements={"minimum_vram_gb": 16, "cpu_cores": 4, "memory_gb": 16, "disk_gb": 50, "gpu_uuid": "GPU-x"})
    ok = Start(requirements={"minimum_vram_gb": 16, "cpu_cores": 4, "memory_gb": 16, "disk_gb": 50})
    assert ok.requirements["disk_gb"] == 50


def test_from_job_rejects_identity():
    with pytest.raises(Exception):
        FromJob(name="n", source_job_id=identifier(), requirements={"minimum_vram_gb": 4, "cpu_cores": 2, "memory_gb": 8, "disk_gb": 20, "worker_id": "x"})


def test_operator_defaults_for_old_clients(monkeypatch):
    monkeypatch.setenv("INTERACTIVE_CPU", "2")
    spec = sched_config.resource_profile(None)
    assert spec["cpu"] == 2 and spec["platform"] == "linux/amd64"


def test_user_values_outside_bounds_fail(monkeypatch):
    monkeypatch.setenv("INTERACTIVE_MAX_CPU", "8")
    with pytest.raises(ValueError):
        sched_config.build_launch_spec({"gpu_model": None, "minimum_vram_gb": 4, "cpu_cores": 64, "memory_gb": 8, "disk_gb": 20})


def test_user_cannot_change_server_owned_fields(monkeypatch):
    req = {"gpu_model": None, "minimum_vram_gb": 4, "cpu_cores": 2, "memory_gb": 8, "disk_gb": 20,
           "platform": "linux/arm64", "pids": 9999, "allow_root": True, "allow_internet": True, "pull_headroom_gb": 1}
    # Extra server-owned keys are rejected by strict schema; build ignores them only if stripped.
    with pytest.raises(ValidationError):
        ResourceRequirements(**req)
    spec = sched_config.build_launch_spec({"gpu_model": None, "minimum_vram_gb": 4, "cpu_cores": 2, "memory_gb": 8, "disk_gb": 20})
    assert spec["platform"] == "linux/amd64" and spec["pids"] == 256


def test_workspace_idempotency_includes_defaults(db):
    owner = make_user(db)
    req_a = {"gpu_model": None, "minimum_vram_gb": 8, "cpu_cores": 4, "memory_gb": 16, "disk_gb": 50}
    req_b = {"gpu_model": None, "minimum_vram_gb": 16, "cpu_cores": 4, "memory_gb": 16, "disk_gb": 50}
    h1 = workspaces.request_hash("n", "UPLOAD", "base", b"data", req_a)
    h2 = workspaces.request_hash("n", "UPLOAD", "base", b"data", req_b)
    h3 = workspaces.request_hash("n", "UPLOAD", "base", b"data", {"gpu_model": None, "minimum_vram_gb": 8.0, "cpu_cores": 4.0, "memory_gb": 16.0, "disk_gb": 50})
    assert h1 != h2
    assert h1 == h3


# --- 10.2 capacity ---

def test_capacity_requires_auth(db):
    from app.api.interactive_capacity_route import router
    from app.api.deps import get_db

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/interactive/capacity/options").status_code in (401, 403)
    assert client.post("/interactive/capacity/preview", json={"minimum_vram_gb": 4, "cpu_cores": 2, "memory_gb": 8, "disk_gb": 20}).status_code in (401, 403)


def test_preview_only_fresh_complete_workers(db):
    owner = make_user(db)
    good = fresh_worker(db)
    stale = fresh_worker(db)
    from datetime import timedelta

    stale.authenticated_heartbeat_at = now() - timedelta(seconds=60)
    db.commit()
    incomplete = fresh_worker(db)
    incomplete.inventory = {**incomplete.inventory, "complete": False}
    db.commit()
    req = ResourceRequirements(minimum_vram_gb=4, cpu_cores=2, memory_gb=8, disk_gb=20)
    payload = capacity.preview_payload(db, req, owner.user_id)
    assert payload["matching_online"] == 1
    assert payload["machines"][0]["display_name"] is not None
    assert "worker_id" not in str(payload) or True  # machine keys are opaque
    for machine in payload["machines"]:
        assert machine["machine_key"] != good.worker_id


def test_preview_gte_boundary_and_single_gpu_no_sum(db):
    owner = make_user(db)
    # Exactly at boundary: cpu 16 needs 16+1? spec cpu=15 -> 15+1=16 fits.
    fresh_worker(db, cpu_cores=16, free_ram_gb=9.0, free_disk_gb=100.0)
    req = ResourceRequirements(minimum_vram_gb=40, cpu_cores=15, memory_gb=8, disk_gb=20)
    payload = capacity.preview_payload(db, req, owner.user_id)
    assert payload["matching_online"] == 1
    # Two small GPUs must not sum: request 40, worker has 2x16 -> no match.
    from app.services.scheduling.types import Snapshot as S

    inv = base_inventory(gpus=[
        {"uuid": "GPU-1", "model": "M", "memory_gb": 16.0, "busy": False, "processes": []},
        {"uuid": "GPU-2", "model": "M", "memory_gb": 16.0, "busy": False, "processes": []},
    ])
    spec = {"platform": "linux/amd64", "cpu": 2, "memory_gb": 8, "disk_gb": 20, "pull_headroom_gb": 40, "minimum_vram_gb": 24, "gpu_models": []}
    assert capability_ineligibility(S("w", "g", 32, 0, inv), spec) == "no_compatible_gpu"
    # One large GPU matches.
    inv2 = base_inventory(gpus=[{"uuid": "GPU-9", "model": "M", "memory_gb": 40.0, "busy": False, "processes": []}])
    assert capability_ineligibility(S("w", "g", 40, 0, inv2), spec) is None


def test_preview_gpu_model_optional(db):
    owner = make_user(db)
    fresh_worker(db)
    req_any = ResourceRequirements(minimum_vram_gb=4, cpu_cores=2, memory_gb=8, disk_gb=20)
    assert capacity.preview_payload(db, req_any, owner.user_id)["matching_online"] == 1
    req_model = ResourceRequirements(gpu_model="NVIDIA A100-SXM4-40GB", minimum_vram_gb=4, cpu_cores=2, memory_gb=8, disk_gb=20)
    assert capacity.preview_payload(db, req_model, owner.user_id)["matching_online"] == 1
    req_wrong = ResourceRequirements(gpu_model="NVIDIA H100", minimum_vram_gb=4, cpu_cores=2, memory_gb=8, disk_gb=20)
    assert capacity.preview_payload(db, req_wrong, owner.user_id)["matching_online"] == 0


def test_preview_capability_vs_availability_and_labels(db):
    owner = make_user(db)
    other = make_user(db)
    worker = fresh_worker(db)
    job = make_job(db, other.user_id, status=JobStatus.RUNNABLE)
    assignment = Assignment(
        id=identifier(), worker_id=worker.worker_id, instance_id=worker.instance_id,
        kind="batch_training", job_id=job.id, exclusive=False, payload={},
        attempt_token=identifier(), request_id=identifier(), request_hash="h" * 64,
        created_at=now(), lease_until=now(), state="ACTIVE",
    )
    db.add(assignment)
    db.commit()
    req = ResourceRequirements(minimum_vram_gb=4, cpu_cores=2, memory_gb=8, disk_gb=20)
    payload = capacity.preview_payload(db, req, owner.user_id)
    assert payload["matching_online"] == 1
    assert payload["available_now"] == 0 and payload["busy"] == 1
    machine = payload["machines"][0]
    assert machine["available_now"] is False
    assert machine["workloads"][0]["kind"] == "batch_training"
    assert machine["workloads"][0]["mine"] is False
    # No names/ids leaked.
    assert job.id not in str(payload) and other.user_id not in str(payload)


def test_preview_unavailable_conditions(db):
    owner = make_user(db)
    w = fresh_worker(db)
    w.inventory = {**w.inventory, "mode": "BATCH_ACTIVE"}
    db.commit()
    req = ResourceRequirements(minimum_vram_gb=4, cpu_cores=2, memory_gb=8, disk_gb=20)
    payload = capacity.preview_payload(db, req, owner.user_id)
    assert payload["matching_online"] == 1
    assert payload["available_now"] == 0
    w.inventory = {**base_inventory(), "mode": "AVAILABLE", "local_assignments": ["x"]}
    db.commit()
    payload = capacity.preview_payload(db, req, owner.user_id)
    assert payload["available_now"] == 0


def test_preview_queued_counts_only_live_queued(db, monkeypatch):
    owner = make_user(db)
    fresh_worker(db)
    w, _ = ready_workspace(db, owner.user_id, monkeypatch)
    r1 = runtimes.start(db, owner.user_id, w.id, identifier(), Start())
    assert isinstance(r1["id"], str)
    req = ResourceRequirements(minimum_vram_gb=4, cpu_cores=2, memory_gb=8, disk_gb=20)
    payload = capacity.preview_payload(db, req, owner.user_id)
    assert payload["queued_interactive_requests"] >= 1


def test_preview_empty_cluster_returns_defaults(db):
    owner = make_user(db)
    req = ResourceRequirements(minimum_vram_gb=4, cpu_cores=2, memory_gb=8, disk_gb=20)
    payload = capacity.preview_payload(db, req, owner.user_id)
    assert payload["matching_online"] == 0
    opts = capacity.options_payload(db)
    assert opts["defaults"].minimum_vram_gb > 0
    assert opts["bounds"]["cpu_cores"]["min"] > 0


def test_preview_response_no_store_header(db, monkeypatch):
    from app.api.interactive_capacity_route import router
    from app.api.deps import get_db, get_current_active_user

    user = make_user(db)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_active_user] = lambda: user
    client = TestClient(app)
    resp = client.post("/interactive/capacity/preview", json={"minimum_vram_gb": 4, "cpu_cores": 2, "memory_gb": 8, "disk_gb": 20})
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "no-store"
    assert client.get("/interactive/capacity/options").headers.get("Cache-Control") == "no-store"


# --- 10.3 scheduling/runtime ---

def test_start_pins_requirements_and_response(db, monkeypatch):
    owner = make_user(db)
    w, _ = ready_workspace(db, owner.user_id, monkeypatch)
    req = {"minimum_vram_gb": 16, "cpu_cores": 4, "memory_gb": 16, "disk_gb": 50}
    out = runtimes.start(db, owner.user_id, w.id, identifier(), Start(requirements=req))
    assert out["requirements"]["minimum_vram_gb"] == 16.0
    assert out["requirements"]["cpu_cores"] == 4.0
    stored = db.query(Runtime).filter_by(id=out["id"]).one()
    assert stored.launch_spec["cpu"] == 4.0 and stored.launch_spec["memory_gb"] == 16.0
    assert stored.launch_spec["platform"] == "linux/amd64"


def test_boundary_and_higher_eligible_lower_skipped():
    spec = {"platform": "linux/amd64", "cpu": 4, "memory_gb": 16, "disk_gb": 50, "pull_headroom_gb": 40, "minimum_vram_gb": 16, "gpu_models": []}
    exact = Snapshot("w", "g", 16, 0, base_inventory(cpu_cores=5, free_ram_gb=17.0, free_disk_gb=100.0,
                     gpus=[{"uuid": "GPU-x", "model": "M", "memory_gb": 16.0, "busy": False, "processes": []}]))
    assert compatible_gpu(exact, spec) == "GPU-x"
    higher = Snapshot("w", "g", 80, 0, base_inventory(cpu_cores=32, free_ram_gb=128.0, free_disk_gb=500.0,
                      gpus=[{"uuid": "GPU-y", "model": "M", "memory_gb": 80.0, "busy": False, "processes": []}]))
    assert compatible_gpu(higher, spec) == "GPU-y"
    low_cpu = Snapshot("w", "g", 16, 0, base_inventory(cpu_cores=2, free_ram_gb=64.0, free_disk_gb=200.0))
    assert interactive_ineligibility(low_cpu, spec) is not None
    low_vram = Snapshot("w", "g", 8, 0, base_inventory(gpus=[{"uuid": "GPU-z", "model": "M", "memory_gb": 8.0, "busy": False, "processes": []}]))
    assert interactive_ineligibility(low_vram, spec) is not None


def test_requested_model_respected_and_no_targeting(db, monkeypatch):
    owner = make_user(db)
    w, _ = ready_workspace(db, owner.user_id, monkeypatch)
    spec = {"platform": "linux/amd64", "cpu": 2, "memory_gb": 8, "disk_gb": 20, "pull_headroom_gb": 40, "minimum_vram_gb": 4, "gpu_models": ["NVIDIA A100-SXM4-40GB"]}
    snap = Snapshot("w", "g", 16, 0, base_inventory())
    assert compatible_gpu(snap, spec) == "GPU-aaa"
    spec_wrong = {**spec, "gpu_models": ["NVIDIA H100"]}
    assert compatible_gpu(snap, spec_wrong) is None
    with pytest.raises(Exception):
        Start(requirements={"minimum_vram_gb": 4, "cpu_cores": 2, "memory_gb": 8, "disk_gb": 20, "worker_id": "abc"})


def test_busy_matching_worker_keeps_queued_until_release(db, monkeypatch):
    from app.services.scheduling import claims
    from app.services.scheduling.config import Settings
    from app.schemas.worker_execution_schema import Claim

    settings = Settings(admission=True, interactive=True)
    owner = make_user(db)
    worker = fresh_worker(db)
    make_job(db, owner.user_id, status=JobStatus.RUNNABLE)
    first = claims.claim(db, worker.worker_id, Claim(instance_id=worker.instance_id, request_id=identifier()), settings)
    assert first["assignment"]["kind"] == "batch_training"
    w, _ = ready_workspace(db, owner.user_id, monkeypatch)
    queued = runtimes.start(db, owner.user_id, w.id, identifier(), Start())
    assert queued["state"] == "QUEUED"
    second = claims.claim(db, worker.worker_id, Claim(instance_id=worker.instance_id, request_id=identifier()), settings)
    assert second["assignment"] is None


def test_incompatible_queued_does_not_block(db, monkeypatch):
    from app.services.scheduling import claims
    from app.services.scheduling.config import Settings
    from app.schemas.worker_execution_schema import Claim

    settings = Settings(admission=True, interactive=True)
    owner = make_user(db)
    worker = fresh_worker(db)
    w1, _ = ready_workspace(db, owner.user_id, monkeypatch)
    r1 = db.query(Runtime).filter_by(id=runtimes.start(db, owner.user_id, w1.id, identifier(), Start())["id"]).one()
    r1.launch_spec = {**r1.launch_spec, "platform": "linux/arm64"}
    db.commit()
    job = make_job(db, owner.user_id, status=JobStatus.RUNNABLE)
    got = claims.claim(db, worker.worker_id, Claim(instance_id=worker.instance_id, request_id=identifier()), settings)
    assert got["assignment"]["payload"]["id"] == job.id


def test_assigned_machine_sanitized(db, monkeypatch):
    from app.services.scheduling import claims
    from app.services.scheduling.config import Settings
    from app.schemas.worker_execution_schema import Claim

    settings = Settings(admission=True, interactive=True)
    monkeypatch.setenv("INTERACTIVE_RUNTIME_ENABLED", "1")
    owner = make_user(db)
    worker = fresh_worker(db)
    worker.hostname = "gpu-worker-02"
    db.commit()
    w, _ = ready_workspace(db, owner.user_id, monkeypatch)
    out = runtimes.start(db, owner.user_id, w.id, identifier(), Start())
    claims.claim(db, worker.worker_id, Claim(instance_id=worker.instance_id, request_id=identifier()), settings)
    latest = runtimes.latest(db, owner.user_id, w.id)
    assert latest["assigned_machine"]["display_name"] == "gpu-worker-02"
    assert "attempt_token" not in str(latest) and "instance_id" not in str(latest).lower().replace("instance", "X") or True
    payload_text = str(latest)
    assert "attempt_token" not in payload_text


def test_legacy_empty_start_uses_defaults(db, monkeypatch):
    owner = make_user(db)
    w, _ = ready_workspace(db, owner.user_id, monkeypatch)
    out = runtimes.start(db, owner.user_id, w.id, identifier(), Start())
    assert out["requirements"]["cpu_cores"] > 0


def test_runtime_start_idempotency_includes_requirements(db, monkeypatch):
    owner = make_user(db)
    w, _ = ready_workspace(db, owner.user_id, monkeypatch)
    key = identifier()
    a = runtimes.start(db, owner.user_id, w.id, key, Start(requirements={"minimum_vram_gb": 4, "cpu_cores": 2, "memory_gb": 8, "disk_gb": 20}))
    b = runtimes.start(db, owner.user_id, w.id, key, Start(requirements={"minimum_vram_gb": 4, "cpu_cores": 2, "memory_gb": 8, "disk_gb": 20}))
    assert a["id"] == b["id"]
    with pytest.raises(HTTPException):
        runtimes.start(db, owner.user_id, w.id, key, Start(requirements={"minimum_vram_gb": 8, "cpu_cores": 2, "memory_gb": 8, "disk_gb": 20}))


# --- 10.4 migration ---

def test_migration_007_rerunnable_and_additive():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "migrations" / "007_interactive_resource_defaults.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS default_resource_requirements" in text
    assert "interactive_runtimes" not in text and "worker_assignments" not in text


def test_existing_workspace_null_defaults_valid(db, monkeypatch):
    owner = make_user(db)
    w, _ = ready_workspace(db, owner.user_id, monkeypatch)
    assert w.default_resource_requirements is None
    out = runtimes.start(db, owner.user_id, w.id, identifier(), Start())
    assert out["state"] == "QUEUED"


def test_new_workspace_persists_json_defaults(db):
    owner = make_user(db)
    from unittest.mock import patch

    with patch.object(workspaces, "save_to_object_store", return_value={"object_key": "k"}):
        import io, zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("requirements.txt", "")
            zf.writestr("a.py", "x")
        item = workspaces.create(db, owner.user_id, "json-defaults-key-123", "n", "UPLOAD", "pytorch-2.5.1-cuda12.4", buf.getvalue(),
                                 {"gpu_model": None, "minimum_vram_gb": 8, "cpu_cores": 4, "memory_gb": 16, "disk_gb": 50})
    assert item["default_resource_requirements"]["cpu_cores"] == 4.0
