"""Worker-first lock order and one durable reservation for every launch."""

import hashlib
import json
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from app.models.worker_model import Worker
from app.models.job_model import Job, JobStatus
from app.models.interactive_runtime_model import (
    InteractiveRuntime as Runtime,
    WorkerAssignment as Assignment,
)
from app.models.interactive_workspace_model import new_id
from .types import Snapshot, Kind, now, utc
from .config import Settings
from .policy import create_policy, worker_eligible, compatible_gpu


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def lock_worker(db, worker_id):
    worker = db.query(Worker).filter_by(worker_id=worker_id).with_for_update().first()
    if not worker:
        raise HTTPException(404, "Worker not registered")
    return worker


def live(db, worker_id):
    return db.query(Assignment).filter_by(worker_id=worker_id, released_at=None).all()


def envelope(assignment, settings):
    if assignment.released_at:
        return {
            "assignment": None,
            "request_state": "released",
            "retry_after_seconds": 5,
        }
    if assignment.state in ("LOST", "CLEANING") or utc(assignment.lease_until) <= now():
        return {"assignment": None, "request_state": "stale", "retry_after_seconds": 5}
    return {
        "assignment": {
            "protocol_version": 1,
            "kind": assignment.kind,
            "assignment_id": assignment.id,
            "attempt_token": assignment.attempt_token,
            "instance_id": assignment.instance_id,
            "generation": assignment.generation,
            "lease_seconds": max(
                0,
                min(
                    settings.lease_seconds,
                    (utc(assignment.lease_until) - now()).total_seconds(),
                ),
            ),
            "lease_until": assignment.lease_until,
            "payload": assignment.payload,
        },
        "retry_after_seconds": 5,
    }


def claim(db, worker_id, body, settings=None, policy=None):
    settings, policy = settings or Settings.from_env(), policy or create_policy()
    request_hash = digest(body.model_dump())
    for _ in range(3):
        try:
            worker = lock_worker(db, worker_id)
            previous = (
                db.query(Assignment)
                .filter_by(
                    worker_id=worker_id,
                    instance_id=body.instance_id,
                    request_id=body.request_id,
                )
                .first()
            )
            if previous:
                if previous.request_hash != request_hash:
                    raise HTTPException(409, "Claim request conflict")
                result = envelope(previous, settings)
                db.commit()
                return result
            active = live(db, worker_id)
            if (
                not settings.admission
                or worker.instance_id != body.instance_id
                or not worker_eligible(worker, now(), settings.fresh_seconds)
                or any(a.exclusive or a.state in ("LOST", "CLEANING") for a in active)
            ):
                db.commit()
                return {"assignment": None, "retry_after_seconds": 5}
            inv = worker.inventory
            if len(active) >= inv["available_slots"] + len(inv["local_assignments"]):
                db.commit()
                return {"assignment": None, "retry_after_seconds": 5}
            if abs(now().timestamp() - inv["observed_at"]) > settings.fresh_seconds:
                db.commit()
                return {"assignment": None, "retry_after_seconds": 5}
            snapshot = Snapshot(
                worker_id, worker.gpu_type, inv["free_vram_gb"], len(active), inv
            )
            candidate = policy.choose(db, snapshot, settings)
            if candidate is None:
                db.commit()
                return {"assignment": None, "retry_after_seconds": 5}
            assignment_id, token, timestamp = new_id(), new_id(), now()
            if candidate.kind == Kind.INTERACTIVE:
                runtime = (
                    db.query(Runtime)
                    .filter_by(
                        id=candidate.target_id, state="QUEUED", desired_state="RUNNING"
                    )
                    .with_for_update(skip_locked=True)
                    .first()
                )
                if (
                    not runtime
                    or not settings.interactive
                    or compatible_gpu(snapshot, runtime.launch_spec)
                    != candidate.gpu_uuid
                ):
                    db.rollback()
                    continue
                runtime.state, runtime.assignment_id, runtime.resource_id = (
                    "ASSIGNED",
                    assignment_id,
                    assignment_id,
                )
                runtime.assigned_at, runtime.startup_deadline = (
                    timestamp,
                    timestamp + timedelta(seconds=settings.startup_seconds),
                )
                if settings.lifetime_seconds:
                    runtime.lifetime_deadline = timestamp + timedelta(
                        seconds=settings.lifetime_seconds
                    )
                payload = {
                    "runtime_id": runtime.id,
                    "workspace_id": runtime.workspace_id,
                    "revision_id": runtime.revision_id,
                    "owner_id": runtime.owner_user_id,
                    "generation": runtime.generation,
                    "image_digest_ref": runtime.image_digest_ref,
                    "gpu_uuid": candidate.gpu_uuid,
                    "launch_spec": runtime.launch_spec,
                }
                # Circular FK: insert reservation before setting runtime pointer.
                runtime.assignment_id = None
                db.flush()
                assignment = Assignment(
                    id=assignment_id,
                    worker_id=worker_id,
                    instance_id=body.instance_id,
                    kind=candidate.kind.value,
                    runtime_id=runtime.id,
                    generation=runtime.generation,
                    exclusive=True,
                    gpu_uuid=candidate.gpu_uuid,
                    payload=payload,
                    attempt_token=token,
                    request_id=body.request_id,
                    request_hash=request_hash,
                    created_at=timestamp,
                    lease_until=timestamp + timedelta(seconds=settings.lease_seconds),
                )
                db.add(assignment)
                db.flush()
                runtime.assignment_id = assignment_id
            else:
                expected = (
                    JobStatus.VRAM_ESTIMATION_PENDING
                    if candidate.kind == Kind.ESTIMATION
                    else (
                        JobStatus.RETRY_NEEDED
                        if candidate.flag == "retry"
                        else JobStatus.RUNNABLE
                    )
                )
                job = (
                    db.query(Job)
                    .filter_by(id=candidate.target_id, status=expected)
                    .with_for_update(skip_locked=True)
                    .first()
                )
                if (
                    not job
                    or db.query(Assignment)
                    .filter_by(job_id=candidate.target_id, released_at=None)
                    .first()
                ):
                    db.rollback()
                    continue
                if candidate.kind != Kind.ESTIMATION:
                    if (
                        job.vram_required is not None
                        and job.vram_required + 1 > snapshot.free_vram
                    ):
                        db.rollback()
                        continue
                    job.status, job.started_at, job.device = (
                        JobStatus.IN_PROGRESS,
                        timestamp,
                        worker.gpu_type,
                    )
                from app.services.job_service import _format_job_response

                payload = json.loads(
                    json.dumps(_format_job_response(job, candidate.flag), default=str)
                )
                assignment = Assignment(
                    id=assignment_id,
                    worker_id=worker_id,
                    instance_id=body.instance_id,
                    kind=candidate.kind.value,
                    job_id=job.id,
                    exclusive=False,
                    payload=payload,
                    attempt_token=token,
                    request_id=body.request_id,
                    request_hash=request_hash,
                    created_at=timestamp,
                    lease_until=timestamp + timedelta(seconds=settings.lease_seconds),
                )
                db.add(assignment)
            db.commit()
            return envelope(assignment, settings)
        except IntegrityError:
            db.rollback()
    return {"assignment": None, "retry_after_seconds": 5}


