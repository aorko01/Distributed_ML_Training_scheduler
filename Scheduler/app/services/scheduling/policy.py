"""Replaceable selection only. No commits, lifecycle updates or remote calls."""

import os
from datetime import timedelta
from sqlalchemy import exists, or_, select, func
from app.models.job_model import Job, JobStatus
from app.models.worker_model import Worker
from app.models.interactive_runtime_model import (
    InteractiveRuntime as Runtime,
    WorkerAssignment as Assignment,
)
from .types import Candidate, Kind, now, utc


def worker_eligible(worker, timestamp, fresh=15):
    inv = worker.inventory or {}
    return (
        worker.protocol_version == 1
        and worker.authenticated_heartbeat_at is not None
        and utc(worker.authenticated_heartbeat_at)
        > timestamp - timedelta(seconds=fresh)
        and not worker.execution_paused
        and not worker.execution_draining
        and not worker.execution_reconciling
        and not worker.is_testing
        and worker.execution_mode in ("AVAILABLE", "BATCH_ACTIVE")
        and inv.get("complete") is True
        and inv.get("available_slots", 0) > 0
    )


def _total_ram(inv):
    for key in ("total_ram_gb", "total_ram", "totalRam"):
        if inv.get(key) is not None:
            try:
                return float(inv.get(key))
            except (TypeError, ValueError):
                pass
    return None


def _total_disk(inv):
    for key in ("total_disk_gb", "total_disk", "totalDisk"):
        if inv.get(key) is not None:
            try:
                return float(inv.get(key))
            except (TypeError, ValueError):
                pass
    return None


def _gpu_list(inv):
    return inv.get("gpus", []) or []


def _model_matches(gpu, spec):
    models = spec.get("gpu_models") or []
    return (not models) or (gpu.get("model") in models)


def capability_ineligibility(snapshot, spec):
    """Total-hardware check: can this worker ever satisfy the request?"""
    inv = snapshot.inventory
    reasons = []
    if inv.get("platform") != spec["platform"]:
        reasons.append("platform_mismatch")
    if not inv.get("nvidia_runtime"):
        reasons.append("nvidia_runtime_unavailable")
    if not inv.get("quota_supported"):
        reasons.append("disk_quota_unavailable")
    if (inv.get("cpu_cores", 0) or 0) < spec["cpu"] + 1:
        reasons.append("insufficient_cpu")
    capable_ram = _total_ram(inv)
    if capable_ram is None:
        capable_ram = inv.get("free_ram_gb", 0) or 0
    if capable_ram < spec["memory_gb"] + 1:
        reasons.append("insufficient_ram")
    capable_disk = _total_disk(inv)
    if capable_disk is None:
        capable_disk = inv.get("free_disk_gb", 0) or 0
    if capable_disk < spec["disk_gb"] + spec["pull_headroom_gb"]:
        reasons.append("insufficient_disk")
    gpus = _gpu_list(inv)
    if not gpus:
        reasons.append("gpu_inventory_missing")
    elif not any(
        gpu.get("uuid", "").startswith("GPU-")
        and (gpu.get("memory_gb", 0) or 0) >= spec["minimum_vram_gb"]
        and _model_matches(gpu, spec)
        for gpu in gpus
    ):
        reasons.append("no_compatible_gpu")
    return ",".join(reasons) or None


def availability_ineligibility(snapshot, spec):
    """Point-in-time check: is this worker free for interactive work now?"""
    inv = snapshot.inventory
    reasons = []
    if snapshot.assignments:
        reasons.append("scheduler_assignments_active")
    if inv.get("local_assignments"):
        reasons.append("worker_assignments_active")
    if inv.get("mode") != "AVAILABLE":
        reasons.append("worker_mode_" + str(inv.get("mode", "unknown")).lower())
    if not inv.get("interactive_ready"):
        reasons.append("interactive_preflight_not_ready")
    if (inv.get("free_ram_gb", 0) or 0) < spec["memory_gb"] + 1:
        reasons.append("insufficient_ram")
    if (inv.get("cpu_cores", 0) or 0) < spec["cpu"] + 1:
        reasons.append("insufficient_cpu")
    if (inv.get("free_disk_gb", 0) or 0) < spec["disk_gb"] + spec["pull_headroom_gb"]:
        reasons.append("insufficient_disk")
    gpus = _gpu_list(inv)
    if not gpus:
        reasons.append("gpu_inventory_missing")
    elif any(g.get("busy") is not False or g.get("processes") for g in gpus):
        pids = sorted({pid for gpu in gpus for pid in gpu.get("processes", [])})
        reasons.append(
            "gpu_busy" + ((":pids=" + ",".join(map(str, pids))) if pids else "")
        )
    if gpus and not any(
        gpu.get("uuid", "").startswith("GPU-")
        and (gpu.get("memory_gb", 0) or 0) >= spec["minimum_vram_gb"]
        and _model_matches(gpu, spec)
        and gpu.get("busy") is False
        and not gpu.get("processes")
        for gpu in gpus
    ):
        reasons.append("no_compatible_gpu")
    return ",".join(reasons) or None


