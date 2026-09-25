from sqlalchemy import Column, String, DateTime, JSON
from sqlalchemy.sql import func
from app.db.database import Base


class WorkerCredential(Base):
    """DB-backed worker credentials.

    Primary store for worker (identity -> secrets) managed via the Admin UI.
    The JSON file at WORKER_CREDENTIALS_FILE remains as a fallback so existing
    deployments keep working; worker_auth checks DB first, then the file.
    Secrets are stored as-is (same as the file format) because workers
    authenticate with ``Authorization: Bearer <secret>`` via compare_digest.
    """

    __tablename__ = "worker_credentials"

    worker_id = Column(String, primary_key=True)
    secrets = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
