from datetime import datetime, timezone

from app.models.worker_model import Worker
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.models.job_model import Job, JobStatus, JobPriority
from app.schemas.worker_schema import WorkerResource


def create_job(db: Session, job_data: dict):
    db_job = Job(
        id=job_data["id"],
        user_id=job_data["user_id"],
        object_key=job_data["object_key"],
        command=job_data["command"],
        docker_base_image=job_data["docker_base_image"],
        config=job_data.get("config"),
        vram_required=job_data.get("vram_required"),
        priority=job_data.get("priority", JobPriority.NORMAL),
        reason_for_priority=job_data.get("reason_for_priority"),
    )
    db.add(db_job)
    # Tells the session: “I want to insert this object into the database.”
    # Object is staged, not yet written to the database.
    # SQLAlchemy keeps track of changes in a transactional “unit of work.”
    db.commit()
    # Writes the staged object into the database.
    # A SQL INSERT statement is executed.
    # After this, the job exists in the database.
    # The transaction is committed; the changes are now permanent.
    db.refresh(db_job)
    return db_job


def set_job_vram_estimation_pending(db: Session, job_id: str):
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise Exception("Job not found")

    if job.status != JobStatus.NOT_RUNNABLE:
        raise Exception("Job is not in NOT_RUNNABLE state")

    job.status = JobStatus.VRAM_ESTIMATION_PENDING

    db.commit()
    db.refresh(job)

    return job


def set_job_runnable(db: Session, job_id: str):
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise Exception("Job not found")

    if job.status != JobStatus.VRAM_ESTIMATION_PENDING:
        raise Exception("Job is not in VRAM_ESTIMATION_PENDING state")

    job.status = JobStatus.RUNNABLE

    db.commit()
    db.refresh(job)

    return job


def save_vram_estimation(db: Session, job_id: str, vram_required: float, step_time: float):
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise Exception("Job not found")

    job.vram_required = vram_required
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
            "command": job.command,
            "docker_base_image": job.docker_base_image,
            "config": job.config,
            "status": job.status.value,
            "priority": job.priority.value,
            "reason_for_priority": job.reason_for_priority,
            "vram_required": job.vram_required,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }
        for job in jobs
    ]


import asyncio
from app.core.redis import redis_client


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
    return {
        "flag": flag,
        "id": job.id,
        "user_id": job.user_id,
        "object_key": job.object_key,
        "command": job.command,
        "docker_base_image": job.docker_base_image,
        "config": job.config,
        "status": job.status.value,
        "priority": job.priority.value,
        "reason_for_priority": job.reason_for_priority,
        "vram_required": job.vram_required,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


async def _check_vram_estimation_strategy(db: Session, request: WorkerResource) -> dict | None:
    """
    Strategy 1: If pulling worker has highest VRAM among connected workers,
    check for any job with VRAM_ESTIMATION_PENDING status.
    """
    is_highest = await _is_highest_vram_worker(request.free_vram)
    if not is_highest:
        return None

    job = (
        db.query(Job)
        .filter(Job.status == JobStatus.VRAM_ESTIMATION_PENDING)
        .order_by(Job.created_at)
        .first()
    )

    if not job:
        return None

    return _format_job_response(job, flag="vram_estimation")


def _check_training_job_strategy(db: Session, request: WorkerResource) -> dict | None:
    """
    Strategy 2: Find runnable training job where (vram_required + 1.0) <= available vram of pulling worker.
    Selects the job with largest vram_required.
    """
    job = (
        db.query(Job)
        .filter(
            Job.status == JobStatus.RUNNABLE,
            or_(
                Job.vram_required.is_(None),
                (Job.vram_required + 1.0) <= request.free_vram,
            ),
        )
        .order_by(Job.vram_required.desc().nullslast(), Job.created_at.asc())
        .first()
    )

    if not job:
        return None

    job.status = JobStatus.IN_PROGRESS
    job.started_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)

    return _format_job_response(job, flag="training")


# List of scheduling strategies in priority order. Easy to extend with new strategies.
SCHEDULING_STRATEGIES = [
    _check_vram_estimation_strategy,
    _check_training_job_strategy,
]


async def get_next_job_for_worker(db: Session, request: WorkerResource):
    """
    Main entry point for pulling a job.
    Executes scheduling strategies in order until a job is matched.
    """
    worker = db.query(Worker).filter(Worker.worker_id == request.worker_id).first()
    if not worker:
        raise Exception("Worker not found")

    for strategy in SCHEDULING_STRATEGIES:
        if asyncio.iscoroutinefunction(strategy):
            job_info = await strategy(db, request)
        else:
            job_info = strategy(db, request)

        if job_info is not None:
            return job_info

    return None


def set_to_completed(db: Session, job_id: str):
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
            "command": job.command,
            "docker_base_image": job.docker_base_image,
            "config": job.config,
            "status": job.status.value,
            "priority": job.priority.value,
            "reason_for_priority": job.reason_for_priority,
            "vram_required": job.vram_required,
            "step_time": job.step_time,
            "gpu_hour": job.gpu_hour,
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
    return sum(job.gpu_hour for job in jobs if job.gpu_hour is not None)


def get_user_job_by_id(db: Session, user_id: str, job_id: str):
    job = (
        db.query(Job)
        .filter(Job.id == job_id, Job.user_id == user_id)
        .first()
    )

    if not job:
        return None

    return {
        "id": job.id,
        "user_id": job.user_id,
        "object_key": job.object_key,
        "command": job.command,
        "docker_base_image": job.docker_base_image,
        "config": job.config,
        "status": job.status.value,
        "priority": job.priority.value,
        "reason_for_priority": job.reason_for_priority,
        "vram_required": job.vram_required,
        "step_time": job.step_time,
        "gpu_hour": job.gpu_hour,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
