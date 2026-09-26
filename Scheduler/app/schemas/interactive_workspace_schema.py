import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.schemas.job_schema import TrainingSubmissionRequest

# Operator-owned identifiers, not arbitrary FROM strings. Keep UI and builder aligned
# by returning the resolved mapping in authenticated internal claims.
# Legacy short ids are kept for backwards compatibility; new submissions may use
# any official PyTorch runtime tag (same set offered by GET /docker/pytorch-tags).
BASE_IMAGES = {
    'pytorch-2.5.1-cuda12.4': 'pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime',
    'pytorch-2.5.1-cuda11.8': 'pytorch/pytorch:2.5.1-cuda11.8-cudnn9-runtime',
}
RUNTIME_REF_RE = re.compile(r'^pytorch/pytorch:[\d.]+-cuda[\d.]+-cudnn[\d.]+-runtime$')


def resolve_base_image(value: str | None) -> str | None:
    """Resolve a client-supplied base image to its full Docker reference.

    Accepts legacy BASE_IMAGES ids as well as any official PyTorch runtime tag
    so the interactive form can offer the full PyTorch/CUDA matrix just like
    batch job submission. Returns None for anything else.
    """
    if not value or not isinstance(value, str):
        return None
    if value in BASE_IMAGES:
        return BASE_IMAGES[value]
    if RUNTIME_REF_RE.fullmatch(value):
        return value
    return None
DIGEST_RE = re.compile(r'^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+@sha256:[0-9a-f]{64}$')
TAG_RE = re.compile(r'^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$')


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class FromJob(Strict):
    name: str = Field(min_length=1, max_length=120)
    source_job_id: str = Field(min_length=1, max_length=128)
    requirements: dict | None = None

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError('name is required')
        return value.strip()

    @field_validator('requirements')
    @classmethod
    def strict_requirements(cls, value):
        if value is None:
            return None
        from app.schemas.interactive_capacity_schema import ResourceRequirements

        if not isinstance(value, dict):
            raise ValueError('requirements must be an object')
        return ResourceRequirements(**value).canonical()


class RevisionTraining(TrainingSubmissionRequest):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)

    @field_validator('name')
    @classmethod
    def clean_training_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Name is required')
        return value


class Claim(Strict):
    builder_id: str = Field(min_length=1, max_length=128)


class Attempt(Claim):
    revision_id: str = Field(min_length=1, max_length=128)
    attempt_id: str = Field(min_length=1, max_length=128)


class Ready(Attempt):
    image_tag: str
    image_digest_ref: str
    resolved_base_digest: str
    developer_profile: str | None = None
    ssh_profile: str | None = None

    @field_validator('developer_profile')
    @classmethod
    def profile(cls, value):
        if value is None:
            return None
        if value != 'v1':
            raise ValueError('unsupported developer profile')
        return value

    @field_validator('ssh_profile')
    @classmethod
    def ssh(cls, value):
        if value is None:
            return None
        if value != 'v1':
            raise ValueError('unsupported SSH profile')
        return value

    @field_validator('image_digest_ref', 'resolved_base_digest')
    @classmethod
    def digest(cls, value):
        if not DIGEST_RE.fullmatch(value):
            raise ValueError('canonical repository digest required')
        return value

    @field_validator('image_tag')
    @classmethod
    def tag(cls, value):
        if not TAG_RE.fullmatch(value):
            raise ValueError('invalid tag')
        return value


class Failure(Attempt):
    failure_type: Literal['user', 'system']
    failure_reason: str = Field(max_length=2000)


class ActiveAttempt(Strict):
    revision_id: str = Field(min_length=1, max_length=128)
    attempt_id: str = Field(min_length=1, max_length=128)


class Heartbeat(Claim):
    active_builds: list[ActiveAttempt] = Field(max_length=64)


class Logs(Attempt):
    lines: list[str] = Field(max_length=100)

    @field_validator('lines')
    @classmethod
    def bounds(cls, value):
        if any(len(line) > 2000 for line in value):
            raise ValueError('line too long')
        return value
