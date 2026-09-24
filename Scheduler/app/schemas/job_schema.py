from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional, Dict, Literal
from app.models.job_model import JobPriority


class JobCreate(BaseModel):
    user_id: str
    object_key: Optional[str] = None
    source_kind: Optional[str] = None
    command: Optional[str] = None
    resume_command: Optional[str] = None
    docker_base_image: str
    config: Optional[Dict] = None
    priority: JobPriority = JobPriority.NORMAL
    reason_for_priority: Optional[str] = None

class JobResponse(BaseModel):
    id: str
    user_id: str
    status: str
    priority: JobPriority
    reason_for_priority: Optional[str] = None
    resume_command: Optional[str] = None
    device: Optional[str] = None
    image_tag: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class Job_status_to_vram_estimation_pending(BaseModel):
    job_id: str


class TrainingSubmissionRequest(BaseModel):
    """Entry/resume command submitted for an already built workspace image.

    Image building and training are decoupled, so the command is supplied after
    the build finishes (see the Training page).
    """

    command: str = Field(min_length=1, max_length=4096)
    resume_command: Optional[str] = Field(default=None, max_length=4096)
    priority: JobPriority = JobPriority.NORMAL
    reason_for_priority: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("command")
    @classmethod
    def single_line_command(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Entry command is required")
        if "\n" in value or "\r" in value:
            raise ValueError("Entry command must be a single line")
        return value

    @field_validator("resume_command")
    @classmethod
    def single_line_resume_command(cls, value):
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if "\n" in value or "\r" in value:
            raise ValueError("Resume command must be a single line")
        return value



class ImageBuilderClaimRequest(BaseModel):
    builder_id: str


class ImageBuildAttemptRequest(BaseModel):
    job_id: str
    builder_id: str
    attempt_id: str


class ImageBuildReadyRequest(ImageBuildAttemptRequest):
    image_tag: str


class ActiveImageBuild(BaseModel):
    job_id: str
    attempt_id: str


class ImageBuilderHeartbeat(BaseModel):
    builder_id: str
    active_builds: list[ActiveImageBuild] = Field(default_factory=list)

class JobIDRequest(BaseModel):
    job_id: str

class JobResumeRequest(BaseModel):
    job_id: str
    worker_id: str
    # GPU type of the requesting worker, checked against job.device so a job
    # being run on a different device is never resumed by this worker.
    device: Optional[str] = None

class VramEstimationReport(BaseModel):
    job_id: str
    vram_required: float
    ram_required: float
    step_time: float

class JobFailureReport(BaseModel):
    job_id: str
    # "user" -> job FAILED (build/training code error), "system" -> job RETRY_NEEDED (infra issue)
    failure_type: Literal["user", "system"]
    failure_reason: Optional[str] = None
    # Required when IMAGE_BUILDING; omitted for failures reported by training
    # workers, which use the same endpoint after the image has been built.
    builder_id: Optional[str] = None
    attempt_id: Optional[str] = None
