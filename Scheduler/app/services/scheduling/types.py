from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class Kind(str, Enum):
    ESTIMATION = "vram_estimation"
    BATCH = "batch_training"
    INTERACTIVE = "interactive_access"


@dataclass(frozen=True)
class Snapshot:
    worker_id: str
    gpu_type: str
    free_vram: float
    assignments: int
    inventory: dict


@dataclass(frozen=True)
class Candidate:
    kind: Kind
    target_id: str
    flag: str = ""
    gpu_uuid: str | None = None


def now():
    return datetime.now(timezone.utc)


def utc(value):
    return (
        value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value
    )
