"""Strict interactive capacity contracts.

One canonical requirements object is used for workspace defaults, capacity
preview, and runtime start. Unknown fields (including any placement identity
such as worker_id, hostname, or GPU UUID) are rejected with 422.
"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ResourceRequirements(Strict):
    gpu_model: str | None = Field(default=None, max_length=128)
    minimum_vram_gb: float = Field(ge=0.5, le=512, allow_inf_nan=False)
    cpu_cores: float = Field(ge=0.5, le=1024, allow_inf_nan=False)
    memory_gb: float = Field(ge=0.5, le=4096, allow_inf_nan=False)
    disk_gb: int = Field(ge=1, le=1000000)

    def canonical(self) -> dict:
        data = self.model_dump()
        # Canonicalize numbers so 4 and 4.0 hash identically.
        for key in ("minimum_vram_gb", "cpu_cores", "memory_gb"):
            data[key] = float(data[key])
        data["disk_gb"] = int(data["disk_gb"])
        if data.get("gpu_model") is not None:
            data["gpu_model"] = data["gpu_model"].strip() or None
            if data["gpu_model"] == "":
                data["gpu_model"] = None
        return data


class CapacityOptions(Strict):
    defaults: ResourceRequirements
    bounds: dict
    gpu_models: list[str]
    cpu_choices: list[float]
    ram_choices: list[float]
    vram_choices: list[float]
    disk_choices: list[int]


class WorkloadSummary(Strict):
    kind: Literal["batch_training", "vram_estimation", "interactive_access"]
    state: str
    mine: bool


class CapacityMachine(Strict):
    machine_key: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)
    gpu_model: str | None = None
    gpu_count: int = Field(ge=0, le=64)
    total_vram_gb: float = Field(ge=0, allow_inf_nan=False)
    free_vram_gb: float = Field(ge=0, allow_inf_nan=False)
    cpu_cores: int = Field(ge=0, le=4096)
    cpu_load_percent: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    total_ram_gb: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    free_ram_gb: float = Field(ge=0, allow_inf_nan=False)
    total_disk_gb: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    free_disk_gb: float = Field(ge=0, allow_inf_nan=False)
    gpu_load_percent: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    available_now: bool
    availability_reason: str | None = None
    workloads: list[WorkloadSummary] = Field(default_factory=list, max_length=64)


class CapacityPreview(Strict):
    generated_at: str
    requirements: ResourceRequirements
    matching_online: int = Field(ge=0)
    available_now: int = Field(ge=0)
    busy: int = Field(ge=0)
    queued_interactive_requests: int = Field(ge=0)
    machines: list[CapacityMachine]
