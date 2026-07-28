from app.models.worker_model import Worker
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.models.job_model import Job, JobStatus
from app.schemas.worker_schema import WorkerResource
import os


def create_job(db: Session, job_data: dict):
    db_job = Job(
        id=job_data["id"],
        object_key=job_data["object_key"],
        command=job_data["command"],
        docker_base_image=job_data["docker_base_image"],
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
            "object_key": job.object_key,
            "command": job.command,
            "docker_base_image": job.docker_base_image,
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
        "docker_base_image": job.docker_base_image,
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


def _find_matching_job(db: Session, status: JobStatus, request: WorkerResource):
    """Find the best job in `status` whose vram_required (+1.0 GB safety margin)
    fits the pulling worker's free VRAM. Jobs with unknown vram_required (not yet
    estimated) are also eligible. Prefers the largest vram_required first (best
    packing), then oldest first."""
    return (
        db.query(Job)
        .filter(
            Job.status == status,
            or_(
                Job.vram_required.is_(None),
                (Job.vram_required + 1.0) <= request.free_vram,
            ),
        )
        .order_by(Job.vram_required.desc().nullslast(), Job.created_at.asc())
        .first()
    )


def _assign_job_to_worker(db: Session, job: Job, worker_id: str, flag: str) -> dict:
    job.status = JobStatus.IN_PROGRESS
    job.assigned_worker_id = worker_id
    db.commit()
    db.refresh(job)
    return _format_job_response(job, flag=flag)


def _check_training_job_strategy(db: Session, request: WorkerResource) -> dict | None:
    """
    Strategy 2: Find a fresh RUNNABLE job (never started before) that fits.
    """
    job = _find_matching_job(db, JobStatus.RUNNABLE, request)
    if not job:
        return None

    return _assign_job_to_worker(db, job, request.worker_id, flag="training")


def _check_retry_job_strategy(db: Session, request: WorkerResource) -> dict | None:
    """
    Strategy 3: Find a job whose previous worker died mid-training
    (RETRY_PENDING, set by the watchdog). The worker resumes it from its last
    checkpoint, so it's matched using the same VRAM-fit logic as a fresh job.
    Placed ahead of fresh jobs in SCHEDULING_STRATEGIES since it represents
    work already in flight.
    """
    job = _find_matching_job(db, JobStatus.RETRY_PENDING, request)
    if not job:
        return None

    return _assign_job_to_worker(db, job, request.worker_id, flag="retry")


# List of scheduling strategies in priority order. Easy to extend with new strategies.
# Order rationale:
#   1. VRAM estimation jobs are cheap, quick, and unblock scheduling for everything
#      else, so they always jump the queue.
#   2. Retries represent work that's already partially done (has a checkpoint) and
#      whose recovery is time-sensitive, so they're preferred over brand-new jobs.
#   3. Fresh jobs run last.
SCHEDULING_STRATEGIES = [
    _check_vram_estimation_strategy,
    _check_retry_job_strategy,
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


# ---------------------------------------------------------------------------
# Fault recovery (watchdog support)
# ---------------------------------------------------------------------------

MAX_JOB_RETRIES = int(os.getenv("MAX_JOB_RETRIES", "3"))


def get_in_progress_assignments(db: Session) -> list[tuple[str, str]]:
    """Return (job_id, assigned_worker_id) for every job currently IN_PROGRESS.
    Used by the watchdog to check which of these workers are still alive."""
    rows = (
        db.query(Job.id, Job.assigned_worker_id)
        .filter(
            Job.status == JobStatus.IN_PROGRESS,
            Job.assigned_worker_id.isnot(None),
        )
        .all()
    )
    return [(row[0], row[1]) for row in rows]


def requeue_job_after_worker_death(db: Session, job_id: str, dead_worker_id: str) -> Job | None:
    """Called by the watchdog when a job's assigned worker has stopped sending
    heartbeats. Re-checks the job is still assigned to that same worker (avoids
    racing a legitimate completion/reassignment that happened concurrently),
    then either requeues it as RETRY_PENDING (picked up again via the retry
    scheduling strategy, resuming from its last checkpoint) or marks it FAILED
    if it has already exhausted MAX_JOB_RETRIES -- guards against a job that
    keeps crashing workers (e.g. a checkpoint that itself corrupts) looping
    forever.
    """
    job = (
        db.query(Job)
        .filter(
            Job.id == job_id,
            Job.status == JobStatus.IN_PROGRESS,
            Job.assigned_worker_id == dead_worker_id,
        )
        .first()
    )
    if not job:
        # Job already moved on (completed / reassigned) since the watchdog
        # took its snapshot -- nothing to do.
        return None

    job.assigned_worker_id = None

    if job.retry_count >= MAX_JOB_RETRIES:
        job.status = JobStatus.FAILED
    else:
        job.retry_count += 1
        job.status = JobStatus.RETRY_PENDING

    db.commit()
    db.refresh(job)
    return job