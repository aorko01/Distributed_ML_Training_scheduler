from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_active_user
from app.models.user_model import User
from app.schemas.interactive_schema import (
    CreateInteractiveSessionRequest,
    CreateInteractiveSessionResponse,
    InteractiveSessionReport,
)
from app.services import interactive_service
from app.services.ephemeral_password_service import verify_ephemeral_password

router = APIRouter()


class EphemeralPasswordVerifyRequest(BaseModel):
    username: str
    password: str


class EphemeralPasswordVerifyResponse(BaseModel):
    session_id: str
    headscale_ip: str


@router.post("/create", response_model=CreateInteractiveSessionResponse)
def create_interactive_session(
    body: CreateInteractiveSessionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        return interactive_service.create_session(db, current_user.user_id, body.base_job_id)
    except interactive_service.InteractiveServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/report_ip")
def report_ip(
    body: InteractiveSessionReport,
    db: Session = Depends(get_db),
):
    """Worker callback: reports the container's tailnet IP (or stopped/failed)."""
    try:
        return interactive_service.update_session_ip(
            db, body.session_id, body.headscale_ip, body.status
        )
    except interactive_service.InteractiveServiceError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db)):
    return interactive_service.list_sessions(db)


@router.get("/sessions/active")
def get_active_session(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    session = interactive_service.get_active_session_for_user(db, current_user.user_id)
    if not session:
        raise HTTPException(status_code=404, detail="No active interactive session")
    return {
        "session_id": session.session_id,
        "headscale_ip": session.headscale_ip,
    }


@router.post("/sessions/verify-ephemeral", response_model=EphemeralPasswordVerifyResponse)
def verify_ephemeral_password_route(
    body: EphemeralPasswordVerifyRequest = Body(...),
):
    """Gateway calls this (no JWT) to validate a one-time SSH password.

    The password was issued by the Scheduler when the user requested connect
    info for a running interactive session. On success the gateway learns the
    session_id and headscale_ip to proxy the SSH channel to.
    """
    result = verify_ephemeral_password(body.username, body.password)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid or expired one-time password")
    return result


@router.get("/{session_id}")
def get_session(
    session_id: str,
    db: Session = Depends(get_db),
):
    record = interactive_service.get_session(db, session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Interactive session not found")
    return {
        "session_id": record.session_id,
        "job_id": record.job_id,
        "user_id": record.user_id,
        "base_job_id": record.base_job_id,
        "worker_id": record.worker_id,
        "headscale_ip": record.headscale_ip,
        "status": record.status.value,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


@router.post("/{session_id}/stop")
def stop_session(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    record = interactive_service.get_session(db, session_id)
    if not record:
        raise HTTPException(status_code=404, detail="Interactive session not found")
    if record.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Not your session")
    try:
        return interactive_service.stop_session(db, session_id)
    except interactive_service.InteractiveServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