def fence(db, worker_id, body, cleanup=False):
    worker = lock_worker(db, worker_id)
    assignment = (
        db.query(Assignment)
        .filter_by(
            id=body.assignment_id,
            worker_id=worker_id,
            instance_id=body.instance_id,
            attempt_token=body.attempt_token,
            generation=body.generation,
        )
        .with_for_update()
        .first()
    )
    if not assignment:
        raise HTTPException(409, "Stale assignment")
    if not cleanup and (
        assignment.released_at
        or worker.instance_id != body.instance_id
        or assignment.state == "LOST"
        or utc(assignment.lease_until) <= now()
    ):
        raise HTTPException(409, "Expired assignment")
    return worker, assignment


def stop_runtime(runtime, code=None):
    runtime.desired_state = "STOPPED"
    if runtime.state not in ("STOPPED", "FAILED"):
        runtime.state = "STOPPING"
    if code and not runtime.failure_code:
        runtime.failure_code, runtime.failure_detail = (
            code,
            "Runtime stopped; start a new runtime after cleanup.",
        )


def _apply_inventory_metrics(worker, inventory):
    """Mirror authenticated inventory into the legacy dashboard columns."""
    values = inventory.model_dump()
    mapping = {
        "hostname": "hostname",
        "ip_address": "ip_address",
        "free_vram_gb": "available_vram",
        "gpus_in_use": "gpus_in_use",
        "gpu_load": "gpu_load",
        "cpu_load": "cpu_load",
        "mem_usage": "mem_usage",
        "cpu_cores": "cpu_cores",
        "total_ram": "total_ram",
        "total_disk": "total_disk",
        "available_disk": "available_disk",
    }
    for source, target in mapping.items():
        value = values.get(source)
        if value is not None:
            setattr(worker, target, value)


