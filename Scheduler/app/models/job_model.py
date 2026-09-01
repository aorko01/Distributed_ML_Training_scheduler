from sqlalchemy import Column, String, DateTime, Float, Enum, JSON, ForeignKey
from sqlalchemy.sql import func
import uuid
from app.db.database import Base
import enum


class JobStatus(enum.Enum):
    NOT_RUNNABLE = "NOT_RUNNABLE"
    VRAM_ESTIMATION_PENDING = "VRAM_ESTIMATION_PENDING"
    RUNNABLE = "RUNNABLE"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRY_NEEDED = "RETRY_NEEDED"
    INTERACTIVE_READY = "INTERACTIVE_READY"
    # Interactive session lifecycle (push-based dispatch; not scheduled via pull)
    INTERACTIVE_DEPLOYING = "INTERACTIVE_DEPLOYING"
    INTERACTIVE_RUNNING = "INTERACTIVE_RUNNING"
    INTERACTIVE_STOPPED = "INTERACTIVE_STOPPED"


class JobPriority(enum.Enum):
    NORMAL = "NORMAL"
    REQUESTED = "REQUESTED"
    HIGH = "HIGH"


# Statuses belonging to the batch pipeline. Once a job enters these, interactive
# session lifecycle reports (update_session_ip, watchdog) must NOT mirror
# session state onto the job anymore.
BATCH_JOB_STATUSES = {
    JobStatus.VRAM_ESTIMATION_PENDING,
    JobStatus.RUNNABLE,
    JobStatus.IN_PROGRESS,
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.RETRY_NEEDED,
}


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.user_id"), nullable=False, index=True)
    # Nullable because interactive jobs have no uploaded archive
    object_key = Column(String, nullable=True, unique=True)
    name = Column(String, nullable=True)
    command = Column(String, nullable=False)
    resume_command = Column(String, nullable=True)
    # Nullable because interactive jobs derive their image from base_job_id
    docker_base_image = Column(String, nullable=True)
    config = Column(JSON, nullable=True)

    priority = Column(
        Enum(JobPriority),
        default=JobPriority.NORMAL,
        nullable=False,
    )
    reason_for_priority = Column(String, nullable=True)

    status = Column(
        Enum(JobStatus),
        default=JobStatus.NOT_RUNNABLE
    )

    # VRAM estimation
    vram_required = Column(Float, nullable=True)  # in GB
    ram_required = Column(Float, nullable=True)  # in GB
    step_time = Column(Float, nullable=True)  # in seconds per step

    # Runtime accounting
    gpu_hour = Column(Float, nullable=True)  # GPU hours used, set when the job completes
    started_at = Column(DateTime(timezone=True), nullable=True)  # when the job started running (IN_PROGRESS)
    device = Column(String, nullable=True)  # device the job is running on (saved when worker pulls for running)

    # Failure reporting (set by builder/worker when a job fails)
    failure_reason = Column(String, nullable=True)  # why the job failed / needs a retry

    # Build type: "training" (default) or "interactive" (derived from a base job)
    build_type = Column(String, nullable=False, default="training")
    # For interactive jobs, the training job id this interactive job is derived from
    base_job_id = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())