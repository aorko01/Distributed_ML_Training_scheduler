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


def requirements_from_spec(spec):
    if not isinstance(spec, dict):
        return None
    try:
        models = spec.get("gpu_models") or []
        return {
            "gpu_model": models[0] if len(models) == 1 else None,
            "minimum_vram_gb": float(spec.get("minimum_vram_gb")),
            "cpu_cores": float(spec.get("cpu")),
            "memory_gb": float(spec.get("memory_gb")),
            "disk_gb": int(spec.get("disk_gb")),
        }
    except (TypeError, ValueError):
        return None


def assigned_machine_summary(db, runtime):
    if not runtime or not getattr(runtime, "assignment_id", None):
        return None
    try:
        assignment = db.get(Assignment, runtime.assignment_id)
        if not assignment:
            return None
        from app.models.worker_model import Worker

        worker = db.query(Worker).filter_by(worker_id=assignment.worker_id).first()
        if not worker:
            return None
        inv = worker.inventory or {}
        gpus = inv.get("gpus") or []
        matched = None
        for gpu in gpus:
            if gpu.get("uuid") == assignment.gpu_uuid:
                matched = gpu
                break
        gpu_model = (matched or {}).get("model") or worker.gpu_type
        total_vram = None
        try:
            total_vram = float((matched or {}).get("memory_gb") or 0) or float(worker.total_vram or 0)
        except (TypeError, ValueError):
            total_vram = float(worker.total_vram or 0)
        display = (inv.get("hostname") or getattr(worker, "hostname", None) or worker.worker_id)
        return {
            "display_name": str(display),
            "gpu_model": gpu_model,
            "total_vram_gb": total_vram,
            "assigned_at": runtime.assigned_at,
        }
    except Exception:
        return None


def _ssh_fields(runtime):
    try:
        return {
            "ssh_capable": bool(getattr(runtime, "ssh_capable", False)),
            "ssh_ready": bool(getattr(runtime, "ssh_ready", False)),
            "ssh_status": getattr(runtime, "ssh_status", None) or "disabled",
            "ssh_generation": getattr(runtime, "ssh_generation", None),
        }
    except Exception:
        return {"ssh_capable": False, "ssh_ready": False, "ssh_status": "disabled", "ssh_generation": None}


