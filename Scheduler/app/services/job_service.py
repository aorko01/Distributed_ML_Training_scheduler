from datetime import datetime, timezone
import uuid

from app.models.worker_model import Worker
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.models.job_model import Job, JobStatus, JobPriority
from app.schemas.worker_schema import WorkerResource


ARCHIVE_SOURCE_KIND = "ARCHIVE"
PACKAGES_ONLY_SOURCE_KIND = "PACKAGES_ONLY"
TRAINING_INELIGIBLE_SOURCE_KINDS = frozenset({PACKAGES_ONLY_SOURCE_KIND})

PACKAGE_ONLY_TRAINING_MESSAGE = (
    "This image has no workspace files and is interactive-only. Open it as an "
    "interactive workspace, add and save files, then submit the saved workspace "
    "for training."
)


def training_eligible_for_source_kind(source_kind: str | None) -> bool:
    return (source_kind or ARCHIVE_SOURCE_KIND) not in TRAINING_INELIGIBLE_SOURCE_KINDS


def _source_kind_of(job) -> str:
    return getattr(job, "source_kind", None) or ARCHIVE_SOURCE_KIND


def create_job(db: Session, job_data: dict):
    source_kind = job_data.get("source_kind") or ARCHIVE_SOURCE_KIND
    object_key = job_data.get("object_key")
    if source_kind == ARCHIVE_SOURCE_KIND and not object_key:
        raise ValueError("ARCHIVE jobs require an object_key")
    if source_kind == PACKAGES_ONLY_SOURCE_KIND and object_key:
        raise ValueError("PACKAGES_ONLY jobs must not have an object_key")
    db_job = Job(
        id=job_data["id"],
        user_id=job_data["user_id"],
        object_key=object_key,
        source_kind=source_kind,
        name=job_data.get("name"),
        command=job_data.get("command"),
        resume_command=job_data.get("resume_command"),
        docker_base_image=job_data["docker_base_image"],
        packages=job_data.get("packages"),
        config=job_data.get("config"),
        vram_required=job_data.get("vram_required"),
        priority=job_data.get("priority", JobPriority.NORMAL),
        reason_for_priority=job_data.get("reason_for_priority"),
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    return db_job


def set_job_image_ready(
    db: Session,
    job_id: str,
    builder_id: str | None = None,
    attempt_id: str | None = None,
    image_tag: str | None = None,
):
    """Record a successful image build.

    The image is now immutable and runnable, but image building and training
    are decoupled: a workspace built without an entry command waits in
    ``IMAGE_READY`` until training is submitted for it (see
    :func:`submit_training`).  Legacy jobs that already carry a command keep
    their previous behaviour and go straight to VRAM estimation.
    """
    job = (
        db.query(Job)
        .filter(Job.id == job_id)
        .with_for_update()
        .first()
    )

    if not job:
        raise Exception("Job not found")

    if job.status != JobStatus.IMAGE_BUILDING:
        raise Exception("Job is not in IMAGE_BUILDING state")

    if builder_id is not None or attempt_id is not None:
        _require_image_build_owner(job, builder_id, attempt_id)

    if _source_kind_of(job) == PACKAGES_ONLY_SOURCE_KIND and job.command:
        # Package-only images are interactive-only. A command at build time
        # would otherwise advance them directly to VRAM estimation.
        job.command = None
    job.status = (
        JobStatus.VRAM_ESTIMATION_PENDING if job.command else JobStatus.IMAGE_READY
    )
    if image_tag is not None:
        job.image_tag = image_tag
    _clear_image_build_lease(job)

    db.commit()
    db.refresh(job)

    return job


def submit_training(
    db: Session,
    user_id: str,
    job_id: str,
    command: str,
    resume_command: str | None = None,
    priority: JobPriority | None = None,
    reason_for_priority: str | None = None,
):
    """Arm an already built workspace image with an entry command.

    This is the second half of the decoupled workflow: ``Add Workspace`` only
    produces an image (``IMAGE_READY``), and the Training page supplies the
    entry/resume command later.  Once a command is stored the job re-enters the
    normal pipeline — VRAM estimation, then training.
    """
    from fastapi import HTTPException

    job = (
        db.query(Job)
        .filter(Job.id == job_id, Job.user_id == user_id)
        .with_for_update()
        .first()
    )

    if not job:
        raise HTTPException(404, "Job not found")

    if _source_kind_of(job) == PACKAGES_ONLY_SOURCE_KIND:
        raise HTTPException(409, PACKAGE_ONLY_TRAINING_MESSAGE)

    if job.status == JobStatus.IMAGE_BUILDING or job.status == JobStatus.NOT_RUNNABLE:
        raise HTTPException(409, "Job image is still building; wait for it to be ready")
    if job.status != JobStatus.IMAGE_READY or not job.image_tag:
        raise HTTPException(
            409, f"Job is not awaiting a training command (status {job.status.value})"
        )

    job.command = command
    job.resume_command = resume_command or None
    if priority is not None:
        job.priority = priority
    job.reason_for_priority = reason_for_priority or None
    # A new entry command invalidates any previous measurement/run metadata.
    job.vram_required = None
    job.ram_required = None
    job.step_time = None
    job.failure_reason = None
    job.status = JobStatus.VRAM_ESTIMATION_PENDING

    db.commit()
    db.refresh(job)

    return job


def set_job_runnable(db: Session, job_id: str):
    _reject_managed_job(db, job_id)
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise Exception("Job not found")

    if job.status != JobStatus.VRAM_ESTIMATION_PENDING:
        raise Exception("Job is not in VRAM_ESTIMATION_PENDING state")

    job.status = JobStatus.RUNNABLE

    db.commit()
    db.refresh(job)

    return job


def save_vram_estimation(db: Session, job_id: str, vram_required: float, ram_required: float, step_time: float):
    _reject_managed_job(db, job_id)
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise Exception("Job not found")

    job.vram_required = vram_required
    job.ram_required = ram_required
    job.step_time = step_time
    job.status = JobStatus.RUNNABLE

    db.commit()
    db.refresh(job)

    return job


def get_not_runnable_jobs(db: Session):
    jobs = (
        db.query(Job)
        .filter(Job.status == JobStatus.NOT_RUNNABLE)
        .order_by(Job.created_at)
        .all()
    )

    return [
        {
            "id": job.id,
            "user_id": job.user_id,
            "object_key": job.object_key,
            "source_kind": job.source_kind or "ARCHIVE",
            "training_eligible": (job.source_kind or "ARCHIVE") != "PACKAGES_ONLY",
            "name": job.name,
            "command": job.command,
            "resume_command": job.resume_command,
            "docker_base_image": job.docker_base_image,
            "packages": job.packages,
            "config": job.config,
            "status": job.status.value,
            "priority": job.priority.value,
            "reason_for_priority": job.reason_for_priority,
            "vram_required": job.vram_required,
            "ram_required": job.ram_required,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "device": job.device,
        }
        for job in jobs
    ]


def claim_job_for_building(db: Session, builder_id: str) -> dict | None:
    """Atomically claim the oldest NOT_RUNNABLE job for image building.

    Sets the job's status to IMAGE_BUILDING so that no other builder thread
    or instance will pull the same job. Returns the job dict (shaped via
    ``_format_job_response`` with flag ``"image_building"``) or ``None`` when
    no unbuilt job is available.

    The row lock is essential here: separate builder threads (and separate
    builder containers) can make this request concurrently.  Without it, two
    transactions can both read the same NOT_RUNNABLE job before either commits
    the IMAGE_BUILDING transition.

    PostgreSQL honours ``skip_locked`` and therefore lets another claimant move
    on to the next pending job instead of waiting on this one.  SQLite ignores
    ``FOR UPDATE`` but remains supported for the single-process test/dev setup.
    """
    job = (
        db.query(Job)
        .filter(
            Job.status == JobStatus.NOT_RUNNABLE,
            or_(
                Job.image_build_excluded_builder_id.is_(None),
                Job.image_build_excluded_builder_id != builder_id,
                Job.image_build_excluded_until.is_(None),
                Job.image_build_excluded_until <= datetime.now(timezone.utc),
            ),
        )
        .order_by(Job.created_at)
        .with_for_update(skip_locked=True)
        .first()
    )

    if not job:
        return None

    job.status = JobStatus.IMAGE_BUILDING
    job.image_builder_id = builder_id
    job.image_build_attempt_id = str(uuid.uuid4())
    job.image_build_started_at = datetime.now(timezone.utc)
    job.image_build_excluded_builder_id = None
    job.image_build_excluded_until = None
    job.image_tag = None
    db.commit()
    db.refresh(job)

    return _format_job_response(job, flag="image_building")


def _require_image_build_owner(
    job: Job, builder_id: str | None, attempt_id: str | None
) -> None:
    """Reject stale image-builder callbacks using the claim's fencing token."""
    if not builder_id or not attempt_id:
        raise Exception("Image builder ID and build attempt ID are required")
    if (
        job.image_builder_id != builder_id
        or job.image_build_attempt_id != attempt_id
    ):
        raise Exception("Stale or unowned image build attempt")


def _clear_image_build_lease(job: Job) -> None:
    job.image_builder_id = None
    job.image_build_attempt_id = None
    job.image_build_started_at = None


def release_job_to_not_runnable(
    db: Session, job_id: str, builder_id: str, attempt_id: str
) -> Job:
    """Release a job that is currently IMAGE_BUILDING back to NOT_RUNNABLE.

    Used when a builder encounters a system-level failure and the job should be
    retried by another builder thread/instance. Raises an exception if the job
    is not found or is not in the IMAGE_BUILDING state.
    """
    job = (
        db.query(Job)
        .filter(Job.id == job_id)
        .with_for_update()
        .first()
    )

    if not job:
        raise Exception("Job not found")

    if job.status != JobStatus.IMAGE_BUILDING:
        raise Exception(
            f"Job is not in IMAGE_BUILDING state (current: {job.status.value})"
        )

    _require_image_build_owner(job, builder_id, attempt_id)

    job.status = JobStatus.NOT_RUNNABLE
    _clear_image_build_lease(job)
    db.commit()
    db.refresh(job)

    return job


import asyncio
from app.core.redis import redis_client

# Maps a pulled job to the worker that is executing it (Redis, not psql), so the
# stall watcher can tell which worker's heartbeat a job depends on.
JOB_WORKER_KEY_PREFIX = "job_worker:"


async def _get_connected_workers_vram() -> list[float]:
    """Retrieve available VRAM for all currently connected workers from Redis."""
    keys = await redis_client.keys("worker:*")
    vrams = []
    for k in keys:
        vram_str = await redis_client.hget(k, "available_vram")
        if vram_str is not None:
            try:
                vrams.append(float(vram_str))
            except ValueError:
                pass
    return vrams


async def _is_highest_vram_worker(worker_free_vram: float) -> bool:
    """Check if worker's available VRAM is highest among all currently connected workers."""
    vrams = await _get_connected_workers_vram()
    if not vrams:
        return True
    return worker_free_vram >= max(vrams)


def _format_job_response(job: Job, flag: str) -> dict:
    source_kind = _source_kind_of(job)
    return {
        "flag": flag,
        "id": job.id,
        "user_id": job.user_id,
        "object_key": job.object_key,
        "source_kind": source_kind,
        "training_eligible": training_eligible_for_source_kind(source_kind),
        "name": job.name,
        "command": job.command,
        "resume_command": job.resume_command,
        "docker_base_image": job.docker_base_image,
        "packages": job.packages,
        "config": job.config,
        "status": job.status.value,
        "priority": job.priority.value,
        "reason_for_priority": job.reason_for_priority,
        "vram_required": job.vram_required,
        "ram_required": job.ram_required,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "device": job.device,
        "image_tag": job.image_tag,
        "source_workspace_id": job.source_workspace_id,
        "source_revision_id": job.source_revision_id,
        "source_image_digest_ref": job.source_image_digest_ref,
        "executable_image_digest_ref": job.executable_image_digest_ref,
        "image_builder_id": job.image_builder_id,
        "image_build_attempt_id": job.image_build_attempt_id,
    }


async def get_job_for_resume(db, job_id, worker_id, device=None):
    from fastapi import HTTPException
    raise HTTPException(410, 'Use authenticated Worker assignment reconciliation')


async def get_next_job_for_worker(db, request):
    from fastapi import HTTPException
    raise HTTPException(410, 'Use authenticated Worker assignment claims')


def _reject_managed_job(db, job_id):
    from app.models.interactive_runtime_model import WorkerAssignment
    from fastapi import HTTPException
    if db.query(WorkerAssignment).filter_by(job_id=job_id).first():
        raise HTTPException(409, 'Assignment-managed jobs require authenticated fenced results')


def set_to_completed(db: Session, job_id: str):
    _reject_managed_job(db, job_id)
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise Exception("Job not found")

    if job.status != JobStatus.IN_PROGRESS:
        raise Exception("Job is not in IN_PROGRESS state")

    job.status = JobStatus.COMPLETED

    if job.started_at is not None:
        started_at = job.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        elapsed_seconds = (datetime.now(timezone.utc) - started_at).total_seconds()
        job.gpu_hour = max(elapsed_seconds, 0.0) / 3600.0

    db.commit()
    db.refresh(job)

    return job


TERMINAL_STATUSES = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.RETRY_NEEDED}


