from sqlalchemy import Column, String, Float, DateTime, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.database import Base
from app.models.user_model import User
import uuid


class ResourceRequest(Base):
    __tablename__ = "resource_requests"

    request_id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.user_id"), nullable=False)
    cpu_ram = Column(Float, nullable=True)
    gpu_vram = Column(Float, nullable=True)
    cpu_cores = Column(Float, nullable=True)
    gpu_type = Column(String, nullable=True)
    disk = Column(Float, nullable=True)
    status = Column(String, nullable=False)
    requested_at = Column(DateTime(timezone=True), server_default=func.now())
    closed_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(String, nullable=True)
    user = relationship("User", backref="resource_requests")