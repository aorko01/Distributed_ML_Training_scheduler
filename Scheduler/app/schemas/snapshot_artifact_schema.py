"""Worker/Builder schemas for the private snapshot artifact handoff."""
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SnapshotFence(Strict):
    instance_id: str = Field(min_length=1, max_length=128)
    assignment_id: str = Field(min_length=1, max_length=128)
    attempt_token: str = Field(min_length=1, max_length=128)
    generation: int = Field(gt=0)


class SnapshotComplete(SnapshotFence):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0, le=8 * 1024 * 1024 * 1024)