def mark_job_failed(
    db: Session,
    job_id: str,
    failure_type: str,
    failure_reason: str | None = None,
    builder_id: str | None = None,
    attempt_id: str | None = None,
):
    """Record a job failure reported by the Docker Image Builder or a Worker.

    - failure_type == "user"   -> status FAILED (build or training code error)
    - failure_type == "system" -> status RETRY_NEEDED (infra/registry/daemon issue)

    Jobs already in a terminal state are left untouched.
    """
    _reject_managed_job(db, job_id)
    job = (
        db.query(Job)
        .filter(Job.id == job_id)
        .with_for_update()
        .first()
    )

    if not job:
        raise Exception("Job not found")

    if job.status in TERMINAL_STATUSES:
        raise Exception(f"Job is already in terminal state {job.status.value}")

    if job.status == JobStatus.IMAGE_BUILDING:
        _require_image_build_owner(job, builder_id, attempt_id)

    was_in_progress = job.status == JobStatus.IN_PROGRESS
    job.status = JobStatus.FAILED if failure_type == "user" else JobStatus.RETRY_NEEDED
    if failure_reason:
        job.failure_reason = failure_reason[:2000]

    if job.status in TERMINAL_STATUSES:
        _clear_image_build_lease(job)

    if was_in_progress and job.started_at is not None:
        started_at = job.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        elapsed_seconds = (datetime.now(timezone.utc) - started_at).total_seconds()
        job.gpu_hour = max(elapsed_seconds, 0.0) / 3600.0

    db.commit()
    db.refresh(job)

    return job


