from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict
from app.models.job_model import JobPriority


class JobCreate(BaseModel):
    user_id: str
    object_key: str
    command: str
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

    model_config = ConfigDict(from_attributes=True)

class Job_status_to_vram_estimation_pending(BaseModel):
    job_id: str

class JobIDRequest(BaseModel):
    job_id: str

class VramEstimationReport(BaseModel):
    job_id: str
    vram_required: float
    step_time: float