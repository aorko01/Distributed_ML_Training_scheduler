"""Durable reservations; Redis is never an execution ownership authority."""

from sqlalchemy import (
    Column,
    String,
    Integer,
    Boolean,
    DateTime,
    JSON,
    ForeignKey,
    ForeignKeyConstraint,
    UniqueConstraint,
    CheckConstraint,
    Index,
    text,
)
from app.db.database import Base
from app.models.interactive_workspace_model import new_id

LIVE = "state NOT IN ('STOPPED','FAILED')"


class InteractiveRuntime(Base):
    __tablename__ = "interactive_runtimes"
    id = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, nullable=False)
    owner_user_id = Column(String, nullable=False)
    revision_id = Column(String, nullable=False)
    image_digest_ref = Column(String, nullable=False)
    generation = Column(Integer, nullable=False)
    profile_version = Column(String, nullable=False)
    launch_spec = Column(JSON, nullable=False)
    request_key = Column(String(128), nullable=False)
    request_hash = Column(String(64), nullable=False)
    desired_state = Column(String, nullable=False, default="RUNNING")
    state = Column(String, nullable=False, default="QUEUED")
    assignment_id = Column(
        String,
        ForeignKey(
            "worker_assignments.id", use_alter=True, name="fk_runtime_assignment"
        ),
    )
    created_at = Column(DateTime(timezone=True), nullable=False)
    assigned_at = Column(DateTime(timezone=True))
    ready_at = Column(DateTime(timezone=True))
    stopped_at = Column(DateTime(timezone=True))
    startup_deadline = Column(DateTime(timezone=True))
    lifetime_deadline = Column(DateTime(timezone=True))
    health_at = Column(DateTime(timezone=True))
    health = Column(JSON, nullable=False, default=dict)
    failure_code = Column(String)
    failure_detail = Column(String(256))
    resource_id = Column(String, unique=True)
    enrollment_started = Column(Boolean, nullable=False, default=False)
    enrollment_id = Column(String)
    endpoint_version = Column(String)
    management_revoked = Column(Boolean, nullable=False, default=False)
    controller_token = Column(String)
    controller_until = Column(DateTime(timezone=True))
    connection_requested_at = Column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "owner_user_id"],
            ["interactive_workspaces.id", "interactive_workspaces.owner_user_id"],
        ),
        ForeignKeyConstraint(
            ["revision_id", "workspace_id"],
            [
                "interactive_image_revisions.id",
                "interactive_image_revisions.workspace_id",
            ],
        ),
        UniqueConstraint("workspace_id", "generation"),
        UniqueConstraint("workspace_id", "request_key"),
        UniqueConstraint("id", "generation"),
        CheckConstraint("generation > 0"),
        CheckConstraint("desired_state IN ('RUNNING','STOPPED')"),
        CheckConstraint(
            "state IN ('QUEUED','ASSIGNED','PULLING','STARTING','CONNECTING','READY','STOPPING','LOST','STOPPED','FAILED')"
        ),
        Index(
            "uq_runtime_live_workspace",
            "workspace_id",
            unique=True,
            postgresql_where=text(LIVE),
            sqlite_where=text(LIVE),
        ),
    )


class WorkerAssignment(Base):
    __tablename__ = "worker_assignments"
    id = Column(String, primary_key=True, default=new_id)
    worker_id = Column(
        String, ForeignKey("workers.worker_id"), nullable=False, index=True
    )
    instance_id = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    job_id = Column(String, ForeignKey("jobs.id"))
    runtime_id = Column(String, ForeignKey("interactive_runtimes.id"))
    generation = Column(Integer)
    attempt_token = Column(String, nullable=False, unique=True)
    request_id = Column(String, nullable=False)
    request_hash = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    exclusive = Column(Boolean, nullable=False, default=False)
    gpu_uuid = Column(String)
    state = Column(String, nullable=False, default="CLAIMED")
    event_sequence = Column(Integer, nullable=False, default=0)
    event_hash = Column(String)
    created_at = Column(DateTime(timezone=True), nullable=False)
    lease_until = Column(DateTime(timezone=True), nullable=False)
    released_at = Column(DateTime(timezone=True))
    cleanup_ack = Column(Boolean, nullable=False, default=False)
    result = Column(JSON)
    result_hash = Column(String)
    __table_args__ = (
        UniqueConstraint("worker_id", "instance_id", "request_id"),
        ForeignKeyConstraint(
            ["runtime_id", "generation"],
            ["interactive_runtimes.id", "interactive_runtimes.generation"],
        ),
        CheckConstraint(
            "(kind IN ('vram_estimation','batch_training') AND job_id IS NOT NULL AND runtime_id IS NULL AND generation IS NULL AND NOT exclusive) OR (kind='interactive_access' AND job_id IS NULL AND runtime_id IS NOT NULL AND generation IS NOT NULL AND exclusive)"
        ),
        CheckConstraint("state IN ('CLAIMED','ACTIVE','CLEANING','LOST','RELEASED')"),
        CheckConstraint(
            "(released_at IS NULL AND state != 'RELEASED') OR (released_at IS NOT NULL AND state = 'RELEASED' AND cleanup_ack)"
        ),
        Index(
            "uq_assignment_live_job",
            "job_id",
            unique=True,
            postgresql_where=text("released_at IS NULL AND job_id IS NOT NULL"),
            sqlite_where=text("released_at IS NULL AND job_id IS NOT NULL"),
        ),
        Index(
            "uq_assignment_live_runtime",
            "runtime_id",
            unique=True,
            postgresql_where=text("released_at IS NULL AND runtime_id IS NOT NULL"),
            sqlite_where=text("released_at IS NULL AND runtime_id IS NOT NULL"),
        ),
        Index(
            "uq_assignment_live_interactive_worker",
            "worker_id",
            unique=True,
            postgresql_where=text("released_at IS NULL AND kind='interactive_access'"),
            sqlite_where=text("released_at IS NULL AND kind='interactive_access'"),
        ),
    )