def public(runtime, db=None):
    item = (
        {field: getattr(runtime, field) for field in PUBLIC_FIELDS} if runtime else None
    )
    if item is not None:
        item.update(_ssh_fields(runtime))
    if item is not None:
        # Operator egress capability for the web terminal (plan.md Phase 1).
        # Derived from the pinned launch_spec; the browser can never set it.
        try:
            spec = runtime.launch_spec or {}
            item["allow_internet"] = bool(spec.get("allow_internet"))
            item["developer_mode"] = bool(spec.get("developer_mode"))
            # Fully package-capable only when the operator pinned developer
            # mode and the Scheduler internet gate; the Worker re-verifies its
            # own gates before launch. The UI distinguishes this from a proven
            # external connection (no probe is performed here).
            item["package_capable"] = bool(spec.get("developer_mode")) and bool(spec.get("allow_internet"))
        except Exception:
            item["allow_internet"] = False
            item["developer_mode"] = False
            item["package_capable"] = False
        try:
            from app.services.scheduling.config import Settings as _Settings
            item["save_enabled"] = bool(_Settings.from_env().workspace_save)
            item["training_submission_enabled"] = bool(_Settings.from_env().workspace_training_submission)
        except Exception:
            item["save_enabled"] = False
            item["training_submission_enabled"] = False
        item["requirements"] = requirements_from_spec(runtime.launch_spec or {})
        if db is not None:
            try:
                item["assigned_machine"] = assigned_machine_summary(db, runtime)
            except Exception:
                item["assigned_machine"] = None
        else:
            item["assigned_machine"] = None
    return item


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
    raw = body.model_dump()
    # Canonicalize requirements so 4 and 4.0 hash identically. Body validator
    # already returns canonical dicts; re-canonicalize defensively.
    requirements = raw.get("requirements")
    if requirements is not None and not isinstance(requirements, dict):
        raise HTTPException(422, "Invalid requirements")
    if isinstance(requirements, dict):
        try:
            from app.schemas.interactive_capacity_schema import ResourceRequirements

            requirements = ResourceRequirements(**requirements).canonical()
            raw["requirements"] = requirements
        except Exception:
            raise HTTPException(422, "Invalid requirements") from None
    # Fall back to workspace defaults, then operator defaults, for old clients.
    effective = requirements
    if effective is None:
        stored = getattr(workspace, "default_resource_requirements", None)
        if stored:
            try:
                from app.schemas.interactive_capacity_schema import ResourceRequirements

                effective = ResourceRequirements(**stored).canonical()
            except Exception:
                effective = None
    hashed = digest(raw)
    previous = (
        db.query(Runtime).filter_by(workspace_id=workspace_id, request_key=key).first()
    )
    if previous:
        if previous.request_hash != hashed:
            raise HTTPException(409, "Idempotency key reused")
        db.commit()
        return public(previous, db)
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
        spec = resource_profile(effective)
    except ValueError:
        raise HTTPException(422, "Requirements outside operator bounds") from None
    except (OverflowError, Exception):
        raise HTTPException(503, "Invalid runtime profile") from None
    # Developer mode is server-owned and pinned at creation (plan.md §4/§6).
    # Only a revision reported by the trusted Builder as prepared ('v1') may
    # run with developer_mode=true. Older revisions stay on the strict path;
    # the revision public payload carries the actionable rebuild hint. The
    # launch_spec hash covers only the canonical client request, so an
    # idempotent retry returns the originally pinned runtime even if operator
    # policy changed meanwhile (handled by the early return above).
    prepared = getattr(revision, 'developer_profile', None) == 'v1'
    if spec.get("developer_mode") and not prepared:
        spec = {**spec, "developer_mode": False}
    # SSH capability is server-owned and pinned at creation (plan.md §6).
    # Only when INTERACTIVE_SSH_ENABLED and the ready revision carries the
    # new SSH image profile; the browser form cannot set it. Old revisions
    # stay browser-capable with ssh_capable=false.
    from .scheduling.config import ssh_enabled as _ssh_flag
    ssh_ok = bool(_ssh_flag()) and getattr(revision, 'ssh_profile', None) == 'v1'
    spec = {**spec, "ssh_capable": bool(ssh_ok)}
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
    try:
        ssh_cap = bool(spec.get("ssh_capable"))
    except Exception:
        ssh_cap = False
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
        ssh_capable=ssh_cap,
        ssh_ready=False,
        ssh_status="provisioning" if ssh_cap else "disabled",
        ssh_generation=generation if ssh_cap else None,
    )
    db.add(runtime)
    db.commit()
    return public(runtime, db)


def latest(db, owner, workspace_id):
    from .interactive_workspace_service import owned

    owned(db, owner, workspace_id)
    return public(
        db.query(Runtime)
        .filter_by(workspace_id=workspace_id)
        .order_by(Runtime.generation.desc())
        .first(),
        db,
    )


def active_for_owner(db, owner, limit=50):
    """Lightweight owned-runtime listing for in-app notifications."""
    rows = (
        db.query(Runtime, Workspace.name)
        .join(Workspace, Workspace.id == Runtime.workspace_id)
        .filter(Runtime.owner_user_id == owner)
        .order_by(Runtime.created_at.desc())
        .limit(limit)
        .all()
    )
    items = []
    for runtime, name in rows:
        entry = public(runtime, db) or {}
        entry["workspace_name"] = name
        items.append(entry)
    return items


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
    return public(runtime, db)


def ssh_grant_ready(db, runtime):
    """SSH readiness: READY + pinned ssh_capable + reported ssh_ready."""
    if not ready(db, runtime):
        return False
    try:
        return bool(getattr(runtime, "ssh_capable", False)) and bool(getattr(runtime, "ssh_ready", False))
    except Exception:
        return False


def _ssh_blocker(db, runtime):
    """Name the actual failing clause (never the ssh_status label)."""
    try:
        if runtime.desired_state != "RUNNING" or runtime.state != "READY":
            return f"state={runtime.state}/{runtime.desired_state}"
        if not (runtime.health_at and utc(runtime.health_at) > now() - timedelta(seconds=15)):
            return "health-stale"
        if not all((runtime.health or {}).get(x) is True for x in ("workload", "broker", "access", "endpoint")):
            return "health-failed"
        assignment = db.get(Assignment, runtime.assignment_id) if runtime.assignment_id else None
        if not assignment or assignment.released_at or assignment.state in ("LOST", "CLEANING"):
            return "assignment-released"
        if not (utc(assignment.lease_until) > now() and assignment.generation == runtime.generation):
            return "lease-or-generation"
        if not getattr(runtime, "ssh_capable", False):
            return "not-ssh-capable"
        if not getattr(runtime, "ssh_ready", False):
            return f"ssh-{getattr(runtime, 'ssh_status', None) or 'not-ready'}"
        return "not-ready"
    except Exception:
        return "not-ready"


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
            "purpose": "browser",
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