def register(db, worker_id, body):
    worker = db.query(Worker).filter_by(worker_id=worker_id).with_for_update().first()
    if not worker:
        worker = Worker(
            worker_id=worker_id,
            gpu_type=body.gpu_type,
            num_gpus=len(body.inventory.gpus),
            total_vram=max([g.memory_gb for g in body.inventory.gpus] or [0]),
        )
        db.add(worker)
        db.flush()
    if worker.instance_id != body.instance_id:
        for assignment in live(db, worker_id):
            assignment.state = "LOST"
            if assignment.runtime_id:
                runtime = db.get(Runtime, assignment.runtime_id)
                stop_runtime(runtime, "INTERRUPTED")
        worker.heartbeat_sequence = 0
    worker.instance_id, worker.protocol_version = body.instance_id, 1
    worker.inventory, worker.gpu_type = body.inventory.model_dump(), body.gpu_type
    _apply_inventory_metrics(worker, body.inventory)
    worker.execution_mode = body.inventory.mode
    worker.execution_reconciling = True
    worker.authenticated_heartbeat_at = now()
    db.commit()
    return {
        "protocol_version": 1,
        "reconcile_assignments": [
            {
                "assignment_id": a.id,
                "instance_id": a.instance_id,
                "attempt_token": a.attempt_token,
                "generation": a.generation,
            }
            for a in live(db, worker_id)
        ],
    }


def heartbeat(db, worker_id, body, settings=None):
    settings = settings or Settings.from_env()
    worker = lock_worker(db, worker_id)
    if (
        worker.instance_id != body.instance_id
        or body.sequence <= worker.heartbeat_sequence
    ):
        raise HTTPException(409, "Stale heartbeat")
    timestamp = now()
    fresh = (
        abs(timestamp.timestamp() - body.inventory.observed_at)
        <= settings.fresh_seconds
    )
    worker.heartbeat_sequence, worker.authenticated_heartbeat_at = (
        body.sequence,
        timestamp,
    )
    worker.execution_paused, worker.execution_draining = body.paused, body.draining
    worker.inventory, worker.execution_mode = (
        body.inventory.model_dump(),
        body.inventory.mode,
    )
    _apply_inventory_metrics(worker, body.inventory)
    active = live(db, worker_id)
    reports = {a.assignment_id: a for a in body.assignments}
    decisions = []
    for assignment in active:
        report = reports.get(assignment.id)
        valid = (
            fresh
            and report
            and report.instance_id == assignment.instance_id == body.instance_id
            and report.attempt_token == assignment.attempt_token
            and report.generation == assignment.generation
            and assignment.state not in ("LOST", "CLEANING")
            and utc(assignment.lease_until) > timestamp
        )
        runtime = (
            db.get(Runtime, assignment.runtime_id) if assignment.runtime_id else None
        )
        if runtime:
            if (
                runtime.startup_deadline
                and runtime.state != "READY"
                and utc(runtime.startup_deadline) <= timestamp
            ):
                stop_runtime(runtime, "START_FAILED")
            if (
                runtime.lifetime_deadline
                and utc(runtime.lifetime_deadline) <= timestamp
            ):
                stop_runtime(runtime)
            valid = valid and runtime.desired_state == "RUNNING"
            if valid:
                runtime.health, runtime.health_at = (
                    report.health.model_dump(),
                    timestamp,
                )
                if runtime.state == "READY" and not all(runtime.health.values()):
                    stop_runtime(runtime, "HEALTH_FAILED")
                    valid = False
        if valid:
            assignment.lease_until = timestamp + timedelta(
                seconds=settings.lease_seconds
            )
            decisions.append(
                {
                    "assignment_id": assignment.id,
                    "action": "renew",
                    "lease_seconds": settings.lease_seconds,
                }
            )
        else:
            if assignment.state != "CLEANING":
                assignment.state = "LOST"
            if runtime and runtime.desired_state == "RUNNING":
                runtime.state = "LOST"
            decisions.append(
                {"assignment_id": assignment.id, "action": "stop", "lease_seconds": 0}
            )
    worker.execution_reconciling = (
        not fresh
        or body.inventory.mode in ("RECONCILING", "CLEANING", "UNCERTAIN")
        or any(a.state == "LOST" for a in active)
        or set(reports) != {a.id for a in active}
    )
    db.commit()
    return {"sequence": body.sequence, "decisions": decisions}


