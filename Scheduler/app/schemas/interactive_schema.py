from pydantic import BaseModel
from typing import Optional
import enum


class InteractiveSessionStatus(str, enum.Enum):
    PENDING = "PENDING"
    DEPLOYING = "DEPLOYING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class CreateInteractiveSessionRequest(BaseModel):
    base_job_id: str


class CreateInteractiveSessionResponse(BaseModel):
    session_id: str
    job_id: str
    status: InteractiveSessionStatus


class InteractiveSessionReport(BaseModel):
    """Worker -> Scheduler report of a deployed interactive container."""
    session_id: str
    headscale_ip: Optional[str] = None
    status: str  # "RUNNING" | "FAILED" | "STOPPED"


class InteractiveSessionOut(BaseModel):
    session_id: str
    job_id: str
    base_job_id: Optional[str] = None
    worker_id: Optional[str] = None
    headscale_ip: Optional[str] = None
    status: InteractiveSessionStatus
    created_at: Optional[str] = None

    class Config:
        from_attributes = True
