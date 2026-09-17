import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Operator-owned identifiers, not arbitrary FROM strings. Keep UI and builder aligned
# by returning the resolved mapping in authenticated internal claims.
BASE_IMAGES = {
    'pytorch-2.5.1-cuda12.4': 'pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime',
    'pytorch-2.5.1-cuda11.8': 'pytorch/pytorch:2.5.1-cuda11.8-cudnn9-runtime',
}
DIGEST_RE = re.compile(r'^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+@sha256:[0-9a-f]{64}$')
TAG_RE = re.compile(r'^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$')


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class FromJob(Strict):
    name: str = Field(min_length=1, max_length=120)
    source_job_id: str = Field(min_length=1, max_length=128)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError('name is required')
        return value.strip()


class Claim(Strict):
    builder_id: str = Field(min_length=1, max_length=128)


class Attempt(Claim):
    revision_id: str = Field(min_length=1, max_length=128)
    attempt_id: str = Field(min_length=1, max_length=128)


class Ready(Attempt):
    image_tag: str
    image_digest_ref: str
    resolved_base_digest: str

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
