from pydantic import BaseModel


class ResourceOptions(BaseModel):
    gpu_types: list[str]
    vram_options: list[float]
    ram_options: list[float]
    core_options: list[int]
    disk_options: list[float]


class ResourceConfig(BaseModel):
    gpu_type: str | None = None
    gpu_vram: float | None = None
    cpu_ram: float | None = None
    cpu_cores: float | None = None
    disk: float | None = None
    op: str = "ge"  # "ge" | "eq"


class ResourceSummaryResponse(BaseModel):
    matching_nodes: int
    avg_running_jobs: float
    queue_total: int
    queue_open: int


class ResourceRequestCreate(BaseModel):
    gpu_type: str | None = None
    gpu_vram: float | None = None
    cpu_ram: float | None = None
    cpu_cores: float | None = None
    disk: float | None = None
    notes: str | None = None


class ResourceRequestResponse(BaseModel):
    request_id: str
    status: str
    message: str
    queue_open: int
