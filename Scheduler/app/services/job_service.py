from datetime import datetime, timezone
import asyncio
import os
import uuid

from app.models.worker_model import Worker
from app.models.interactive_session_model import (
    InteractiveSession,
    InteractiveSessionStatus,
)
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.models.job_model import Job, JobStatus, JobPriority
from app.schemas.worker_schema import WorkerResource
from app.core.redis import redis_client


def create_job(db: Session, job_data: dict):
    db_job = Job(
        id=job_data["id"],
        user_id=job_data["user_id"],
        object_key=job_data["object_key"],
        name=job_data.get("name"),
        command=job_data["command"],
        resume_command=job_data.get("resume_command"),
        docker_base_image=job_data["docker_base_image"],
        config=job_data.get("config"),
        vram_required=job_data.get("vram_required"),
        priority=job_data.get("priority", JobPriority.NORMAL),
        reason_for_priority=job_data.get("reason_for_priority"),
    )
    db.add(db_job)
    # Tells the session: "I want to insert this object into the database."
    # Object is staged, not yet written to the database.
    # SQLAlchemy keeps track of changes in a transactional "unit of work."
    db.commit()
    # Writes the staged object into the database.
    # A SQL INSERT statement is executed.
    # After this, the job exists in the database.
    # The transaction is committed; the changes are now permanent.
    db.refresh(db_job)
    return db_job


def create_interactive_job(db: Session, job_data: dict):
    """Create an interactive job in one of two modes (two-container model):

    - Derived: base_job_id points at an existing training job. The interactive
      job has no zip archive (object_key=None); the builder reuses the base
      training image ({user}/{base_job_id}:latest) as the env container image.
      No env image is built. The shared access image (aorko123/access-sshd:latest)
      provides SSH + tailnet.
    - Direct: object_key points at an uploaded archive and config carries the
      environment spec (python_version / pytorch_version / cuda_version or a
      full base_image override). The builder builds a standalone env image
      ({user}/{job.id}-env:latest) from a fresh base and copies the user's
      code into it. The shared access image provides SSH + tailnet.
    """
    base_job_id = job_data.get("base_job_id")
    user_id = job_data["user_id"]
    object_key = job_data.get("object_key")

    if base_job_id:
        base_job = db.query(Job).filter(Job.id == base_job_id).first()
        if not base_job:
            raise Exception("Base job not found")
        if base_job.user_id != user_id:
            raise Exception("Base job does not belong to this user")
    elif not object_key:
        raise Exception("Interactive job needs either base_job_id or an uploaded archive")

    if base_job_id:
        # Reuse an existing interactive session for this base job so the image
        # is never built twice. The partial unique index
        # uq_jobs_base_job_interactive enforces at most one interactive job per
        # base_job_id, so we must check for ANY existing row regardless of
        # status — not just NOT_RUNNABLE / INTERACTIVE_READY.
        existing = (
            db.query(Job)
            .filter(
                Job.user_id == user_id,
                Job.base_job_id == base_job_id,
                Job.build_type == "interactive",
            )
            .first()
        )
        if existing:
            # If the previous session is in a terminal or failed state, reset
            # it so the user can retry the build without hitting a constraint
            # violation.
            terminal_states = {
                JobStatus.INTERACTIVE_STOPPED,
                JobStatus.FAILED,
            }
            if existing.status in terminal_states:
                existing.status = JobStatus.NOT_RUNNABLE  # type: ignore[assignment]
                existing.failure_reason = None
                db.commit()
                db.refresh(existing)
            return existing

    default_name = None
    if base_job_id:
        base_job = db.query(Job).filter(Job.id == base_job_id).first()
        default_name = base_job.name if base_job else None

    job_id = str(uuid.uuid4())
    db_job = Job(
        id=job_id,
        user_id=user_id,
        object_key=object_key,
        name=job_data.get("name") or default_name,
        command="",
        resume_command=None,
        docker_base_image=None,
        config=job_data.get("config"),
        priority=JobPriority.NORMAL,
        build_type="interactive",
        base_job_id=base_job_id,
        status=JobStatus.NOT_RUNNABLE,
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    return db_job


def mark_interactive_ready(db: Session, job_id: str):
    """Mark an interactive job as INTERACTIVE_READY after the builder finishes."""
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise Exception("Job not found")

    if job.status != JobStatus.NOT_RUNNABLE:
        raise Exception("Job is not in NOT_RUNNABLE state")

    job.status = JobStatus.INTERACTIVE_READY  # type: ignore[assignment]

    db.commit()
    db.refresh(job)

    return job


def set_job_vram_estimation_pending(db: Session, job_id: str):
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise Exception("Job not found")

    if job.status != JobStatus.NOT_RUNNABLE:
        raise Exception("Job is not in NOT_RUNNABLE state")

    job.status = JobStatus.VRAM_ESTIMATION_PENDING  # type: ignore[assignment]

    db.commit()
    db.refresh(job)

    return job


def set_job_runnable(db: Session, job_id: str):
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise Exception("Job not found")

    if job.status != JobStatus.VRAM_ESTIMATION_PENDING:
        raise Exception("Job is not in VRAM_ESTIMATION_PENDING state")

    job.status = JobStatus.RUNNABLE  # type: ignore[assignment]

    db.commit()
    db.refresh(job)

    return job


def save_vram_estimation(db: Session, job_id: str, vram_required: float, ram_required: float, step_time: float):
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise Exception("Job not found")

    job.vram_required = vram_required  # type: ignore[assignment]
    job.ram_required = ram_required  # type: ignore[assignment]
    job.step_time = step_time  # type: ignore[assignment]
    job.status = JobStatus.RUNNABLE  # type: ignore[assignment]

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
            "name": job.name,
            "command": job.command,
            "resume_command": job.resume_command,
            "docker_base_image": job.docker_base_image,
            "config": job.config,
            "status": job.status.value,
            "priority": job.priority.value,
            "reason_for_priority": job.reason_for_priority,
            "vram_required": job.vram_required,
            "ram_required": job.ram_required,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "device": job.device,
            "build_type": job.build_type,
            "base_job_id": job.base_job_id,
        }
        for job in jobs
    ]


