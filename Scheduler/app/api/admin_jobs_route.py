from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_superuser, get_db
from app.models.job_model import Job, JobPriority, JobStatus
from app.models.user_model import User

router = APIRouter(prefix="/admin/jobs", tags=["admin-jobs"])

# Jobs an admin still needs to see: everything that is not terminal.
ACTIVE_STATUSES = [
    JobStatus.NOT_RUNNABLE,
    JobStatus.IMAGE_READY,
    JobStatus.IMAGE_BUILDING,
    JobStatus.VRAM_ESTIMATION_PENDING,
    JobStatus.RUNNABLE,
    JobStatus.IN_PROGRESS,
    JobStatus.RETRY_NEEDED,
]


class AdminQueueJob(BaseModel):
    id: str
    name: str | None = None
    username: str | None = None
    status: str
    priority: str
    vram_required: float | None = None
    reason_for_priority: str | None = None
    created_at: str | None = None


class AdminJobPriorityUpdate(BaseModel):
    priority: str = Field(description="HIGH to approve, NORMAL to deny")


@router.get("/queue", response_model=list[AdminQueueJob])
def list_queue(
    _: User = Depends(get_current_superuser),
    db: Session = Depends(get_db),
):
    """List all non-terminal jobs, oldest first, with owner usernames."""
    rows = (
        db.query(Job, User.username)
        .outerjoin(User, User.user_id == Job.user_id)
        .filter(Job.status.in_(ACTIVE_STATUSES))
        .order_by(Job.created_at.asc())
        .all()
    )
    return [
        {
            "id": job.id,
            "name": job.name,
            "username": username,
            "status": job.status.value,
            "priority": job.priority.value,
            "vram_required": job.vram_required,
            "reason_for_priority": job.reason_for_priority,
            "created_at": job.created_at.isoformat() if job.created_at else None,
        }
        for job, username in rows
    ]


@router.patch("/{job_id}/priority", response_model=AdminQueueJob)
def set_job_priority(
    job_id: str,
    body: AdminJobPriorityUpdate,
    _: User = Depends(get_current_superuser),
    db: Session = Depends(get_db),
):
    """Approve (HIGH) or deny (NORMAL) a job's priority request.

    REQUESTED can only come from the user's own submission flow, so this
    endpoint accepts just HIGH and NORMAL.
    """
    try:
        priority = JobPriority(body.priority)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="priority must be HIGH or NORMAL"
        ) from None
    if priority == JobPriority.REQUESTED:
        raise HTTPException(
            status_code=400, detail="priority must be HIGH or NORMAL"
        )
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    job.priority = priority
    db.commit()
    db.refresh(job)
    owner = db.query(User).filter(User.user_id == job.user_id).first()
    return {
        "id": job.id,
        "name": job.name,
        "username": owner.username if owner else None,
        "status": job.status.value,
        "priority": job.priority.value,
        "vram_required": job.vram_required,
        "reason_for_priority": job.reason_for_priority,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }
