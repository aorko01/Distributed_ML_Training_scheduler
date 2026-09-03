from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db.database import SessionLocal
from app.models.user_model import User
from app.models.job_model import Job
from app.api.deps import get_current_active_user

router = APIRouter(prefix="/admin", tags=["admin"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/users")
def list_users(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """List all users with computed job count and GPU hours."""
    users = db.query(User).order_by(User.created_at).all()

    # Aggregate stats per user
    rows = (
        db.query(
            Job.user_id,
            func.count(Job.id),
            func.coalesce(func.sum(Job.gpu_hour), 0.0),
        )
        .group_by(Job.user_id)
        .all()
    )
    stats = {r[0]: (int(r[1]), round(float(r[2]), 4)) for r in rows}

    return [
        {
            "user_id": u.user_id,
            "username": u.username,
            "name": u.name,
            "email": u.email,
            "is_active": u.is_active,
            "is_superuser": u.is_superuser,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "jobs_count": stats.get(u.user_id, (0, 0.0))[0],
            "gpu_hours": stats.get(u.user_id, (0, 0.0))[1],
        }
        for u in users
    ]


@router.get("/jobs")
def list_all_jobs(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """List all jobs with joined user info."""
    rows = (
        db.query(Job, User.username)
        .join(User, User.user_id == Job.user_id)
        .order_by(Job.created_at.desc())
        .all()
    )
    return [
        {
            "id": job.id,
            "name": job.name,
            "user_id": job.user_id,
            "username": username,
            "priority": job.priority.value,
            "status": job.status.value,
            "vram_required": job.vram_required,
            "reason_for_priority": job.reason_for_priority,
            "build_type": job.build_type,
            "created_at": job.created_at.isoformat() if job.created_at else None,
        }
        for job, username in rows
    ]
