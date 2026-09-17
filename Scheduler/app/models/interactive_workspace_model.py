"""Interactive image records are deliberately independent of batch Job states."""
import uuid
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, ForeignKeyConstraint, UniqueConstraint, CheckConstraint, JSON
from sqlalchemy.sql import func
from app.db.database import Base


def new_id():
    return str(uuid.uuid4())


class InteractiveWorkspace(Base):
    __tablename__ = 'interactive_workspaces'
    id = Column(String, primary_key=True, default=new_id)
    owner_user_id = Column(String, ForeignKey('users.user_id'), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    source_type = Column(String, nullable=False)
    source_job_id = Column(String, nullable=True)
    current_revision_id = Column(String, nullable=True)
    request_key = Column(String(128), nullable=False)
    request_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint('owner_user_id', 'request_key'),
        UniqueConstraint('id', 'owner_user_id'),
        ForeignKeyConstraint(['source_job_id', 'owner_user_id'], ['jobs.id', 'jobs.user_id']),
        ForeignKeyConstraint(['current_revision_id', 'id'], ['interactive_image_revisions.id', 'interactive_image_revisions.workspace_id'], use_alter=True, name='fk_workspace_current_revision'),
        CheckConstraint("(source_type = 'UPLOAD' AND source_job_id IS NULL) OR (source_type = 'EXISTING_JOB' AND source_job_id IS NOT NULL)"),
    )


class InteractiveImageRevision(Base):
    __tablename__ = 'interactive_image_revisions'
    id = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, ForeignKey('interactive_workspaces.id'), nullable=False, index=True)
    revision_number = Column(Integer, nullable=False, default=1)
    origin = Column(String, nullable=False)
    source_object_key = Column(String)
    source_image_tag = Column(String)
    requested_base_image = Column(String)
    resolved_base_digest = Column(String)
    state = Column(String, nullable=False, default='QUEUED', index=True)
    image_tag = Column(String)
    image_digest_ref = Column(String)
    failure_type = Column(String)
    failure_reason = Column(String)
    builder_id = Column(String, index=True)
    attempt_id = Column(String, unique=True, index=True)
    started_at = Column(DateTime(timezone=True))
    lease_until = Column(DateTime(timezone=True), index=True)
    excluded_builder_id = Column(String)
    excluded_until = Column(DateTime(timezone=True))
    attempt_count = Column(Integer, nullable=False, default=0)
    build_logs = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint('workspace_id', 'revision_number'),
        UniqueConstraint('id', 'workspace_id'),
        CheckConstraint('revision_number > 0 AND attempt_count >= 0'),
        CheckConstraint("origin IN ('UPLOAD','EXISTING_JOB','SNAPSHOT')"),
        CheckConstraint("state IN ('QUEUED','BUILDING','IMAGE_READY','FAILED','CANCELLED')"),
        CheckConstraint("state != 'IMAGE_READY' OR (image_tag IS NOT NULL AND image_digest_ref IS NOT NULL AND resolved_base_digest IS NOT NULL)"),
        CheckConstraint("(origin = 'UPLOAD' AND source_object_key IS NOT NULL AND requested_base_image IS NOT NULL AND source_image_tag IS NULL) OR (origin = 'EXISTING_JOB' AND source_image_tag IS NOT NULL AND source_object_key IS NULL AND requested_base_image IS NULL) OR origin = 'SNAPSHOT'"),
        CheckConstraint("state != 'BUILDING' OR (builder_id IS NOT NULL AND attempt_id IS NOT NULL AND started_at IS NOT NULL AND lease_until IS NOT NULL)"),
    )
