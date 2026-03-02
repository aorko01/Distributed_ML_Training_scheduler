from sqlalchemy import Column, String, DateTime,Integer, Enum, JSON
from sqlalchemy.sql import func
import uuid
from app.db.database import Base
import enum


class JobStatus(enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    
class VramStatus(enum.Enum):
    NOT_CALCULATED = "NOT_CALCULATED"
    CALCULATED = "CALCULATED"

class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    script_path = Column(String, nullable=False)
    dataset_path = Column(String, nullable=False)
    config = Column(JSON, nullable=True)
    status = Column(Enum(JobStatus), default=JobStatus.PENDING)
    
    #for Vram estimation
    vram_required = Column(Integer, nullable=True)  # in GB, can be empty initially
    vram_status = Column(Enum(VramStatus), default=VramStatus.NOT_CALCULATED)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())