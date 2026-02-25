from pydantic import BaseModel

class WorkerInfo(BaseModel):
    worker_id: str
    mac_address: str
    gpu_type: str
    num_gpus: int
    total_vram: float

class WorkerResponse(BaseModel):
    message: str
    worker_id: str