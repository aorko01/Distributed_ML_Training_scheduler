"""Authenticated interactive capacity preview.

Point-in-time, no-store preview. The durable claim transaction in
scheduling.claims remains the only assignment authority.
"""
import hashlib
import logging
from datetime import datetime, timezone

from app.models.worker_model import Worker
from app.models.interactive_runtime_model import WorkerAssignment as Assignment
from app.models.interactive_runtime_model import InteractiveRuntime as Runtime
from app.services.scheduling.config import operator_bounds, operator_defaults
from app.services.scheduling.policy import (
    capability_ineligibility,
    availability_ineligibility,
)
from app.services.scheduling.types import Snapshot, now, utc
from app.schemas.interactive_capacity_schema import ResourceRequirements

logger = logging.getLogger("uvicorn.error")

KIND_LABEL = {
    "batch_training": "batch_training",
    "vram_estimation": "vram_estimation",
    "interactive_access": "interactive_access",
}


def _fresh_workers(db, fresh_seconds=15):
    from datetime import timedelta

    cutoff = now() - timedelta(seconds=fresh_seconds)
    return (
        db.query(Worker)
        .filter(
            Worker.protocol_version == 1,
            Worker.authenticated_heartbeat_at.isnot(None),
            Worker.authenticated_heartbeat_at > cutoff,
        )
        .all()
    )


def _snapshot_for(worker, assignments_by_worker):
    inv = worker.inventory or {}
    live = assignments_by_worker.get(worker.worker_id, [])
    return Snapshot(worker.worker_id, worker.gpu_type, inv.get("free_vram_gb", 0) or 0, len(live), inv)


def _requirements_to_spec(requirements: ResourceRequirements):
    from app.services.scheduling.config import build_launch_spec

    return build_launch_spec(requirements.canonical())


def options_payload(db):
    bounds = operator_bounds()
    defaults_raw = operator_defaults()
    defaults = ResourceRequirements(
        gpu_model=None,
        minimum_vram_gb=float(defaults_raw["minimum_vram_gb"]),
        cpu_cores=float(defaults_raw["cpu"]),
        memory_gb=float(defaults_raw["memory_gb"]),
        disk_gb=int(defaults_raw["disk_gb"]),
    )
    gpu_models: set[str] = set()
    cpu_vals: set[float] = set()
    ram_vals: set[float] = set()
    vram_vals: set[float] = set()
    disk_vals: set[int] = set()
    try:
        workers = _fresh_workers(db)
        for worker in workers:
            inv = worker.inventory or {}
            if inv.get("complete") is not True:
                continue
            if not inv.get("interactive_ready"):
                continue
            for gpu in inv.get("gpus") or []:
                model = gpu.get("model")
                if model:
                    gpu_models.add(str(model)[:128])
                try:
                    mem = float(gpu.get("memory_gb") or 0)
                    if mem > 0:
                        vram_vals.add(round(mem, 2))
                except (TypeError, ValueError):
                    pass
            try:
                cores = inv.get("cpu_cores")
                if cores:
                    cpu_vals.add(float(cores))
            except (TypeError, ValueError):
                pass
            try:
                free_ram = float(inv.get("free_ram_gb") or 0)
                if free_ram > 0:
                    ram_vals.add(round(free_ram, 2))
            except (TypeError, ValueError):
                pass
            try:
                free_disk = float(inv.get("free_disk_gb") or 0)
                if free_disk > 0:
                    disk_vals.add(int(free_disk))
            except (TypeError, ValueError):
                pass
    except Exception:
        logger.exception("interactive_capacity options_inventory_failed")
    allowlist = bounds.get("gpu_models_allowlist") or []
    if allowlist:
        gpu_models = {m for m in gpu_models if m in allowlist} or set(allowlist)

    def _bounded(values, lo, hi, default):
        safe = sorted(v for v in values if lo <= v <= hi)
        if default not in safe:
            safe = sorted(set(safe + [default]))
        return safe[:32]

    return {
        "defaults": defaults,
        "bounds": bounds,
        "gpu_models": sorted(gpu_models)[:64],
        "cpu_choices": _bounded(cpu_vals, bounds["cpu_cores"]["min"], bounds["cpu_cores"]["max"], float(defaults.cpu_cores)),
        "ram_choices": _bounded(ram_vals, bounds["memory_gb"]["min"], bounds["memory_gb"]["max"], float(defaults.memory_gb)),
        "vram_choices": _bounded(vram_vals, bounds["minimum_vram_gb"]["min"], bounds["minimum_vram_gb"]["max"], float(defaults.minimum_vram_gb)),
        "disk_choices": _bounded(disk_vals, bounds["disk_gb"]["min"], bounds["disk_gb"]["max"], int(defaults.disk_gb)),
    }


def _workload_entries(assignments, owner):
    entries = []
    for assignment in assignments:
        kind = assignment.kind
        if kind not in KIND_LABEL:
            continue
        entries.append(
            {
                "kind": KIND_LABEL[kind],
                "state": assignment.state,
                "mine": assignment_state_mine(assignment, owner),
            }
        )
    # Worker-local inventory without a scheduler row is shown generically.
    return entries


def assignment_state_mine(assignment, owner):
    try:
        payload = assignment.payload or {}
        if isinstance(payload, dict) and payload.get("owner_id") == owner:
            return True
        # Batch jobs: look up owner via job_id would be N+1; instead treat
        # runtime-owned rows via runtime owner check done by caller. Fallback:
        return False
    except Exception:
        return False


