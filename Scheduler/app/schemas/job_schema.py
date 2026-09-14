from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Literal
from app.models.job_model import JobPriority


class JobCreate(BaseModel):
    user_id: str
    object_key: str
    command: str
    resume_command: Optional[str] = None
    docker_base_image: str
    config: Optional[Dict] = None
    priority: JobPriority = JobPriority.NORMAL
    reason_for_priority: Optional[str] = None

class JobResponse(BaseModel):
    id: str
    user_id: str
    status: str
    priority: JobPriority
    reason_for_priority: Optional[str] = None
    resume_command: Optional[str] = None
    device: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class Job_status_to_vram_estimation_pending(BaseModel):
    job_id: str

class JobIDRequest(BaseModel):
    job_id: str

class JobResumeRequest(BaseModel):
    job_id: str
    worker_id: str
    # GPU type of the requesting worker, checked against job.device so a job
    # being run on a different device is never resumed by this worker.
    device: Optional[str] = None

class VramEstimationReport(BaseModel):
    job_id: str
    vram_required: float
    ram_required: float
    step_time: float

class JobFailureReport(BaseModel):
    job_id: str
    # "user" -> job FAILED (build/training code error), "system" -> job RETRY_NEEDED (infra issue)
    failure_type: Literal["user", "system"]
    failure_reason: Optional[str] = None