def get_runnable_jobs_count(db: Session) -> int:
    """Total number of jobs waiting in the queue (RUNNABLE)."""
    return db.query(Job).filter(Job.status == JobStatus.RUNNABLE).count()


def get_user_jobs(db: Session, user_id: str):
    jobs = (
        db.query(Job)
        .filter(Job.user_id == user_id)
        .order_by(Job.created_at.desc())
        .all()
    )

    return [
        {
            "id": job.id,
            "user_id": job.user_id,
            "object_key": job.object_key,
            "source_kind": job.source_kind or "ARCHIVE",
            "training_eligible": (job.source_kind or "ARCHIVE") != "PACKAGES_ONLY",
            "name": job.name,
            "command": job.command,
            "resume_command": job.resume_command,
            "docker_base_image": job.docker_base_image,
            "packages": job.packages,
            "config": job.config,
            "status": job.status.value,
            "priority": job.priority.value,
            "reason_for_priority": job.reason_for_priority,
            "vram_required": job.vram_required,
            "ram_required": job.ram_required,
            "step_time": job.step_time,
            "gpu_hour": job.gpu_hour,
            "device": job.device,
            "failure_reason": job.failure_reason,
            "image_tag": job.image_tag,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }
        for job in jobs
    ]


def get_user_jobs_count(db: Session, user_id: str) -> int:
    return db.query(Job).filter(Job.user_id == user_id).count()


