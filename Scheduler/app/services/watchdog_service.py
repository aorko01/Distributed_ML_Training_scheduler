import asyncio
import time
from datetime import datetime, timezone

from app.core.redis import redis_client
from app.db.database import SessionLocal
from app.models.job_model import Job, JobStatus

# A job sent to a worker is marked RETRY_NEEDED when that worker has not sent a
# heartbeat for this long.
STALL_TIMEOUT_SECONDS = 3 * 60

# How often the watcher scans for stalled jobs.
SCAN_INTERVAL_SECONDS = 30

# Statuses in which a job is actively assigned to / running on a worker.
RUNNING_STATUSES = {JobStatus.IN_PROGRESS, JobStatus.VRAM_ESTIMATION_PENDING}

WORKER_HEARTBEAT_PREFIX = "worker_heartbeat:"
JOB_WORKER_PREFIX = "job_worker:"


async def _last_heartbeat_ts(worker_id: str) -> int | None:
    raw = await redis_client.get(WORKER_HEARTBEAT_PREFIX + worker_id)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _mark_retry_needed(db: SessionLocal, job: Job) -> None:
    """Requeue the job as RETRY_NEEDED (an infrastructure issue, not a user error)."""
    job.status = JobStatus.RETRY_NEEDED
    job.device = None

    if job.started_at is not None:
        started_at = job.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        elapsed_seconds = (datetime.now(timezone.utc) - started_at).total_seconds()
        job.gpu_hour = max(elapsed_seconds, 0.0) / 3600.0

    job.failure_reason = (
        job.failure_reason
        or f"Worker did not send a heartbeat for {STALL_TIMEOUT_SECONDS} seconds"
    )

    db.commit()
    db.refresh(job)


async def _cleanup_stale_job_workers(db: SessionLocal, running_ids: set[str]) -> None:
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
            worker_id = await redis_client.get(JOB_WORKER_PREFIX + job.id)
            if worker_id is None:
                # Job was pulled before job->worker tracking existed; do not touch it.
                continue

            last = await _last_heartbeat_ts(worker_id)
            if last is None or (now - last) >= STALL_TIMEOUT_SECONDS:
                _mark_retry_needed(db, job)
                await redis_client.delete(JOB_WORKER_PREFIX + job.id)
                marked += 1

        await _cleanup_stale_job_workers(db, running_ids)
        return marked
    finally:
        db.close()


async def run_stall_watcher():
    """Background loop that periodically scans for stalled jobs."""
    while True:
        try:
            marked = await check_stalled_jobs()
            if marked:
                print(f"[watchdog] marked {marked} stalled job(s) as RETRY_NEEDED")
        except Exception as e:
            print(f"[watchdog] stall check failed: {e}")
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)