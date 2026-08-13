from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.job_model import Job, JobStatus
from app.models.worker_model import Worker
from app.services.worker_service import get_all_workers


async def get_overview(db: Session) -> dict:
    """Aggregate live cluster stats for the admin Overview page."""
    workers = db.query(Worker).all()

    nodes_total = len(workers)
    nodes_online = 0
    cluster_load = 0.0
    gpus_total = 0
    gpus_allocated = 0

    for worker in workers:
        gpus_total += worker.num_gpus or 0

    live_workers = await get_all_workers(db)
    for worker in live_workers:
        nodes_online += 1
        if worker.get("gpu_load") is not None:
            cluster_load += float(worker["gpu_load"])
        if worker.get("gpus_in_use") is not None:
            gpus_allocated += int(worker["gpus_in_use"])
        elif worker.get("num_gpus") and worker.get("gpu_load") is not None:
            gpus_allocated += round(int(worker["num_gpus"]) * float(worker["gpu_load"]) / 100.0)

    if nodes_online > 0:
        cluster_load = round(cluster_load / nodes_online, 1)

    queue_depth = (
        db.query(func.count(Job.id))
        .filter(
            Job.status.in_([JobStatus.RUNNABLE, JobStatus.VRAM_ESTIMATION_PENDING])
        )
        .scalar()
    )

    return {
        "nodes_online": nodes_online,
        "nodes_total": nodes_total,
        "cluster_load": cluster_load,
        "queue_depth": int(queue_depth),
        "gpus_allocated": int(gpus_allocated),
        "gpus_total": int(gpus_total),
    }


def _completion_time(job: Job) -> datetime | None:
    """Best-effort completion timestamp for a completed job."""
    value = job.updated_at or job.created_at
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket_daily(completed: list[tuple[datetime, str]]) -> list[dict]:
    now = datetime.now(timezone.utc)
    buckets = {
        now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=h): 0
        for h in range(23, -1, -1)
    }
    for ts, _ in completed:
        key = ts.replace(minute=0, second=0, microsecond=0)
        if key in buckets:
            buckets[key] += 1
    return [
        {"label": key.strftime("%H:%M"), "jobs": count}
        for key, count in sorted(buckets.items())
    ]


def _bucket_weekly(completed: list[tuple[datetime, str]]) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    buckets = {today - timedelta(days=d): 0 for d in range(6, -1, -1)}
    for ts, _ in completed:
        key = ts.date()
        if key in buckets:
            buckets[key] += 1
    return [
        {"label": key.strftime("%a"), "jobs": count}
        for key, count in sorted(buckets.items())
    ]


def _bucket_monthly(completed: list[tuple[datetime, str]]) -> list[dict]:
    now = datetime.now(timezone.utc)
    this_week = (now - timedelta(days=now.weekday())).date()
    week_keys = [this_week - timedelta(weeks=w) for w in range(3, -1, -1)]
    buckets = {key: 0 for key in week_keys}
    for ts, _ in completed:
        start = (ts - timedelta(days=ts.weekday())).date()
        if start in buckets:
            buckets[start] += 1
    return [
        {"label": f"Week {idx + 1}", "jobs": buckets[key]}
        for idx, key in enumerate(week_keys)
    ]


def _bucket_yearly(completed: list[tuple[datetime, str]]) -> list[dict]:
    now = datetime.now(timezone.utc)
    # Build the last 12 calendar months
    month_keys = []
    for i in range(12):
        month = now.month - i
        year = now.year
        while month <= 0:
            month += 12
            year -= 1
        month_keys.append(datetime(year, month, 1, tzinfo=timezone.utc))
    buckets = {key: 0 for key in month_keys}
    for ts, _ in completed:
        key = ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if key in buckets:
            buckets[key] += 1
    return [
        {"label": key.strftime("%b"), "jobs": count}
        for key, count in sorted(buckets.items())
    ]


def get_throughput(db: Session) -> dict:
    """Completed-job counts bucketed per period for the admin Overview chart."""
    jobs = (
        db.query(Job)
        .filter(Job.status == JobStatus.COMPLETED)
        .all()
    )
    completed = [
        (ts, job.id)
        for job in jobs
        if (ts := _completion_time(job)) is not None
    ]

    return {
        "daily": _bucket_daily(completed),
        "weekly": _bucket_weekly(completed),
        "monthly": _bucket_monthly(completed),
        "yearly": _bucket_yearly(completed),
    }
