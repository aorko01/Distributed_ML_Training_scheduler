import asyncio
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.redis import redis_client
from app.db.database import SessionLocal
from app.models.job_model import Job, JobStatus, BATCH_JOB_STATUSES
from app.models.interactive_session_model import (
    InteractiveSession,
    InteractiveSessionStatus,
)

# A job sent to a worker is marked RETRY_NEEDED when that worker has not sent a
# heartbeat for this long.
STALL_TIMEOUT_SECONDS = 3 * 60

# A job in PENDING state (builder started but hasn't finished) is reset to
# NOT_RUNNABLE if it stays stuck for longer than this.
PENDING_STALL_TIMEOUT_SECONDS = 30 * 60

# How often the watcher scans for stalled jobs.
SCAN_INTERVAL_SECONDS = 30

# An interactive session is considered stalled when neither the worker nor the
# container has sent a heartbeat for this long.
INTERACTIVE_STALL_TIMEOUT_SECONDS = 90

# Statuses in which a job is actively assigned to / running on a worker.
RUNNING_STATUSES = {JobStatus.IN_PROGRESS, JobStatus.VRAM_ESTIMATION_PENDING}

WORKER_HEARTBEAT_PREFIX = "worker_heartbeat:"
JOB_WORKER_PREFIX = "job_worker:"
INTERACTIVE_HEARTBEAT_PREFIX = "interactive_heartbeat:"


async def _last_heartbeat_ts(worker_id: str) -> int | None:
    raw = await redis_client.get(WORKER_HEARTBEAT_PREFIX + worker_id)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _mark_retry_needed(db: Session, job: Job) -> None:
    """Requeue the job as RETRY_NEEDED (an infrastructure issue, not a user error)."""
    job.status = JobStatus.RETRY_NEEDED  # type: ignore[assignment]
    job.device = None

    if job.started_at is not None:
        started_at = job.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        elapsed_seconds = (datetime.now(timezone.utc) - started_at).total_seconds()
        job.gpu_hour = max(elapsed_seconds, 0.0) / 3600.0

    job.failure_reason = (  # type: ignore[assignment]
        job.failure_reason
        or f"Worker did not send a heartbeat for {STALL_TIMEOUT_SECONDS} seconds"
    )

    db.commit()
    db.refresh(job)


async def _cleanup_stale_job_workers(db: Session, running_ids: set[str]) -> None:
    """Drop job->worker mappings for jobs that are no longer running so Redis does
    not accumulate stale keys for completed/failed/requeued jobs."""
    keys = await redis_client.keys(JOB_WORKER_PREFIX + "*")
    stale = [
        key
        for key in keys
        if key[len(JOB_WORKER_PREFIX):] not in running_ids
    ]
    if stale:
        await redis_client.delete(*stale)


async def check_stalled_jobs() -> int:
    """Mark jobs as RETRY_NEEDED when the worker they were sent to has not sent a
    heartbeat for STALL_TIMEOUT_SECONDS. Heartbeat data lives in Redis (in-memory)
    rather than psql, so this scan is fast."""
    db = SessionLocal()
    try:
        running = db.query(Job).filter(Job.status.in_(RUNNING_STATUSES)).all()
        running_ids = {job.id for job in running}
        now = int(time.time())
        marked = 0

        for job in running:
            worker_id = await redis_client.get(JOB_WORKER_PREFIX + job.id)  # type: ignore[arg-type]
            if worker_id is None:
                # Job was pulled before job->worker tracking existed; do not touch it.
                continue

            last = await _last_heartbeat_ts(str(worker_id))
            if last is None or (now - last) >= STALL_TIMEOUT_SECONDS:
                _mark_retry_needed(db, job)
                await redis_client.delete(JOB_WORKER_PREFIX + job.id)  # type: ignore[arg-type]
                marked += 1

        await _cleanup_stale_job_workers(db, running_ids)
        return marked
    finally:
        db.close()