def interactive_ineligibility(snapshot, spec):
    """Return a stable operator-facing reason when a worker cannot host a runtime."""
    inv = snapshot.inventory
    reasons = []
    if snapshot.assignments:
        reasons.append("scheduler_assignments_active")
    if inv.get("local_assignments"):
        reasons.append("worker_assignments_active")
    if inv.get("mode") != "AVAILABLE":
        reasons.append("worker_mode_" + str(inv.get("mode", "unknown")).lower())
    if not inv.get("interactive_ready"):
        reasons.append("interactive_preflight_not_ready")
    if inv.get("platform") != spec["platform"]:
        reasons.append("platform_mismatch")
    if not inv.get("nvidia_runtime"):
        reasons.append("nvidia_runtime_unavailable")
    if not inv.get("quota_supported"):
        reasons.append("disk_quota_unavailable")
    if (inv.get("free_ram_gb", 0) or 0) < spec["memory_gb"] + 1:
        reasons.append("insufficient_ram")
    if (inv.get("cpu_cores", 0) or 0) < spec["cpu"] + 1:
        reasons.append("insufficient_cpu")
    if (inv.get("free_disk_gb", 0) or 0) < spec["disk_gb"] + spec["pull_headroom_gb"]:
        reasons.append("insufficient_disk")
    gpus = _gpu_list(inv)
    if not gpus:
        reasons.append("gpu_inventory_missing")
    elif any(g.get("busy") is not False or g.get("processes") for g in gpus):
        pids = sorted({pid for gpu in gpus for pid in gpu.get("processes", [])})
        reasons.append(
            "gpu_busy" + ((":pids=" + ",".join(map(str, pids))) if pids else "")
        )
    if gpus and not any(
        gpu.get("uuid", "").startswith("GPU-")
        and (gpu.get("memory_gb", 0) or 0) >= spec["minimum_vram_gb"]
        and _model_matches(gpu, spec)
        for gpu in gpus
    ):
        reasons.append("no_compatible_gpu")
    return ",".join(reasons) or None


def compatible_gpu(snapshot, spec):
    inv = snapshot.inventory
    if interactive_ineligibility(snapshot, spec):
        return None
    for gpu in inv.get("gpus", []):
        if (
            gpu.get("uuid", "").startswith("GPU-")
            and gpu.get("memory_gb", 0) >= spec["minimum_vram_gb"]
            and (not spec["gpu_models"] or gpu.get("model") in spec["gpu_models"])
        ):
            return gpu["uuid"]
    return None


class ThreeTierPolicy:
    def choose(self, db, snapshot, settings):
        assigned = exists().where(
            Assignment.job_id == Job.id, Assignment.released_at.is_(None)
        )
        base = db.query(Job).filter(~assigned)
        # Unavailable high-VRAM workers must not block estimation.
        maximum = snapshot.free_vram
        holds = (
            select(func.count(Assignment.id))
            .where(
                Assignment.worker_id == Worker.worker_id,
                Assignment.released_at.is_(None),
            )
            .correlate(Worker)
            .scalar_subquery()
        )
        workers = db.query(Worker, holds).filter(
            Worker.protocol_version == 1,
            Worker.authenticated_heartbeat_at
            > now() - timedelta(seconds=settings.fresh_seconds),
            Worker.execution_paused.is_(False),
            Worker.execution_draining.is_(False),
            Worker.execution_reconciling.is_(False),
            ~exists().where(
                Assignment.worker_id == Worker.worker_id,
                Assignment.released_at.is_(None),
                or_(
                    Assignment.exclusive.is_(True),
                    Assignment.state.in_(("LOST", "CLEANING")),
                ),
            ),
        )
        for worker, count in workers.yield_per(64):
            inv = worker.inventory or {}
            if (
                count == 0
                and not inv.get("local_assignments")
                and worker_eligible(worker, now(), settings.fresh_seconds)
                and abs(now().timestamp() - inv.get("observed_at", 0))
                <= settings.fresh_seconds
            ):
                maximum = max(maximum, inv.get("free_vram_gb", 0))
        if (
            snapshot.assignments == 0
            and not snapshot.inventory.get("local_assignments")
            and snapshot.free_vram >= maximum
        ):
            job = (
                base.filter(Job.status == JobStatus.VRAM_ESTIMATION_PENDING)
                .order_by(Job.created_at, Job.id)
                .first()
            )
            if job:
                return Candidate(Kind.ESTIMATION, job.id, "vram_estimation")
        if settings.interactive and snapshot.assignments == 0:
            # All starts pin the same configured platform/profile. Bounded query
            # pages skip incompatible profiles without head-of-line blocking.
            queue = (
                db.query(Runtime)
                .filter(Runtime.state == "QUEUED", Runtime.desired_state == "RUNNING")
                .order_by(Runtime.created_at, Runtime.id)
            )
            for runtime in queue.yield_per(64):
                gpu = compatible_gpu(snapshot, runtime.launch_spec)
                if gpu:
                    return Candidate(Kind.INTERACTIVE, runtime.id, gpu_uuid=gpu)
        fits = or_(
            Job.vram_required.is_(None), Job.vram_required + 1 <= snapshot.free_vram
        )
        for status, flag in (
            (JobStatus.RETRY_NEEDED, "retry"),
            (JobStatus.RUNNABLE, "training"),
        ):
            query = base.filter(Job.status == status, fits)
            query = (
                query.order_by(Job.created_at, Job.id)
                if flag == "retry"
                else query.order_by(
                    Job.vram_required.desc().nullslast(), Job.created_at, Job.id
                )
            )
            job = query.first()
            if job:
                return Candidate(Kind.BATCH, job.id, flag)
        return None


def create_policy():
    name = os.getenv("SCHEDULING_POLICY", "three-tier")
    if name != "three-tier":
        raise ValueError("Unknown scheduling policy")
    return ThreeTierPolicy()
