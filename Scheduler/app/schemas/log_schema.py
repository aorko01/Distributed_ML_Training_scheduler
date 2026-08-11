from pydantic import BaseModel, Field


class LogLinesRequest(BaseModel):
    lines: list[str] = Field(default_factory=list)