# Maps a pulled job to the worker that is executing it (Redis, not psql), so the
# stall watcher can tell which worker's heartbeat a job depends on.
JOB_WORKER_KEY_PREFIX = "job_worker:"


async def _worker_busy_with_jobs(db: Session, worker_id: str) -> bool:
    """Return True if the worker is currently running a batch job
    (IN_PROGRESS or VRAM_ESTIMATION_PENDING) tracked via the job_worker Redis map."""
    running_jobs = (
        db.query(Job)
        .filter(Job.status.in_([JobStatus.IN_PROGRESS, JobStatus.VRAM_ESTIMATION_PENDING]))
        .all()
    )
    for job in running_jobs:
        assigned = await redis_client.get(JOB_WORKER_KEY_PREFIX + job.id)
        if assigned == worker_id:
            return True
    return False


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
        "name": job.name,
        "command": job.command,
        "resume_command": job.resume_command,
        "docker_base_image": job.docker_base_image,
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


async def _check_interactive_job_strategy(db: Session, request: WorkerResource) -> dict | None:
    """
    Strategy 0: Hand an interactive session to any idle worker that polls.

    The worker stays scheduling-agnostic: the payload carries the env image
    tag, the shared access image tag, and the runtime credentials (headscale
    URL/pre-auth key, SSH public key) the access container entrypoint needs.
    The session record is created up front by interactive_service.create_session()
    in PENDING state; here it is bound to the pulling worker and moved to
    DEPLOYING.

    Two-container model:
    - env_image_tag: the training/environment container (no SSH/Headscale).
      For derived sessions this is the base training job's image; for direct
      sessions it is the freshly built env image.
    - access_image_tag: the shared aorko123/access-sshd image (sshd + tailscaled)
      that joins the env container's PID namespace via nsenter.
    """
    # Never dispatch interactive work to a machine that is already running a
    # batch job (IN_PROGRESS or VRAM_ESTIMATION_PENDING).
    if await _worker_busy_with_jobs(db, request.worker_id):
        return None

    job = (
        db.query(Job)
        .filter(Job.status == JobStatus.INTERACTIVE_READY)
        .order_by(Job.created_at.asc())
        .first()
    )
    if not job:
        return None

    # Serialize dispatch: lock the PENDING session row with FOR UPDATE so two
    # workers cannot both claim the same session. Also skip sessions previously
    # dispatched to this worker (last_worker_id) to avoid re-dispatching to a
    # machine that just lost its container.
    session = (
        db.query(InteractiveSession)
        .filter(
            InteractiveSession.job_id == job.id,
            InteractiveSession.status == InteractiveSessionStatus.PENDING,
            or_(
                InteractiveSession.last_worker_id.is_(None),
                InteractiveSession.last_worker_id != request.worker_id,
            ),
        )
        .order_by(InteractiveSession.created_at.asc())
        .with_for_update()
        .first()
    )
    if not session:
        return None

    job.status = JobStatus.INTERACTIVE_DEPLOYING  # type: ignore[assignment]
    job.started_at = datetime.now(timezone.utc)
    job.device = request.gpu_type  # type: ignore[assignment]

    session.worker_id = request.worker_id  # type: ignore[assignment]
    session.last_worker_id = request.worker_id  # type: ignore[assignment]
    session.status = InteractiveSessionStatus.DEPLOYING  # type: ignore[assignment]
    db.commit()
    db.refresh(job)
    db.refresh(session)

    await redis_client.set(JOB_WORKER_KEY_PREFIX + job.id, request.worker_id)  # type: ignore[arg-type]

    docker_hub_username = os.getenv('DOCKER_HUB_USERNAME', 'aorko123')
    if job.base_job_id:
        # Derived: reuse the base training job's image as the env image.
        env_image_tag = f"{docker_hub_username}/{job.base_job_id}:latest"
    else:
        # Direct: the builder built a fresh env image for this job.
        env_image_tag = f"{docker_hub_username}/{job.id}-env:latest"

    return {
        "flag": "interactive",
        "id": job.id,
        "job_id": job.id,
        "session_id": session.session_id,
        "env_image_tag": env_image_tag,
        "access_image_tag": "aorko123/access-sshd:latest",
        "headscale_url": os.getenv("HEADSCALE_URL", ""),
        "headscale_auth_key": session.headscale_auth_key,
        "ssh_public_key": session.ssh_public_key,
        "status": JobStatus.INTERACTIVE_DEPLOYING.value,
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

    await redis_client.set(JOB_WORKER_KEY_PREFIX + job.id, request.worker_id)  # type: ignore[arg-type]

    return _format_job_response(job, flag="vram_estimation")


async def _check_training_job_strategy(db: Session, request: WorkerResource) -> dict | None:
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

    job.status = JobStatus.IN_PROGRESS  # type: ignore[assignment]
    job.started_at = datetime.now(timezone.utc)
    job.device = request.gpu_type  # type: ignore[assignment]  # Save the device when worker pulls for running
    db.commit()
    db.refresh(job)

    await redis_client.set(JOB_WORKER_KEY_PREFIX + job.id, request.worker_id)  # type: ignore[arg-type]

    return _format_job_response(job, flag="training")


async def _check_retry_job_strategy(db: Session, request: WorkerResource) -> dict | None:
    """
    Strategy 3: Requeue a job that previously failed due to an infrastructure
    issue (RETRY_NEEDED). It is only sent to a worker whose free VRAM fits the
    job's estimated requirement, so the worker can restore the checkpoints from
    the object store and resume with the resume command.
    """
    job = (
        db.query(Job)
        .filter(
            Job.status == JobStatus.RETRY_NEEDED,
            or_(
                Job.vram_required.is_(None),
                (Job.vram_required + 1.0) <= request.free_vram,
            ),
        )
        .order_by(Job.created_at.asc())
        .first()
    )

    if not job:
        return None

    job.status = JobStatus.IN_PROGRESS  # type: ignore[assignment]
    job.started_at = datetime.now(timezone.utc)
    job.device = request.gpu_type  # type: ignore[assignment]  # Save the device when worker pulls for running
    db.commit()
    db.refresh(job)

    await redis_client.set(JOB_WORKER_KEY_PREFIX + job.id, request.worker_id)  # type: ignore[arg-type]

    return _format_job_response(job, flag="retry")


# List of scheduling strategies in priority order. Easy to extend with new strategies.
SCHEDULING_STRATEGIES = [
    _check_interactive_job_strategy,
    _check_vram_estimation_strategy,
    _check_retry_job_strategy,
    _check_training_job_strategy,
]


async def get_job_for_resume(db: Session, job_id: str, worker_id: str,
                             device: str | None = None) -> dict | None:
    """Return a job (shaped as a retry payload) if it is still IN_PROGRESS,
    assigned to the given worker, and running on the same device (GPU type) as
    the requesting worker, so a restarted worker can resume it before the stall
    watchdog marks it RETRY_NEEDED.

    Returns None when the job was already requeued, completed or failed, when it
    is being run by a different worker, or when it is running on a different
    device (which would let two devices run the same job).
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None or job.status != JobStatus.IN_PROGRESS:
        return None

    assigned = await redis_client.get(JOB_WORKER_KEY_PREFIX + job.id)
    if assigned not in (None, worker_id):
        return None

    # Only resume when the job's recorded device matches this worker's device;
    # otherwise the job may be running on another device entirely.
    if job.device and device and job.device != device:
        return None

    # Re-assert the job->worker mapping so the stall watchdog keeps tracking
    # this worker if the job stalls again after being resumed.
    await redis_client.set(JOB_WORKER_KEY_PREFIX + job.id, worker_id)

    return _format_job_response(job, flag="retry")


async def get_next_job_for_worker(db: Session, request: WorkerResource):
    """
    Main entry point for pulling a job.
    Executes scheduling strategies in order until a job is matched.
    """
    worker = db.query(Worker).filter(Worker.worker_id == request.worker_id).first()
    if not worker:
        raise Exception("Worker not found")

    # Do not assign any job to a worker reserved for testing.
    if worker.is_testing is True:
        return None

    # Block pulls while a machine hosts an interactive session (DEPLOYING through
    # STOPPING). The worker is busy deploying/running/tearing down its interactive
    # container and must not pull additional batch jobs.
    active_interactive = (
        db.query(InteractiveSession)
        .filter(
            InteractiveSession.worker_id == request.worker_id,
            InteractiveSession.status.in_([
                InteractiveSessionStatus.DEPLOYING,
                InteractiveSessionStatus.RUNNING,
                InteractiveSessionStatus.STOPPING,
            ]),
        )
        .first()
    )
    if active_interactive:
        return None

    for strategy in SCHEDULING_STRATEGIES:
        if asyncio.iscoroutinefunction(strategy):
            job_info = await strategy(db, request)  # type: ignore[misc]
        else:
            job_info = strategy(db, request)  # type: ignore[assignment]

        if job_info is not None:
            return job_info

    return None


def set_to_completed(db: Session, job_id: str):
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise Exception("Job not found")

    if job.status != JobStatus.IN_PROGRESS:
        raise Exception("Job is not in IN_PROGRESS state")

    job.status = JobStatus.COMPLETED  # type: ignore[assignment]

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


def mark_job_failed(db: Session, job_id: str, failure_type: str, failure_reason: str | None = None):
    """Record a job failure reported by the Docker Image Builder or a Worker.

    - failure_type == "user"   -> status FAILED (build or training code error)
    - failure_type == "system" -> status RETRY_NEEDED (infra/registry/daemon issue)

    Jobs already in a terminal state are left untouched.
    """
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise Exception("Job not found")

    if job.status in TERMINAL_STATUSES:
        raise Exception(f"Job is already in terminal state {job.status.value}")

    was_in_progress = job.status == JobStatus.IN_PROGRESS
    job.status = JobStatus.FAILED if failure_type == "user" else JobStatus.RETRY_NEEDED  # type: ignore[assignment]
    if failure_reason:
        job.failure_reason = failure_reason[:2000]  # type: ignore[assignment]

    if was_in_progress and job.started_at is not None:
        started_at = job.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        elapsed_seconds = (datetime.now(timezone.utc) - started_at).total_seconds()
        job.gpu_hour = max(elapsed_seconds, 0.0) / 3600.0  # type: ignore[assignment]

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
            "name": job.name,
            "command": job.command,
            "resume_command": job.resume_command,
            "docker_base_image": job.docker_base_image,
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

    return {
        "id": job.id,
        "user_id": job.user_id,
        "object_key": job.object_key,
        "name": job.name,
        "command": job.command,
        "resume_command": job.resume_command,
        "docker_base_image": job.docker_base_image,
        "config": job.config,
        "status": job.status.value,
        "priority": job.priority.value,
        "reason_for_priority": job.reason_for_priority,
        "vram_required": job.vram_required,
        "step_time": job.step_time,
        "gpu_hour": job.gpu_hour,
        "device": job.device,
        "failure_reason": job.failure_reason,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def commit_interactive_job(db: Session, job_id: str, command: str,
                           resume_command: str | None, priority: JobPriority,
                           reason_for_priority: str | None) -> dict:
    """Validate and queue a commit of a running interactive session.

    Stores the training command fields on the job record now (so they survive a
    slow push) but does NOT move the job out of INTERACTIVE_RUNNING — the
    VRAM_ESTIMATION_PENDING transition happens only in complete_commit(), after
    the worker confirms the image was pushed.
    """
    from app.models.interactive_session_model import (
        InteractiveSession,
        InteractiveSessionStatus,
    )

    if not command or not command.strip():
        raise Exception("Run command is required")

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise Exception("Job not found")
    if job.status != JobStatus.INTERACTIVE_RUNNING:
        raise Exception("Job is not in INTERACTIVE_RUNNING state")

    session = (
        db.query(InteractiveSession)
        .filter(
            InteractiveSession.job_id == job_id,
            InteractiveSession.status == InteractiveSessionStatus.RUNNING,
        )
        .first()
    )
    if not session:
        raise Exception("No running interactive session for this job")
    if session.commit_pending:
        raise Exception("A commit is already in progress for this session")

    job.command = command  # type: ignore[assignment]
    job.resume_command = resume_command  # type: ignore[assignment]
    job.priority = priority  # type: ignore[assignment]
    job.reason_for_priority = reason_for_priority  # type: ignore[assignment]

    docker_hub_username = os.getenv("DOCKER_HUB_USERNAME", "aorko123")
    image_tag = f"{docker_hub_username}/{job_id}:latest"
    session.commit_pending = True  # type: ignore[assignment]
    session.commit_image_tag = image_tag  # type: ignore[assignment]

    db.commit()
    db.refresh(job)
    db.refresh(session)

    return {
        "job_id": job.id,
        "session_id": session.session_id,
        "image_tag": image_tag,
        "status": job.status.value,
    }


def complete_commit(db: Session, job_id: str):
    """Worker confirmed the committed image was pushed: move the job into the
    batch pipeline. Idempotent — a second call after the transition is a no-op."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise Exception("Job not found")

    session = (
        db.query(InteractiveSession)
        .filter(InteractiveSession.job_id == job_id)
        .order_by(InteractiveSession.created_at.desc())
        .first()
    )

    # Already moved to batch pipeline (idempotent).
    if job.status == JobStatus.VRAM_ESTIMATION_PENDING:
        if session:
            session.commit_pending = False  # type: ignore[assignment]
            session.commit_image_tag = None  # type: ignore[assignment]
            db.commit()
        return job

    if not session or not session.commit_pending:
        raise Exception("No pending commit for this job")

    session.commit_pending = False  # type: ignore[assignment]
    session.commit_image_tag = None  # type: ignore[assignment]
    job.status = JobStatus.VRAM_ESTIMATION_PENDING  # type: ignore[assignment]
    db.commit()
    db.refresh(job)
    return job


def fail_commit(db: Session, job_id: str, reason: str | None = None):
    """Worker reported the commit/push failed: clear the pending flag and leave
    the job INTERACTIVE_RUNNING so the user can retry. If the job already moved
    to the batch pipeline, ignore (the push actually succeeded; the worker's
    completion report was simply lost)."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise Exception("Job not found")

    # Push actually succeeded; ignore the late failure report.
    if job.status == JobStatus.VRAM_ESTIMATION_PENDING:
        return job

    session = (
        db.query(InteractiveSession)
        .filter(InteractiveSession.job_id == job_id)
        .order_by(InteractiveSession.created_at.desc())
        .first()
    )
    if session:
        session.commit_pending = False  # type: ignore[assignment]
        session.commit_image_tag = None  # type: ignore[assignment]
    if reason:
        job.failure_reason = reason[:2000]  # type: ignore[assignment]
    db.commit()
    db.refresh(job)
    return job
