"""
dashboard_route.py
──────────────────
Exposes GET /dashboard for the Vite/React frontend.

The router has NO prefix, so it is registered in main.py as:
    app.include_router(dashboard_router)
which makes the endpoint resolve to exactly GET /dashboard.

build_dashboard_data() is also exported so jobs_route.py can import and reuse
it when returning the updated state after a POST /jobs submission.
"""

import datetime
from typing import List

from fastapi import APIRouter
from app.db.database import SessionLocal
from app.models.job_model import Job, JobStatus
from app.models.worker_model import Worker

router = APIRouter(tags=["dashboard"])


# ---------------------------------------------------------------------------
# Helper: map DB rows → DashboardData dict (TypeScript interface in mockApi.ts)
# ---------------------------------------------------------------------------

def build_dashboard_data(db) -> dict:
    """
    Queries the Job and Worker tables and returns a dict that strictly matches
    the DashboardData TypeScript interface expected by the frontend's mockApi.ts.
    Synthetic / fallback values are used for fields the DB schema does not track
    (e.g. GPU temperature, per-job duration, score).
    """

    jobs: List[Job] = db.query(Job).order_by(Job.created_at.desc()).all()
    workers: List[Worker] = db.query(Worker).all()

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
            "project":           job.command[:40] if job.command else "Untitled Job",
            "status":            fe_status,
            "progress":          50 if fe_status == "Running" else 0,
            "queuePos":          queue_pos if fe_status == "Queued" else None,
            "type":              "Training",
            "created":           (
                job.created_at.strftime("%Y-%m-%d")
                if job.created_at
                else str(datetime.date.today())
            ),
            "gpu":               "Auto-sized GPU slot",
            "owner":             "Researcher",
            "description":       f"object_key: {job.object_key}",
            "runCommand":        job.command or "",
            "estimatedDuration": "Estimated after profiling",
        })

    # completedJobs ------------------------------------------------------------
    completed_jobs_out = []
    for job in finished_db:
        fe_status = STATUS_MAP.get(job.status, "Failed")
        completed_jobs_out.append({
            "id":       str(job.id),
            "project":  job.command[:40] if job.command else "Completed Job",
            "date":     (
                job.updated_at.strftime("%Y-%m-%d")
                if job.updated_at
                else str(datetime.date.today())
            ),
            "status":   fe_status,   # "Success" | "Failed"
            "duration": "N/A",
            "gpu":      "N/A",
            "score":    "N/A",
            "artifact": job.object_key or "N/A",
        })

    # clusterNodes — derived from registered workers; fallback if empty --------
    NODE_STATUSES = ["Online", "Online", "Degraded"]  # simple visual cycle
    cluster_nodes_out = []
    for i, w in enumerate(workers):
        cluster_nodes_out.append({
            "name":        f"gpu-w{str(i + 1).zfill(2)}",
            "status":      NODE_STATUSES[i % len(NODE_STATUSES)],
            "gpu":         w.gpu_type or "Unknown GPU",
            "utilization": min(95, 40 + (i * 17) % 56),
            "temperature": f"{60 + (i * 7) % 20}°C",
        })

    if not cluster_nodes_out:
        cluster_nodes_out = [
            {"name": "gpu-a01", "status": "Online",   "gpu": "RTX 4090", "utilization": 84, "temperature": "69°C"},
            {"name": "gpu-a02", "status": "Online",   "gpu": "A100",     "utilization": 76, "temperature": "63°C"},
            {"name": "gpu-b07", "status": "Degraded", "gpu": "RTX 3090", "utilization": 59, "temperature": "74°C"},
            {"name": "gpu-c11", "status": "Online",   "gpu": "L40S",     "utilization": 91, "temperature": "71°C"},
        ]

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
            "id":     f"evt-{job.id[:8]}",
            "label":  f"Job {fe_status.lower()}",
            "detail": f"{job.command[:60] if job.command else 'Job'} — {job.object_key or ''}",
            "time":   "recently",
            "tone":   tone,
        })

    if not activity_feed_out:
        activity_feed_out = [
            {
                "id":     "evt-default",
                "label":  "Scheduler ready",
                "detail": "No jobs have been submitted yet.",
                "time":   "just now",
                "tone":   "info",
            }
        ]

    # overview stats -----------------------------------------------------------
    total_completed = len(finished_db)
    successful      = sum(1 for j in finished_db if j.status == JobStatus.COMPLETED)
    success_rate    = round((successful / total_completed) * 100) if total_completed else 100
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
        "gpuHoursSaved":      successful * 2,   # rough heuristic
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
