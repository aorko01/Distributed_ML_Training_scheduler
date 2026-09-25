"""Scoped CLI refresh tokens for dml-ssh (plan.md §5)."""
from sqlalchemy import Column, String, DateTime
from app.db.database import Base


class CliRefreshToken(Base):
    __tablename__ = "cli_refresh_tokens"
    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    scope = Column(String, nullable=False, default="interactive:ssh")
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True))
    replaced_by = Column(String)
