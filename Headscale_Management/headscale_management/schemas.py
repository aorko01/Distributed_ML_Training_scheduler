from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Enroll(Strict):
    role: Literal["endpoint", "gateway"]
    identity: Identifier
    generation: Identifier


class Generation(Strict):
    generation: Identifier


class Register(Generation):
    owner: Identifier
    enrollment_id: Identifier
    service: Identifier
    protocol: Literal["tcp-stream-v1"]
    port: Annotated[int, Field(ge=1, le=65535)]


class Probe(Generation):
    version: Identifier
    probe_id: Identifier
    success: bool


class Issue(Generation):
    user: Identifier
    resource_id: Identifier
    service: Identifier
    gateway_id: Identifier
    authorized: Literal[True]
    purpose: Literal["browser", "ssh"] = "browser"


class Claim(Strict):
    ticket: Annotated[str, Field(min_length=1, max_length=8192)]
    request_id: Annotated[str, Field(min_length=36, max_length=36, pattern=r"^[0-9a-f-]{36}$")]
