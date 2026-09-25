from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("actor", "idempotency_key"), UniqueConstraint("role", "identity", "generation"))
    id: Mapped[str] = mapped_column(String, primary_key=True)
    actor: Mapped[str] = mapped_column(String)
    idempotency_key: Mapped[str] = mapped_column(String)
    request_hash: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)
    identity: Mapped[str] = mapped_column(String)
    generation: Mapped[str] = mapped_column(String)
    tags: Mapped[list] = mapped_column(JSON)
    expires: Mapped[float] = mapped_column(Float)
    key_id: Mapped[str | None] = mapped_column(String)
    ciphertext: Mapped[str | None] = mapped_column(Text)
    node_id: Mapped[str | None] = mapped_column(String)
    ips: Mapped[list] = mapped_column(JSON, default=list)
    observed: Mapped[float | None] = mapped_column(Float)
    online: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    retry_after: Mapped[float] = mapped_column(Float, default=0)
    created: Mapped[float] = mapped_column(Float)
    updated: Mapped[float] = mapped_column(Float)


class Endpoint(Base):
    __tablename__ = "endpoints"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner: Mapped[str] = mapped_column(String)
    generation: Mapped[str] = mapped_column(String)
    enrollment_id: Mapped[str] = mapped_column(ForeignKey("enrollments.id"))
    service: Mapped[str] = mapped_column(String)
    protocol: Mapped[str] = mapped_column(String)
    port: Mapped[int] = mapped_column(Integer)
    version: Mapped[str] = mapped_column(String)
    lease_expires: Mapped[float] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String)
    probe_id: Mapped[str | None] = mapped_column(String)
    probe_expires: Mapped[float | None] = mapped_column(Float)
    probe_gateway: Mapped[str | None] = mapped_column(String)
    probed: Mapped[float | None] = mapped_column(Float)


class Grant(Base):
    __tablename__ = "grants"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user: Mapped[str] = mapped_column(String)
    resource_id: Mapped[str] = mapped_column(ForeignKey("endpoints.id"))
    generation: Mapped[str] = mapped_column(String)
    service: Mapped[str] = mapped_column(String)
    gateway: Mapped[str] = mapped_column(String)
    expires: Mapped[float] = mapped_column(Float)
    deadline: Mapped[float] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String)
    ticket: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(String, default="browser")


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("grant_id"), UniqueConstraint("gateway", "request_id"))
    id: Mapped[str] = mapped_column(String, primary_key=True)
    grant_id: Mapped[str] = mapped_column(ForeignKey("grants.id"))
    gateway: Mapped[str] = mapped_column(String)
    request_id: Mapped[str] = mapped_column(String)
    version: Mapped[str] = mapped_column(String)
    lease_expires: Mapped[float] = mapped_column(Float)
    deadline: Mapped[float] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String)
