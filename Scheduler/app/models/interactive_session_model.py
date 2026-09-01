from sqlalchemy import Boolean, Column, String, DateTime, Enum, ForeignKey
from sqlalchemy.sql import func
import enum
import uuid

from app.db.database import Base


class InteractiveSessionStatus(enum.Enum):
    PENDING = "PENDING"
    DEPLOYING = "DEPLOYING"
    RUNNING = "RUNNING"
    # User asked to stop; the command is delivered to the assigned worker via
    # its heartbeat response until the worker reports the container stopped.
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class InteractiveSession(Base):
    __tablename__ = "interactive_sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    # The training job this interactive session was created from.
    base_job_id = Column(String, nullable=True)
    # Unique id used as the tailnet hostname for the container.
    session_id = Column(String, unique=True, nullable=False, index=True)
    # Gateway-side keypair/session identifier.
    gateway_session_id = Column(String, nullable=True)
    worker_id = Column(String, nullable=True)
    # 100.x.x.x tailnet IP reported by the worker once the container is up.
    headscale_ip = Column(String, nullable=True)
    ssh_public_key = Column(String, nullable=True)
    # Short-lived Headscale pre-auth key used to join the container (temp).
    headscale_auth_key = Column(String, nullable=True)

    status = Column(
        Enum(InteractiveSessionStatus),
        default=InteractiveSessionStatus.PENDING,
        nullable=False,
    )

    # Pending "commit this session as a training image" request, delivered to
    # the hosting worker via its heartbeat response.
    commit_pending = Column(Boolean, default=False, nullable=False)
    commit_image_tag = Column(String, nullable=True)

    # Last worker that hosted this session (for failover: avoid re-dispatching
    # to the same machine that just lost the container).
    last_worker_id = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    stopped_at = Column(DateTime(timezone=True), nullable=True)
