from pydantic import BaseModel

class HeartbeatSchema(BaseModel):
    worker_id: str
    gpu_type:str
    available_vram: float
    gpus_in_use: int | None = None
    gpu_load: float | None = None
    cpu_load: float | None = None
    mem_usage: float | None = None
    total_disk: float | None = None
    available_disk: float | None = None
    hostname: str | None = None
    ip_address: str | None = None

class HeartbeatResponse(BaseModel):
    status: str
    worker_id: str