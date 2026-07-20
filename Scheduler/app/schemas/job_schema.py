from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict


class JobCreate(BaseModel):
    object_key: str
    script_path: str
    config: Optional[Dict] = None
    
class JobResponse(BaseModel):
    id: str
    status: str

    model_config = ConfigDict(from_attributes=True)

class Job_status_to_vram_estimation_pending(BaseModel):
    job_id: str

class JobIDRequest(BaseModel):
    job_id: str