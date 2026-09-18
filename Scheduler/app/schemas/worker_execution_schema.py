from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

UUID = Annotated[
    str,
    Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GPU(Strict):
    uuid: Annotated[str, Field(min_length=5, max_length=80)]
    model: Annotated[str, Field(max_length=128)]
    memory_gb: Annotated[float, Field(ge=0, le=10000, allow_inf_nan=False)]
    busy: bool
    processes: Annotated[list[int], Field(max_length=512)]


class Inventory(Strict):
    complete: bool
    observed_at: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    mode: Literal[
        "RECONCILING",
        "AVAILABLE",
        "BATCH_ACTIVE",
        "INTERACTIVE_RESERVED",
        "INTERACTIVE_ACTIVE",
        "CLEANING",
        "UNCERTAIN",
    ]
    available_slots: Annotated[int, Field(ge=0, le=64)]
    local_assignments: Annotated[list[UUID], Field(max_length=64)]
    free_vram_gb: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    free_ram_gb: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    free_disk_gb: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    cpu_cores: Annotated[int, Field(ge=0, le=4096)]
    # Optional host telemetry keeps the protocol backwards compatible with
    # Workers that predate the dashboard's legacy node fields.
    hostname: str | None = None
    ip_address: str | None = None
    cpu_load: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None
    mem_usage: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None
    total_ram: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    total_disk: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    available_disk: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    gpu_load: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None
    gpus_in_use: Annotated[int, Field(ge=0, le=64)] | None = None
    platform: Literal["linux/amd64", "linux/arm64"]
    nvidia_runtime: bool
    quota_supported: bool
    interactive_ready: bool
    gpus: Annotated[list[GPU], Field(max_length=64)]


class Register(Strict):
    protocol_version: Literal[1]
    instance_id: UUID
    gpu_type: Annotated[str, Field(min_length=1, max_length=128)]
    inventory: Inventory


class Claim(Strict):
    instance_id: UUID
    request_id: UUID


class Fence(Strict):
    instance_id: UUID
    assignment_id: UUID
    attempt_token: UUID
    generation: int | None = None


class Health(Strict):
    workload: bool = False
    broker: bool = False
    access: bool = False
    endpoint: bool = False


class Active(Fence):
    health: Health = Field(default_factory=Health)


class Heartbeat(Strict):
    instance_id: UUID
    sequence: Annotated[int, Field(ge=1, le=2**53)]
    paused: bool
    draining: bool
    inventory: Inventory
    assignments: Annotated[list[Active], Field(max_length=64)]


class Event(Fence):
    sequence: Annotated[int, Field(ge=1, le=2**53)]
    phase: Literal["PULLING", "STARTING", "CONNECTING", "STOPPING"]
    health: Health = Field(default_factory=Health)
    failure_code: (
        Literal[
            "PULL_FAILED",
            "UNSUPPORTED_IMAGE",
            "GPU_BUSY",
            "DISK_FULL",
            "START_FAILED",
            "HEALTH_FAILED",
            "LEASE_LOST",
            "INTERRUPTED",
            "LOCAL_CONFLICT",
        ]
        | None
    ) = None


class Result(Fence):
    outcome: Literal["completed", "estimation", "user_failure", "system_failure"]
    vram_required: (
        Annotated[float, Field(ge=0, le=10000, allow_inf_nan=False)] | None
    ) = None
    ram_required: (
        Annotated[float, Field(ge=0, le=100000, allow_inf_nan=False)] | None
    ) = None
    step_time: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None


class Start(Strict):
    revision_id: UUID | None = None


class Logs(Fence):
    lines: Annotated[
        list[Annotated[str, Field(max_length=4096)]], Field(max_length=100)
    ]
