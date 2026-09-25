import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from app.core.redis import redis_client
from app.db.database import SessionLocal
from app.models.job_model import Job, JobStatus
from app.services.image_builder_service import (
    ATTEMPT_HEARTBEAT_PREFIX,
    HEARTBEAT_TIMEOUT_SECONDS as IMAGE_BUILD_HEARTBEAT_TIMEOUT_SECONDS,
)

logger = logging.getLogger("uvicorn.error")

# A job sent to a worker is marked RETRY_NEEDED when that worker has not sent a
# heartbeat for this long.
STALL_TIMEOUT_SECONDS = 3 * 60

# How often the watcher scans for stalled jobs.
SCAN_INTERVAL_SECONDS = max(1, int(os.getenv("WATCHDOG_SCAN_INTERVAL_SECONDS", "5")))

# A queued interactive image has no lease to expire, so surface an absent or
# misconfigured builder explicitly. Keep warnings rate-limited per revision.
INTERACTIVE_QUEUE_WARNING_SECONDS = max(
    1, int(os.getenv("INTERACTIVE_QUEUE_WARNING_SECONDS", "30"))
)
INTERACTIVE_QUEUE_WARNING_INTERVAL_SECONDS = max(
    1, int(os.getenv("INTERACTIVE_QUEUE_WARNING_INTERVAL_SECONDS", "60"))
)
_interactive_queue_warning_at: dict[str, float] = {}

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


async def _last_image_build_heartbeat_ts(attempt_id: str) -> int | None:
    raw = await redis_client.get(ATTEMPT_HEARTBEAT_PREFIX + attempt_id)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _mark_retry_needed(db: SessionLocal, job: Job) -> None:
    """Requeue the job as RETRY_NEEDED (an infrastructure issue, not a user error)."""
    from app.models.interactive_runtime_model import WorkerAssignment
    if db.query(WorkerAssignment).filter_by(job_id=job.id).first():
        return
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
    """Requeue stalled training jobs and expired image-build attempts.

    Heartbeat data lives in Redis (in-memory) rather than psql, so the scan is
    fast. Training jobs become RETRY_NEEDED; image builds return to
    NOT_RUNNABLE so a different builder can claim them.
    """
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
        marked += await _requeue_stalled_image_builds(db, now)
        from app.services.interactive_workspace_service import expire
        marked += expire(db)
        from app.services.workspace_editor_service import reconcile as reconcile_workspaces
        marked += reconcile_workspaces(db)
        _warn_unclaimed_interactive_builds(db)
        return marked
    finally:
        db.close()


def _age_seconds(timestamp: datetime | None, now: int) -> float | None:
    if timestamp is None:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return now - timestamp.timestamp()


async def _requeue_stalled_image_builds(db: SessionLocal, now: int) -> int:
    """Expire image-build leases without touching a newer replacement attempt.

    The conditional UPDATE is the database-side fence.  Even if a completion
    or reassignment races this scan, only the exact attempt inspected here can
    be moved back to NOT_RUNNABLE.
    """
    builds = db.query(Job).filter(Job.status == JobStatus.IMAGE_BUILDING).all()
    marked = 0

    for job in builds:
        attempt_id = job.image_build_attempt_id
        last = (
            await _last_image_build_heartbeat_ts(attempt_id)
            if attempt_id
            else None
        )
        age = now - last if last is not None else _age_seconds(
            job.image_build_started_at or job.updated_at or job.created_at,
            now,
        )
        if age is None or age < IMAGE_BUILD_HEARTBEAT_TIMEOUT_SECONDS:
            continue

        filters = [
            Job.id == job.id,
            Job.status == JobStatus.IMAGE_BUILDING,
        ]
        if attempt_id is None:
            filters.append(Job.image_build_attempt_id.is_(None))
        else:
            filters.append(Job.image_build_attempt_id == attempt_id)

        updated = (
            db.query(Job)
            .filter(*filters)
            .update(
                {
                    Job.status: JobStatus.NOT_RUNNABLE,
                    Job.image_build_excluded_builder_id: job.image_builder_id,
                    Job.image_build_excluded_until: datetime.fromtimestamp(
                        now, tz=timezone.utc
                    ) + timedelta(seconds=IMAGE_BUILD_HEARTBEAT_TIMEOUT_SECONDS),
                    Job.image_builder_id: None,
                    Job.image_build_attempt_id: None,
                    Job.image_build_started_at: None,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        if not updated:
            continue

        if attempt_id:
            await redis_client.delete(ATTEMPT_HEARTBEAT_PREFIX + attempt_id)
        marked += 1

    return marked


def _warn_unclaimed_interactive_builds(db: SessionLocal) -> None:
    """Warn when no builder has claimed an interactive revision for too long."""
    from app.models.interactive_workspace_model import InteractiveImageRevision

    timestamp = datetime.now(timezone.utc)
    monotonic_now = time.monotonic()
    revisions = (
        db.query(InteractiveImageRevision)
        .filter(InteractiveImageRevision.state == "QUEUED")
        .all()
    )
    queued_ids = {revision.id for revision in revisions}
    for revision_id in list(_interactive_queue_warning_at):
        if revision_id not in queued_ids:
            _interactive_queue_warning_at.pop(revision_id, None)

    for revision in revisions:
        created_at = revision.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_seconds = max(0, int((timestamp - created_at).total_seconds()))
        last_warning = _interactive_queue_warning_at.get(revision.id)
        if age_seconds < INTERACTIVE_QUEUE_WARNING_SECONDS or (
            last_warning is not None
            and monotonic_now - last_warning < INTERACTIVE_QUEUE_WARNING_INTERVAL_SECONDS
        ):
            continue
        _interactive_queue_warning_at[revision.id] = monotonic_now
        logger.warning(
            "interactive_workspace queue_unclaimed revision_id=%s workspace_id=%s queued_seconds=%s attempt_count=%s excluded_builder_id=%s; verify the image builder uses compose.interactive.yaml and is calling /internal/interactive/builds/claim",
            revision.id,
            revision.workspace_id,
            age_seconds,
            revision.attempt_count,
            revision.excluded_builder_id,
        )


async def run_stall_watcher():
    """Background loop that periodically scans for stalled jobs."""
    while True:
        try:
            marked = await check_stalled_jobs()
            if marked:
                logger.warning("watchdog requeued_stalled count=%s", marked)
        except Exception as e:
            logger.exception("watchdog stall_check_failed: %s", e)
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)
