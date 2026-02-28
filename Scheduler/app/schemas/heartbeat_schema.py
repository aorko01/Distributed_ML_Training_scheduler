from pydantic import BaseModel

class HeartbeatSchema(BaseModel):
    worker_id: str
    gpu_type:str
    available_vram: float