def preview_payload(db, requirements: ResourceRequirements, owner: str):
    spec = _requirements_to_spec(requirements)
    workers = _fresh_workers(db)
    worker_ids = [w.worker_id for w in workers]
    assignments_by_worker: dict[str, list] = {wid: [] for wid in worker_ids}
    if worker_ids:
        rows = (
            db.query(Assignment)
            .filter(Assignment.worker_id.in_(worker_ids), Assignment.released_at.is_(None))
            .all()
        )
        # Batched single query (no N+1 per card). Annotate mine without leaking
        # other users: runtime owner check in one extra batched query.
        runtime_ids = [r.runtime_id for r in rows if r.runtime_id]
        runtime_owners: dict[str, str] = {}
        if runtime_ids:
            for runtime in db.query(Runtime).filter(Runtime.id.in_(runtime_ids)).all():
                runtime_owners[runtime.id] = runtime.owner_user_id
        job_ids = [r.job_id for r in rows if r.job_id]
        job_owners: dict[str, str] = {}
        if job_ids:
            from app.models.job_model import Job

            for job in db.query(Job).filter(Job.id.in_(job_ids)).all():
                job_owners[job.id] = job.user_id
        for row in rows:
            assignments_by_worker.setdefault(row.worker_id, []).append(row)
            mine = False
            if row.runtime_id and runtime_owners.get(row.runtime_id) == owner:
                mine = True
            if row.job_id and job_owners.get(row.job_id) == owner:
                mine = True
            # Stash without persisting.
            row._preview_mine = mine
    machines = []
    for worker in workers:
        inv = worker.inventory or {}
        if inv.get("complete") is not True:
            continue
        # Only interactive-capable platform/runtime/quota workers.
        if inv.get("platform") != spec["platform"]:
            continue
        if not inv.get("nvidia_runtime") or not inv.get("quota_supported"):
            continue
        snapshot = _snapshot_for(worker, assignments_by_worker)
        if capability_ineligibility(snapshot, spec):
            continue
        avail_reason = availability_ineligibility(snapshot, spec)
        available = avail_reason is None
        live_rows = assignments_by_worker.get(worker.worker_id, [])
        workloads = []
        for row in live_rows:
            if row.kind in KIND_LABEL:
                workloads.append(
                    {"kind": KIND_LABEL[row.kind], "state": row.state, "mine": bool(getattr(row, "_preview_mine", False))}
                )
        if inv.get("local_assignments"):
            workloads.append({"kind": "batch_training", "state": "WORKER_LOCAL", "mine": False})
            available = False
            avail_reason = (avail_reason + ",worker_assignments_active" if avail_reason else "worker_assignments_active")
        gpus = inv.get("gpus") or []
        total_vram = 0.0
        free_vram = float(inv.get("free_vram_gb") or 0)
        primary_model = None
        try:
            total_vram = max([float(g.get("memory_gb") or 0) for g in gpus] or [0.0])
            # Primary display: largest GPU.
            ordered = sorted(gpus, key=lambda g: float(g.get("memory_gb") or 0), reverse=True)
            if ordered:
                primary_model = ordered[0].get("model")
        except (TypeError, ValueError):
            pass
        try:
            cpu_cores = int(inv.get("cpu_cores") or 0)
        except (TypeError, ValueError):
            cpu_cores = 0
        try:
            free_ram = float(inv.get("free_ram_gb") or 0)
        except (TypeError, ValueError):
            free_ram = 0.0
        try:
            free_disk = float(inv.get("free_disk_gb") or 0)
        except (TypeError, ValueError):
            free_disk = 0.0
        total_ram = inv.get("total_ram_gb") or inv.get("total_ram")
        total_disk = inv.get("total_disk_gb") or inv.get("total_disk")
        try:
            total_ram = float(total_ram) if total_ram is not None else None
        except (TypeError, ValueError):
            total_ram = None
        try:
            total_disk = float(total_disk) if total_disk is not None else None
        except (TypeError, ValueError):
            total_disk = None
        display = inv.get("hostname") or getattr(worker, "hostname", None) or worker.worker_id
        key = hashlib.sha256(worker.worker_id.encode()).hexdigest()[:16]
        machines.append(
            {
                "machine_key": key,
                "display_name": str(display)[:256],
                "gpu_model": primary_model,
                "gpu_count": len(gpus),
                "total_vram_gb": float(total_vram),
                "free_vram_gb": float(free_vram),
                "cpu_cores": cpu_cores,
                "cpu_load_percent": inv.get("cpu_load"),
                "total_ram_gb": total_ram,
                "free_ram_gb": float(free_ram),
                "total_disk_gb": total_disk,
                "free_disk_gb": float(free_disk),
                "gpu_load_percent": inv.get("gpu_load"),
                "available_now": bool(available),
                "availability_reason": None if available else (avail_reason or "unavailable"),
                "workloads": workloads,
            }
        )
    machines.sort(key=lambda m: (not m["available_now"], m["display_name"]))
    queued = (
        db.query(Runtime)
        .filter(Runtime.state == "QUEUED", Runtime.desired_state == "RUNNING")
        .count()
    )
    matching = len(machines)
    available_now = sum(1 for m in machines if m["available_now"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "requirements": requirements.canonical(),
        "matching_online": matching,
        "available_now": available_now,
        "busy": matching - available_now,
        "queued_interactive_requests": int(queued),
        "machines": machines,
    }
