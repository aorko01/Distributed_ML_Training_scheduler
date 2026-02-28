from sqlalchemy import Column, String, Integer, Float, DateTime
from sqlalchemy.sql import func
from app.db.database import Base
import uuid

class Worker(Base):
    __tablename__ = "workers"

    worker_id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    mac_address = Column(String, unique=True, nullable=False)
    gpu_type = Column(String, nullable=False)
    num_gpus = Column(Integer, nullable=False)
    total_vram = Column(Float, nullable=False)
    first_seen = Column(DateTime(timezone=True), server_default=func.now())
    last_registered = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())