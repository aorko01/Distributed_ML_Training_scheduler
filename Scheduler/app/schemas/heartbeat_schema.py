from pydantic import BaseModel


class HeartbeatSchema(BaseModel):
    worker_id: str
    gpu_type: str
    available_vram: float
    gpus_in_use: int | None = None
    gpu_load: float | None = None
    cpu_load: float | None = None
    mem_usage: float | None = None
    cpu_cores: int | None = None
    total_ram: float | None = None
    total_disk: float | None = None
    available_disk: float | None = None
    hostname: str | None = None
    ip_address: str | None = None
    # Session IDs of interactive containers this worker currently has running
    # (piggybacked on the heartbeat so the scheduler can track container liveness).
    interactive_ssessions: list[str] = []


class HeartbeatResponse(BaseModel):
    status: str
    worker_id: str
    # Interactive sessions this worker must stop (delivered out-of-band because
    # workers are not inbound-reachable). Redelivered until the worker reports
    # the container stopped.
    stop_sessions: list[str] = []
    # Commit commands for interactive sessions this worker hosts (delivered
    # out-of-band like stop_sessions; redelivered until commit_complete/failed).
    commit_sessions: list[dict] = []
