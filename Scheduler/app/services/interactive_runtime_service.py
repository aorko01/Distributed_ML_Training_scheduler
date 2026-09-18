from datetime import timedelta
import os
from urllib.parse import urlparse, quote
from fastapi import HTTPException
from sqlalchemy import func
from app.models.interactive_workspace_model import (
    InteractiveWorkspace as Workspace,
    InteractiveImageRevision as Revision,
    new_id,
)
from app.models.interactive_runtime_model import (
    InteractiveRuntime as Runtime,
    WorkerAssignment as Assignment,
)
from .scheduling.config import Settings, resource_profile
from .scheduling.types import now, utc
from .scheduling.claims import digest, stop_runtime, lock_worker

PUBLIC_FIELDS = (
    "id",
    "workspace_id",
    "revision_id",
    "generation",
    "profile_version",
    "desired_state",
    "state",
    "created_at",
    "assigned_at",
    "ready_at",
    "stopped_at",
    "startup_deadline",
    "lifetime_deadline",
    "failure_code",
    "failure_detail",
    "access_service",
    "application_protocol",
    "editor_capable",
    "workspace_root",
)


def public(runtime):
    return (
        {field: getattr(runtime, field) for field in PUBLIC_FIELDS} if runtime else None
    )


def owned_runtime(db, owner, runtime_id):
    runtime = db.query(Runtime).filter_by(id=runtime_id, owner_user_id=owner).first()
    if not runtime:
        raise HTTPException(404, "Runtime not found")
    return runtime


def start(db, owner, workspace_id, key, body):
    workspace = (
        db.query(Workspace)
        .filter_by(id=workspace_id, owner_user_id=owner)
        .with_for_update()
        .first()
    )
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    hashed = digest(body.model_dump())
    previous = (
        db.query(Runtime).filter_by(workspace_id=workspace_id, request_key=key).first()
    )
    if previous:
        if previous.request_hash != hashed:
            raise HTTPException(409, "Idempotency key reused")
        db.commit()
        return public(previous)
    if not Settings.from_env().interactive:
        raise HTTPException(503, "Runtime admission disabled")
    if (
        db.query(Runtime)
        .filter(
            Runtime.workspace_id == workspace_id,
            ~Runtime.state.in_(["STOPPED", "FAILED"]),
        )
        .first()
    ):
        raise HTTPException(409, "Workspace runtime still active or awaiting cleanup")
    revision = (
        db.query(Revision)
        .filter_by(
            id=body.revision_id or workspace.saved_revision_id or workspace.current_revision_id,
            workspace_id=workspace_id,
            state="IMAGE_READY",
        )
        .first()
    )
    if not revision:
        raise HTTPException(409, "Start requires a ready revision")
    try:
        spec = resource_profile()
    except (ValueError, OverflowError):
        raise HTTPException(503, "Invalid runtime profile") from None
    if (
        spec["cpu"] <= 0
        or spec["memory_gb"] <= 0
        or spec["disk_gb"] <= 0
        or not 16 <= spec["pids"] <= 4096
    ):
        raise HTTPException(503, "Invalid runtime profile")
    generation = (
        db.query(func.max(Runtime.generation))
        .filter_by(workspace_id=workspace_id)
        .scalar()
        or 0
    ) + 1
    editor = Settings.from_env().workspace_editor
    runtime = Runtime(
        id=new_id(),
        workspace_id=workspace_id,
        owner_user_id=owner,
        revision_id=revision.id,
        image_digest_ref=revision.image_digest_ref,
        generation=generation,
        profile_version=spec["version"],
        launch_spec=spec,
        request_key=key,
        request_hash=hashed,
        created_at=now(),
        desired_state="RUNNING",
        state="QUEUED",
        access_service="workspace" if editor else "terminal",
        application_protocol="workspace-stream-v1" if editor else "terminal-stream-v1",
        editor_capable=editor,
    )
    db.add(runtime)
    db.commit()
    return public(runtime)


def latest(db, owner, workspace_id):
    from .interactive_workspace_service import owned

    owned(db, owner, workspace_id)
    return public(
        db.query(Runtime)
        .filter_by(workspace_id=workspace_id)
        .order_by(Runtime.generation.desc())
        .first()
    )