def event(db, worker_id, body):
    _, assignment = fence(db, worker_id, body)
    if not assignment.runtime_id:
        raise HTTPException(409, "Interactive event required")
    runtime = (
        db.query(Runtime).filter_by(id=assignment.runtime_id).with_for_update().one()
    )
    hashed = digest(body.model_dump())
    if body.sequence <= assignment.event_sequence:
        if (
            body.sequence == assignment.event_sequence
            and hashed != assignment.event_hash
        ):
            raise HTTPException(409, "Event conflict")
        db.commit()
        return {"accepted": True, "state": runtime.state}
    order = ["ASSIGNED", "PULLING", "STARTING", "CONNECTING"]
    if body.failure_code or body.phase == "STOPPING":
        stop_runtime(runtime, body.failure_code)
        assignment.state = "CLEANING"
    elif runtime.desired_state == "RUNNING":
        if runtime.state not in order or order.index(body.phase) < order.index(
            runtime.state
        ):
            raise HTTPException(409, "Illegal runtime transition")
        runtime.state = body.phase
        runtime.health, runtime.health_at = body.health.model_dump(), now()
        assignment.state = "ACTIVE"
    assignment.event_sequence, assignment.event_hash = body.sequence, hashed
    db.commit()
    return {"accepted": True, "state": runtime.state}


def result(db, worker_id, body):
    _, assignment = fence(db, worker_id, body)
    if not assignment.job_id:
        raise HTTPException(409, "Batch result required")
    value = body.model_dump(
        exclude={"instance_id", "assignment_id", "attempt_token", "generation"}
    )
    hashed = digest(value)
    if assignment.result_hash and assignment.result_hash != hashed:
        raise HTTPException(409, "Result conflict")
    if body.outcome == "estimation" and (
        assignment.kind != Kind.ESTIMATION.value
        or any(value[x] is None for x in ("vram_required", "ram_required", "step_time"))
    ):
        raise HTTPException(422, "Valid estimation report required")
    if body.outcome == "completed" and assignment.kind != Kind.BATCH.value:
        raise HTTPException(422, "Training completion required")
    assignment.result, assignment.result_hash, assignment.state = (
        value,
        hashed,
        "CLEANING",
    )
    db.commit()
    return {"accepted": True}


def release_if_clean(db, worker, assignment):
    if assignment.released_at:
        return True
    if not assignment.cleanup_ack:
        return False
    runtime = db.get(Runtime, assignment.runtime_id) if assignment.runtime_id else None
    if runtime and not runtime.management_revoked:
        return False
    if runtime:
        runtime.state = "FAILED" if runtime.failure_code else "STOPPED"
        runtime.stopped_at = now()
    if assignment.job_id:
        job = db.query(Job).filter_by(id=assignment.job_id).with_for_update().one()
        value = assignment.result or {"outcome": "system_failure"}
        if value["outcome"] == "estimation":
            job.vram_required, job.ram_required, job.step_time = (
                value["vram_required"],
                value["ram_required"],
                value["step_time"],
            )
            job.status = JobStatus.RUNNABLE
        elif value["outcome"] == "completed":
            job.status = JobStatus.COMPLETED
            job.gpu_hour = (
                max(0, (now() - utc(job.started_at)).total_seconds() / 3600)
                if job.started_at
                else 0
            )
        elif value["outcome"] == "user_failure":
            job.status, job.failure_reason = (
                JobStatus.FAILED,
                "Workload execution failed.",
            )
        else:
            job.status = (
                JobStatus.VRAM_ESTIMATION_PENDING
                if assignment.kind == Kind.ESTIMATION.value
                else JobStatus.RETRY_NEEDED
            )
            job.failure_reason = (
                "Execution interrupted; previous attempt cleanup confirmed."
            )
        job.device = None
    assignment.state, assignment.released_at = "RELEASED", now()
    return True


def cleanup(db, worker_id, body):
    worker, assignment = fence(db, worker_id, body, cleanup=True)
    assignment.cleanup_ack = True
    if assignment.runtime_id:
        stop_runtime(db.get(Runtime, assignment.runtime_id))
    released = release_if_clean(db, worker, assignment)
    db.commit()
    return {"released": released}


def expire(db):
    ids = (
        db.query(Assignment.worker_id)
        .filter(Assignment.released_at.is_(None), Assignment.lease_until <= now())
        .distinct()
        .all()
    )
    for (worker_id,) in ids:
        worker = lock_worker(db, worker_id)
        for assignment in live(db, worker_id):
            if utc(assignment.lease_until) <= now():
                assignment.state = "LOST"
                worker.execution_reconciling = True
                if assignment.runtime_id:
                    runtime = db.get(Runtime, assignment.runtime_id)
                    if runtime.desired_state == "RUNNING":
                        runtime.state = "LOST"
                        runtime.failure_code = "LEASE_LOST"
        db.commit()
