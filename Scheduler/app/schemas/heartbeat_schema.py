from pydantic import BaseModel

class HeartbeatSchema(BaseModel):
    worker_id: str
    gpu_type:str
    available_vram: float

class HeartbeatResponse(BaseModel):
    status: str
    worker_id: str