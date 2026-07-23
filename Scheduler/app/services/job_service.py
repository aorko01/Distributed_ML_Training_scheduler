from app.models.worker_model import Worker
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.models.job_model import Job, JobStatus
from app.schemas.worker_schema import WorkerResource


def create_job(db: Session, job_data: dict):
    db_job = Job(
        id=job_data["id"],
        object_key=job_data["object_key"],
        command=job_data["command"],
        config=job_data.get("config"),
        vram_required=job_data.get("vram_required"),
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
            "object_key": job.object_key,
            "command": job.command,
            "config": job.config,
            "status": job.status.value,
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
        "object_key": job.object_key,
        "command": job.command,
        "config": job.config,
        "status": job.status.value,
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

    db.commit()
    db.refresh(job)

    return job
