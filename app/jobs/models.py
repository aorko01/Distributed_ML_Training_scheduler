from sqlalchemy import Column, String, DateTime, Enum, JSON
from sqlalchemy.sql import func
import uuid
from app.database import Base
import enum


class JobStatus(enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    script_path = Column(String, nullable=False)
    dataset_path = Column(String, nullable=False)
    config = Column(JSON, nullable=True)
    status = Column(Enum(JobStatus), default=JobStatus.PENDING)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())