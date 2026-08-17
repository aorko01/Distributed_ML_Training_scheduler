from sqlalchemy import Column, String, Integer,Float, DateTime
from sqlalchemy.sql import func
from app.db.database import Base
import uuid

class Worker(Base):
    __tablename__ = "workers"

    worker_id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    gpu_type = Column(String, nullable=False)
    num_gpus = Column(Integer, nullable=False)
    total_vram = Column(Float, nullable=False)
    gpus_in_use = Column(Integer, nullable=True)
    available_vram = Column(Float, nullable=True)
    hostname = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)
    gpu_load = Column(Float, nullable=True)
    cpu_load = Column(Float, nullable=True)
    mem_usage = Column(Float, nullable=True)
    total_disk = Column(Float, nullable=True)
    available_disk = Column(Float, nullable=True)
    first_seen = Column(DateTime(timezone=True), server_default=func.now())
    last_registered = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())