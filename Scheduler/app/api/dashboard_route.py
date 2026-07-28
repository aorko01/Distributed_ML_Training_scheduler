"""
dashboard_route.py
──────────────────
Exposes GET /dashboard for the React frontend.

The router has no prefix, so main.py registers it at exactly GET /dashboard.
"""

import datetime
from typing import Any

from fastapi import APIRouter
from app.db.database import SessionLocal
from app.models.job_model import Job, JobStatus
from app.models.worker_model import Worker

router = APIRouter(tags=["dashboard"])


# ---------------------------------------------------------------------------
# Helper: map DB rows → DashboardData dict (TypeScript interface in mockApi.ts)
# ---------------------------------------------------------------------------

def _job_config(job: Job) -> dict[str, Any]:
    """Return a config dict even for jobs created before config was stored."""
    return job.config if isinstance(job.config, dict) else {}


def _project_name(job: Job) -> str:
    config = _job_config(job)
    return config.get("projectTitle") or job.command or "Untitled job"


def _format_date(value: datetime.datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "Unknown"


def _relative_time(value: datetime.datetime | None) -> str:
    if value is None:
        return "unknown time"

    now = datetime.datetime.now(value.tzinfo) if value.tzinfo else datetime.datetime.now()
    seconds = max(0, int((now - value).total_seconds()))
    if seconds < 60:
        return "just now"
    if seconds < 3_600:
        return f"{seconds // 60} min ago"
    if seconds < 86_400:
        return f"{seconds // 3_600}h ago"
    return f"{seconds // 86_400}d ago"


def _duration(start: datetime.datetime | None, end: datetime.datetime | None) -> str:
    if start is None or end is None:
        return "N/A"
    seconds = max(0, int((end - start).total_seconds()))
    hours, remainder = divmod(seconds, 3_600)
    minutes = remainder // 60
    return f"{hours}h {minutes:02d}m"


def build_dashboard_data(db) -> dict:
    """
    Queries the Job and Worker tables and returns a dict that strictly matches
    the DashboardData TypeScript interface expected by the frontend's mockApi.ts.
    Values come from persisted jobs and registered workers.  Fields for which
    the scheduler has no telemetry are explicitly marked unavailable instead
    of being populated with placeholder dashboard data.
    """

    jobs = db.query(Job).order_by(Job.created_at.desc()).all()
    workers = db.query(Worker).all()

    # Map internal JobStatus enum → frontend JobStatus string -----------------
    STATUS_MAP = {
        JobStatus.NOT_RUNNABLE:            "Queued",
        JobStatus.VRAM_ESTIMATION_PENDING: "Provisioning",
        JobStatus.RUNNABLE:                "Queued",
        JobStatus.IN_PROGRESS:             "Running",
        JobStatus.COMPLETED:               "Success",
        JobStatus.FAILED:                  "Failed",
    }

    active_statuses   = {
        JobStatus.NOT_RUNNABLE,
        JobStatus.VRAM_ESTIMATION_PENDING,
        JobStatus.RUNNABLE,
        JobStatus.IN_PROGRESS,
    }
    finished_statuses = {JobStatus.COMPLETED, JobStatus.FAILED}

    active_db   = [j for j in jobs if j.status in active_statuses]
    finished_db = [j for j in jobs if j.status in finished_statuses]

    # activeJobs ---------------------------------------------------------------
    active_jobs_out = []
    queue_pos = 0
    for job in active_db:
        fe_status = STATUS_MAP.get(job.status, "Queued")
        if fe_status == "Queued":
            queue_pos += 1

        active_jobs_out.append({
            "id":                str(job.id),
            "project":           _project_name(job),
            "status":            fe_status,
            # The worker protocol does not report job-level progress yet.
            "progress":          0,
            "queuePos":          queue_pos if fe_status == "Queued" else None,
            "type":              "Training",
            "created":           _format_date(job.created_at),
            "gpu":               (
                f"{job.vram_required:g} GB VRAM required"
                if job.vram_required is not None
                else "VRAM estimation pending"
            ),
            "owner":             "Researcher",
            "description":       _job_config(job).get("description") or "No description provided.",
            "runCommand":        job.command or "",
            "estimatedDuration": (
                f"{job.step_time:g}s per step"
                if job.step_time is not None
                else "Not estimated"
            ),
        })

    # completedJobs ------------------------------------------------------------
    completed_jobs_out = []
    for job in finished_db:
        fe_status = STATUS_MAP.get(job.status, "Failed")
        completed_jobs_out.append({
            "id":       str(job.id),
            "project":  _project_name(job),
            "date":     _format_date(job.updated_at or job.created_at),
            "status":   fe_status,   # "Success" | "Failed"
            "duration": _duration(job.created_at, job.updated_at),
            "gpu":      (
                f"{job.vram_required:g} GB VRAM"
                if job.vram_required is not None
                else "Not recorded"
            ),
            "score":    "N/A",
            "artifact": job.object_key,
        })

    # clusterNodes — only registered workers are returned.  Worker heartbeats
    # are not part of this synchronous endpoint, so a worker is "Registered",
    # not claimed to be online or degraded without evidence.
    cluster_nodes_out = []
    for worker in workers:
        cluster_nodes_out.append({
            "name":        worker.worker_id,
            "status":      "Registered",
            "gpu":         worker.gpu_type,
            "utilization": 0,
            "temperature": "Not reported",
        })

    # activityFeed — synthesised from recent DB rows ---------------------------
    activity_feed_out = []
    for job in (active_db + finished_db)[:4]:
        fe_status = STATUS_MAP.get(job.status, "Queued")
        tone = (
            "success" if fe_status in ("Running", "Success")
            else "warning" if fe_status == "Failed"
            else "info"
        )
        activity_feed_out.append({
            "id":     f"job-{job.id}",
            "label":  f"Job {fe_status.lower()}",
            "detail": _project_name(job),
            "time":   _relative_time(job.updated_at or job.created_at),
            "tone":   tone,
        })

    # overview stats -----------------------------------------------------------
    total_completed = len(finished_db)
    successful      = sum(1 for j in finished_db if j.status == JobStatus.COMPLETED)
    success_rate    = round((successful / total_completed) * 100) if total_completed else 0
    avg_util        = (
        sum(n["utilization"] for n in cluster_nodes_out)
        // max(len(cluster_nodes_out), 1)
    )

    overview_out = {
        "activeJobs":         len(active_db),
        "queuedJobs":         sum(
            1 for j in active_db
            if j.status in {JobStatus.NOT_RUNNABLE, JobStatus.RUNNABLE}
        ),
        "runningJobs":        sum(
            1 for j in active_db if j.status == JobStatus.IN_PROGRESS
        ),
        "clusterUtilization": avg_util,
        "gpuHoursSaved":      0,  # The scheduler does not record this metric.
        "successRate":        success_rate,
    }

    return {
        "overview":      overview_out,
        "activeJobs":    active_jobs_out,
        "completedJobs": completed_jobs_out,
        "clusterNodes":  cluster_nodes_out,
        "activityFeed":  activity_feed_out,
    }


# ---------------------------------------------------------------------------
# GET /dashboard
# ---------------------------------------------------------------------------

@router.get("/dashboard")
def get_dashboard():
    """
    Returns a DashboardData payload consumed by the frontend's getDashboardData().
    Queries the Job and Worker tables; no authentication required for the
    capstone demo.
    """
    db = SessionLocal()
    try:
        return build_dashboard_data(db)
    finally:
        db.close()
