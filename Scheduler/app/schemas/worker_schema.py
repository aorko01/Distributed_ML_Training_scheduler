from pydantic import BaseModel

class WorkerInfo(BaseModel):
    worker_id: str
    gpu_type: str
    num_gpus: int
    total_vram: float
    gpus_in_use: int | None = None
    available_vram: float | None = None
    hostname: str | None = None
    ip_address: str | None = None
    gpu_load: float | None = None
    cpu_load: float | None = None
    mem_usage: float | None = None
    cpu_cores: int | None = None
    total_ram: float | None = None
    total_disk: float | None = None
    available_disk: float | None = None

class WorkerResponse(BaseModel):
    message: str
    worker_id: str
    

class WorkerResource(BaseModel):
    worker_id: str
    gpu_type: str
    free_vram:float

class WorkerNodeInfo(BaseModel):
    worker_id: str
    hostname: str | None = None
    ip_address: str | None = None
    gpu_type: str
    num_gpus: int
    total_vram: float
    gpus_in_use: int | None = None
    available_vram: float | None = None
    gpu_load: float | None = None
    cpu_load: float | None = None
    mem_usage: float | None = None
    cpu_cores: int | None = None
    total_ram: float | None = None
    total_disk: float | None = None
    available_disk: float | None = None
    status: str
    running_jobs: int
    first_seen: str | None = None
    last_registered: str | None = None