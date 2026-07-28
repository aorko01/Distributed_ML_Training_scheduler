from sqlalchemy import Column, String, DateTime, Float, Integer, Enum, JSON
from sqlalchemy.sql import func
import uuid
from app.db.database import Base
import enum


class JobStatus(enum.Enum):
    NOT_RUNNABLE = "NOT_RUNNABLE"
    VRAM_ESTIMATION_PENDING = "VRAM_ESTIMATION_PENDING"
    RUNNABLE = "RUNNABLE"
    IN_PROGRESS = "IN_PROGRESS"
    RETRY_PENDING = "RETRY_PENDING"  # worker died mid-training; needs reassignment
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    object_key = Column(String, nullable=False, unique=True)
    command = Column(String, nullable=False)
    docker_base_image = Column(String, nullable=False)
    config = Column(JSON, nullable=True)

    status = Column(
        Enum(JobStatus),
        default=JobStatus.NOT_RUNNABLE
    )

    # VRAM estimation
    vram_required = Column(Float, nullable=True)  # in GB
    step_time = Column(Float, nullable=True)  # in seconds per step

    # Fault recovery
    assigned_worker_id = Column(String, nullable=True)  # worker currently (or last) running this job
    retry_count = Column(Integer, nullable=False, default=0)  # number of times reassigned after worker death

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())