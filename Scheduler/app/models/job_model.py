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


class JobPriority(enum.Enum):
    NORMAL = "NORMAL"
    REQUESTED = "REQUESTED"
    HIGH = "HIGH"


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.user_id"), nullable=False, index=True)
    object_key = Column(String, nullable=False, unique=True)
    command = Column(String, nullable=False)
    docker_base_image = Column(String, nullable=False)
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
    step_time = Column(Float, nullable=True)  # in seconds per step

    # Runtime accounting
    gpu_hour = Column(Float, nullable=True)  # GPU hours used, set when the job completes
    started_at = Column(DateTime(timezone=True), nullable=True)  # when the job started running (IN_PROGRESS)
    device = Column(String, nullable=True)  # device the job is running on (saved when worker pulls for running)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())