def ssh_info(db, owner, runtime_id):
    """Owner-checked SSH capability/status + pinned host key (no secrets)."""
    runtime = owned_runtime(db, owner, runtime_id)
    info = public(runtime, db) or {}
    if not info.get("ssh_capable"):
        raise HTTPException(404, "SSH unavailable for this runtime (rebuild from an SSH-capable revision)")
    return {
        "runtime_id": runtime.id,
        "generation": runtime.generation,
        # Lifecycle snapshot so the CLI fails fast on dead runtimes instead
        # of writing a host block for a STOPPED/FAILED runtime whose ssh_*
        # flags are frozen at their last live values (all already in public()).
        "state": runtime.state,
        "desired_state": runtime.desired_state,
        "failure_code": getattr(runtime, "failure_code", None),
        "failure_detail": getattr(runtime, "failure_detail", None),
        "ssh_user": "dml",
        "workspace": "/workspace",
        "ssh_capable": bool(getattr(runtime, "ssh_capable", False)),
        "ssh_ready": bool(getattr(runtime, "ssh_ready", False)),
        "ssh_status": getattr(runtime, "ssh_status", None) or "disabled",
        "host_key": getattr(runtime, "ssh_host_key", None),
        "host_key_fingerprint": getattr(runtime, "ssh_key_fingerprint", None),
        "ssh_generation": getattr(runtime, "ssh_generation", None) or runtime.generation,
    }


def ssh_connection(db, owner, runtime_id, management):
    """Owner-checked single-use SSH-purpose grant on the pinned service."""
    from .scheduling.config import ssh_enabled as _ssh_on
    if not _ssh_on():
        raise HTTPException(404, "SSH unavailable (feature disabled)")
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
    if not ssh_grant_ready(db, runtime):
        raise HTTPException(409, f"SSH unavailable ({_ssh_blocker(db, runtime)})")
    # Burst-tolerant grant window, independent from browser grants: VS Code
    # opens install + exec legs within the same second, so a hard 1/sec
    # throttle races them into 429s. Allow a small burst per short window;
    # abuse control is preserved (still N/minute overall).
    window_start = getattr(runtime, "ssh_grant_window_start", None)
    window_count = getattr(runtime, "ssh_grant_window_count", None) or 0
    if not window_start or utc(window_start) <= now() - timedelta(seconds=10):
        runtime.ssh_grant_window_start, runtime.ssh_grant_window_count = now(), 1
    elif window_count >= 5:
        raise HTTPException(429, "Wait before requesting another SSH connection")
    else:
        runtime.ssh_grant_window_count = window_count + 1
    for field in ("ssh_host_key",):
        if not getattr(runtime, field, None):
            raise HTTPException(409, "SSH host key not reported")
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
    runtime.ssh_connection_requested_at = now()
    resource_id, generation = runtime.resource_id, runtime.generation
    db.commit()
    grant = management.call(
        "POST",
        "access-grants",
        {
            "user": owner,
            "resource_id": resource_id,
            "generation": str(generation),
            "service": runtime.access_service,
            "gateway_id": os.environ["INTERACTIVE_GATEWAY_ID"],
            "authorized": True,
            "purpose": "ssh",
        },
    )
    db.expire_all()
    runtime = owned_runtime(db, owner, runtime_id)
    lock_worker(db, db.get(Assignment, runtime.assignment_id).worker_id)
    db.refresh(runtime)
    if (
        not ssh_grant_ready(db, runtime)
        or runtime.resource_id != resource_id
        or runtime.generation != generation
    ):
        db.rollback()
        management.call("DELETE", "access-grants/" + grant["grant_id"])
        raise HTTPException(409, "Runtime changed during SSH connection request")
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
        "purpose": "ssh",
        "protocol": "tcp-stream-v1",
        "service": runtime.access_service,
    }
