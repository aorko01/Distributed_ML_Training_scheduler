"""Owner and Worker schemas for durable workspace workflows."""
import shlex
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SaveRequest(Strict):
    generation: int = Field(gt=0)
    parent_revision_id: str = Field(min_length=1, max_length=128)


class TrainingSettings(Strict):
    name: str = Field(min_length=1, max_length=120)
    command: str = Field(min_length=1, max_length=1024)
    resume_command: str | None = Field(default=None, max_length=1024)
    priority: Literal["NORMAL", "REQUESTED", "HIGH"] = "NORMAL"
    reason_for_priority: str | None = Field(default=None, max_length=1000)

    @field_validator("command", "resume_command")
    @classmethod
    def python_invocation(cls, value):
        if value is None:
            return value
        try:
            parts = shlex.split(value, posix=True)
        except ValueError:
            raise ValueError("Use a simple Python command") from None
        if not parts or parts[0] not in ("python", "python3") or any(token in value for token in (";", "|", "&", "`", "$(", "\n", "\r")):
            raise ValueError("Use a simple Python command such as python train.py --epochs 10")
        if len(parts) > 64 or not any(part.endswith(".py") for part in parts[1:]):
            raise ValueError("Training command must invoke a Python script")
        return value


class TrainingRequest(SaveRequest):
    settings: TrainingSettings