def get_user_gpu_hours(db: Session, user_id: str) -> float:
    """Sum the gpu_hour of every job belonging to the user."""
    jobs = db.query(Job).filter(Job.user_id == user_id).all()
    total_hours = sum(job.gpu_hour for job in jobs if job.gpu_hour is not None)
    return round(total_hours, 4)


def get_user_job_by_id(db: Session, user_id: str, job_id: str):
    job = (
        db.query(Job)
        .filter(Job.id == job_id, Job.user_id == user_id)
        .first()
    )

    if not job:
        return None

    source_kind = job.source_kind or "ARCHIVE"
    return {
        "id": job.id,
        "user_id": job.user_id,
        "object_key": job.object_key,
        "source_kind": source_kind,
        "training_eligible": source_kind != "PACKAGES_ONLY",
        "name": job.name,
        "command": job.command,
        "resume_command": job.resume_command,
        "docker_base_image": job.docker_base_image,
        "packages": job.packages,
        "config": job.config,
        "status": job.status.value,
        "priority": job.priority.value,
        "reason_for_priority": job.reason_for_priority,
        "vram_required": job.vram_required,
        "step_time": job.step_time,
        "gpu_hour": job.gpu_hour,
        "device": job.device,
        "failure_reason": job.failure_reason,
        "image_tag": job.image_tag,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