def stop(db, owner, runtime_id):
    runtime = owned_runtime(db, owner, runtime_id)
    # Read identity first, then acquire Worker -> runtime, matching claim locks.
    assignment_id = runtime.assignment_id
    if assignment_id:
        assignment = db.get(Assignment, assignment_id)
        lock_worker(db, assignment.worker_id)
    runtime = (
        db.query(Runtime)
        .filter_by(id=runtime_id)
        .populate_existing()
        .with_for_update()
        .one()
    )
    stop_runtime(runtime)
    if not runtime.assignment_id:
        runtime.state, runtime.stopped_at, runtime.management_revoked = (
            "STOPPED",
            now(),
            True,
        )
    db.commit()
    return public(runtime)


def ready(db, runtime):
    assignment = (
        db.get(Assignment, runtime.assignment_id) if runtime.assignment_id else None
    )
    return (
        runtime.desired_state == "RUNNING"
        and runtime.state == "READY"
        and runtime.health_at
        and utc(runtime.health_at) > now() - timedelta(seconds=15)
        and all(
            runtime.health.get(x) is True
            for x in ("workload", "broker", "access", "endpoint")
        )
        and assignment
        and not assignment.released_at
        and assignment.state not in ("LOST", "CLEANING")
        and utc(assignment.lease_until) > now()
        and assignment.generation == runtime.generation
    )


def connection(db, owner, runtime_id, management, workspace=False):
    runtime = owned_runtime(db, owner, runtime_id)
    if runtime.assignment_id:
        lock_worker(db, db.get(Assignment, runtime.assignment_id).worker_id)
    runtime = (
        db.query(Runtime)
        .filter_by(id=runtime_id)
        .populate_existing()
        .with_for_update()
        .one()
    )
    if not ready(db, runtime) or (workspace and (not runtime.editor_capable or runtime.access_service != "workspace")):
        raise HTTPException(409, "Runtime is unavailable")
    requested_field = "workspace_connection_requested_at" if workspace else "connection_requested_at"
    requested_at = getattr(runtime, requested_field)
    if requested_at and utc(
        requested_at
    ) > now() - timedelta(seconds=5):
        raise HTTPException(429, "Wait before requesting another connection")
    origin = os.getenv("INTERACTIVE_GATEWAY_WSS_ORIGIN", "")
    parsed = urlparse(origin)
    if (
        parsed.scheme != "wss"
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.path not in ("", "/")
    ):
        raise HTTPException(503, "Public Gateway unavailable")
    setattr(runtime, requested_field, now())
    resource_id, generation = runtime.resource_id, runtime.generation
    db.commit()
    grant = management.call(
        "POST",
        "access-grants",
        {
            "user": owner,
            "resource_id": resource_id,
            "generation": str(generation),
            # A workspace endpoint still accepts the legacy OPEN verification
            # path, but it is registered under its immutable workspace service.
            "service": runtime.access_service,
            "gateway_id": os.environ["INTERACTIVE_GATEWAY_ID"],
            "authorized": True,
        },
    )
    db.expire_all()
    runtime = owned_runtime(db, owner, runtime_id)
    lock_worker(db, db.get(Assignment, runtime.assignment_id).worker_id)
    db.refresh(runtime)
    if (
        not ready(db, runtime)
        or runtime.resource_id != resource_id
        or runtime.generation != generation
    ):
        db.rollback()
        management.call("DELETE", "access-grants/" + grant["grant_id"])
        raise HTTPException(409, "Runtime changed during connection request")
    db.commit()
    return {
        "wss_url": origin.rstrip("/")
        + "/v1/connect/"
        + quote(resource_id, safe="")
        + "/" + runtime.access_service,
        "ticket": grant["ticket"],
        "expires_at": grant["expires_at"],
        "runtime_id": runtime.id,
        "generation": generation,
        "protocol": "tcp-stream-v1",
        "terminal_protocol": "terminal-stream-v1" if not workspace else None,
        "workspace_protocol": "workspace-stream-v1" if workspace else None,
        "service": runtime.access_service,
    }
