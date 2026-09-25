from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_superuser, get_db
from app.models.job_model import Job
from app.models.user_model import User

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


class AdminUserResponse(BaseModel):
    user_id: str
    username: str
    email: str
    name: str | None = None
    is_active: bool
    is_superuser: bool
    jobs_count: int
    gpu_hours: float
    created_at: str | None = None


class AdminUserUpdate(BaseModel):
    is_active: bool | None = None
    is_superuser: bool | None = None


def _to_response(user: User, jobs_count: int, gpu_hours: float) -> dict:
    return {
        "user_id": user.user_id,
        "username": user.username,
        "email": user.email,
        "name": user.name,
        "is_active": bool(user.is_active),
        "is_superuser": bool(user.is_superuser),
        "jobs_count": int(jobs_count),
        "gpu_hours": round(float(gpu_hours), 4),
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.get("", response_model=list[AdminUserResponse])
def list_users(
    _: User = Depends(get_current_superuser),
    db: Session = Depends(get_db),
):
    """List every user with real job counts and GPU hours."""
    users = db.query(User).order_by(User.created_at.asc()).all()
    result = []
    for user in users:
        jobs_count = (
            db.query(func.count(Job.id)).filter(Job.user_id == user.user_id).scalar()
            or 0
        )
        gpu_hours = (
            db.query(func.coalesce(func.sum(Job.gpu_hour), 0.0))
            .filter(Job.user_id == user.user_id)
            .scalar()
            or 0.0
        )
        result.append(_to_response(user, jobs_count, gpu_hours))
    return result


@router.patch("/{user_id}", response_model=AdminUserResponse)
def update_user(
    user_id: str,
    body: AdminUserUpdate,
    current: User = Depends(get_current_superuser),
    db: Session = Depends(get_db),
):
    """Enable/disable an account or grant/revoke admin."""
    user = db.query(User).filter(User.user_id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.user_id == current.user_id and (
        body.is_active is False or body.is_superuser is False
    ):
        raise HTTPException(
            status_code=400, detail="Cannot deactivate or demote your own account"
        )
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.is_superuser is not None:
        user.is_superuser = body.is_superuser
    db.commit()
    db.refresh(user)
    jobs_count = (
        db.query(func.count(Job.id)).filter(Job.user_id == user.user_id).scalar() or 0
    )
    gpu_hours = (
        db.query(func.coalesce(func.sum(Job.gpu_hour), 0.0))
        .filter(Job.user_id == user.user_id)
        .scalar()
        or 0.0
    )
    return _to_response(user, jobs_count, gpu_hours)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: str,
    current: User = Depends(get_current_superuser),
    db: Session = Depends(get_db),
):
    """Delete a user. Refused when the user still owns jobs or is yourself."""
    user = db.query(User).filter(User.user_id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.user_id == current.user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    owned = db.query(func.count(Job.id)).filter(Job.user_id == user.user_id).scalar()
    if owned:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete a user that still owns jobs",
        )
    db.delete(user)
    db.commit()
    return None