async def check_stalled_interactive_sessions() -> int:
    """Detect interactive sessions whose worker or container has gone quiet
    and either finalize them (STOPPING) or requeue them (DEPLOYING/RUNNING).

    Liveness is tracked via two Redis keys:
    - worker_heartbeat:<worker_id>  (written by the worker heartbeat)
    - interactive_heartbeat:<session_id>  (written by the worker heartbeat
      and by update_session_ip when the container reports RUNNING)

    If both are fresh the session is healthy. If either is stale:
    - STOPPING sessions are finalized (STOPPED + job INTERACTIVE_STOPPED),
      mirroring the normal stop_session bookkeeping.
    - DEPLOYING/RUNNING sessions are requeued (PENDING + job INTERACTIVE_READY)
      so another idle worker can pick them up. The old job_worker key is
      cleaned up by _cleanup_stale_job_workers in the same watcher cycle.
    """
    db = SessionLocal()
    try:
        sessions = (
            db.query(InteractiveSession)
            .filter(InteractiveSession.status.in_([
                InteractiveSessionStatus.DEPLOYING,
                InteractiveSessionStatus.RUNNING,
                InteractiveSessionStatus.STOPPING,
            ]))
            .all()
        )
        now = int(time.time())
        handled = 0

        for session in sessions:
            worker_alive = False
            container_alive = False

            if session.worker_id:
                last_worker = await _last_heartbeat_ts(str(session.worker_id))
                if last_worker is not None and (now - last_worker) < INTERACTIVE_STALL_TIMEOUT_SECONDS:
                    worker_alive = True

            if session.session_id:
                raw = await redis_client.get(INTERACTIVE_HEARTBEAT_PREFIX + str(session.session_id))
                if raw is not None:
                    try:
                        last_container = int(raw)
                        if (now - last_container) < INTERACTIVE_STALL_TIMEOUT_SECONDS:
                            container_alive = True
                    except (TypeError, ValueError):
                        pass

            if worker_alive and container_alive:
                continue

            if session.status == InteractiveSessionStatus.STOPPING:
                # Finalize: the session was being stopped but the worker/container
                # went down. Mark it STOPPED and close out the job.
                session.headscale_ip = None
                session.status = InteractiveSessionStatus.STOPPED  # type: ignore[assignment]
                session.stopped_at = datetime.now(timezone.utc)
                job = db.query(Job).filter(Job.id == session.job_id).first()
                if job and job.status not in BATCH_JOB_STATUSES:
                    job.status = JobStatus.INTERACTIVE_STOPPED  # type: ignore[assignment]
                db.commit()
                handled += 1
            else:
                # Requeue: the session was DEPLOYING or RUNNING but the worker or
                # container went down. Reset to PENDING so another idle worker
                # can pick it up.
                old_worker_id = session.worker_id
                session.worker_id = None
                session.headscale_ip = None
                session.last_worker_id = old_worker_id
                session.status = InteractiveSessionStatus.PENDING  # type: ignore[assignment]
                job = db.query(Job).filter(Job.id == session.job_id).first()
                if job and job.status not in BATCH_JOB_STATUSES:
                    job.status = JobStatus.INTERACTIVE_READY  # type: ignore[assignment]
                    job.failure_reason = (  # type: ignore[assignment]
                        job.failure_reason
                        or f"Interactive session requeued: worker/container went stale "
                        f"(last worker {old_worker_id})"
                    )
                db.commit()
                handled += 1

        return handled
    finally:
        db.close()


async def check_stalled_pending_jobs() -> int:
    """Reset PENDING jobs that have been stuck for longer than
    PENDING_STALL_TIMEOUT_SECONDS back to NOT_RUNNABLE so they can be
    retried. This handles cases where the builder crashed or lost track of
    a job without notifying the scheduler."""
    db = SessionLocal()
    try:
        pending_jobs = (
            db.query(Job)
            .filter(Job.status == JobStatus.PENDING)
            .all()
        )
        now = int(time.time())
        reset = 0

        for job in pending_jobs:
            # Fall back to created_at when updated_at was never set (e.g. the
            # builder crashed before touching the row).
            reference_ts = job.updated_at or job.created_at
            if reference_ts is None:
                continue
            updated_ts = reference_ts.timestamp()
            if (now - updated_ts) >= PENDING_STALL_TIMEOUT_SECONDS:
                job.status = JobStatus.NOT_RUNNABLE  # type: ignore[assignment]
                job.failure_reason = (  # type: ignore[assignment]
                    job.failure_reason
                    or f"PENDING job stalled for more than {PENDING_STALL_TIMEOUT_SECONDS} seconds"
                )
                db.commit()
                db.refresh(job)
                reset += 1

        return reset
    finally:
        db.close()


async def run_stall_watcher():
    """Background loop that periodically scans for stalled jobs and
    interactive sessions."""
    while True:
        try:
            marked = await check_stalled_jobs()
            if marked:
                print(f"[watchdog] marked {marked} stalled job(s) as RETRY_NEEDED")
            pending_reset = await check_stalled_pending_jobs()
            if pending_reset:
                print(f"[watchdog] reset {pending_reset} stalled PENDING job(s) to NOT_RUNNABLE")
            interactive_marked = await check_stalled_interactive_sessions()
            if interactive_marked:
                print(f"[watchdog] requeued/finalized {interactive_marked} stalled interactive session(s)")
        except Exception as e:
            print(f"[watchdog] stall check failed: {e}")
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)
