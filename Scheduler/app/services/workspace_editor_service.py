"""Durable owner-facing save and submission requests.

Capture/publish completion is deliberately Worker/Builder driven.  These
methods never report a revision or job as successful merely because a browser
clicked a button.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import and_

from app.models.interactive_workspace_model import (
    InteractiveWorkspace as Workspace, WorkspaceSaveOperation as Save,
    InteractiveImageRevision as Revision, WorkspaceTrainingSubmission as Submission, new_id,
)
from app.models.interactive_runtime_model import InteractiveRuntime as Runtime, WorkerAssignment as Assignment
from app.models.job_model import Job, JobStatus, JobPriority
from app.services.interactive_runtime_service import ready
from app.services.scheduling.config import Settings


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _owned_runtime(db, owner, runtime_id):
    runtime = db.query(Runtime).filter_by(id=runtime_id, owner_user_id=owner).with_for_update().first()
    if not runtime:
        raise HTTPException(404, "Runtime not found")
    return runtime


def save_public(item, db=None):
    value = {key: getattr(item, key) for key in (
        "id", "workspace_id", "runtime_id", "generation", "parent_revision_id", "purpose", "state",
        "target_revision_id", "head_advanced", "stop_after_save", "failure_code", "failure_detail", "created_at", "updated_at",
    )}
    rev = db.get(Revision, item.target_revision_id) if db and item.target_revision_id else None
    value["image_digest_ref"] = rev.image_digest_ref if rev and rev.state == "IMAGE_READY" else None
    return value


def submission_public(item):
    return {key: getattr(item, key) for key in (
        "id", "workspace_id", "runtime_id", "generation", "save_operation_id", "revision_id", "state", "job_id",
        "failure_code", "failure_detail", "created_at", "updated_at",
    )}


def create_save(db, owner, runtime_id, request_key, body, purpose="SAVE"):
    settings = Settings.from_env()
    if not settings.workspace_save:
        raise HTTPException(503, "Workspace saving is not enabled")
    runtime = _owned_runtime(db, owner, runtime_id)
    if body.generation != runtime.generation or body.parent_revision_id != runtime.revision_id:
        raise HTTPException(409, "Workspace source changed; reload before saving")
    payload = body.model_dump()
    request_hash = _hash({"purpose": purpose, **payload})
    existing = db.query(Save).filter_by(runtime_id=runtime.id, generation=runtime.generation, request_key=request_key).first()
    if existing:
        if existing.request_hash != request_hash or existing.purpose != purpose:
            raise HTTPException(409, "Idempotency key reused")
        return save_public(existing, db)
    if not runtime.editor_capable or not ready(db, runtime):
        raise HTTPException(409, "Workspace runtime is unavailable")
    active = db.query(Save).filter(
        Save.runtime_id == runtime.id, Save.generation == runtime.generation,
        Save.state.in_(("REQUESTED", "CAPTURING", "UPLOADING", "PUBLISH_QUEUED", "PUBLISHING")),
    ).first()
    if active:
        raise HTTPException(409, "A workspace save is already in progress")
    assignment = db.get(Assignment, runtime.assignment_id)
    if not assignment or assignment.released_at:
        raise HTTPException(409, "Workspace runtime is unavailable")
    workspace = db.query(Workspace).filter_by(id=runtime.workspace_id).with_for_update().one()
    item = Save(
        id=new_id(), owner_user_id=owner, workspace_id=runtime.workspace_id, runtime_id=runtime.id,
        generation=runtime.generation, assignment_id=assignment.id, attempt_token=assignment.attempt_token,
        parent_revision_id=runtime.revision_id, purpose=purpose, request_key=request_key,
        expected_saved_revision_id=workspace.saved_revision_id,
        request_hash=request_hash, state="REQUESTED",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return save_public(item, db)


def get_save(db, owner, save_id):
    item = db.query(Save).filter_by(id=save_id, owner_user_id=owner).first()
    if not item:
        raise HTTPException(404, "Save not found")
    return save_public(item, db)


def save_and_stop(db, owner, runtime_id, request_key, body):
    value = create_save(db, owner, runtime_id, request_key, body)
    item = db.query(Save).filter_by(id=value["id"], owner_user_id=owner).with_for_update().one()
    if item.purpose != "SAVE":
        raise HTTPException(409, "Training submission owns this save")
    item.stop_after_save = True
    db.commit()
    return save_public(item, db)


def create_submission(db, owner, runtime_id, request_key, body):
    if not Settings.from_env().workspace_training_submission:
        raise HTTPException(503, "Workspace training submission is not enabled")
    # Both flags are required; a training workflow must never enqueue a job
    # without the durable snapshot pipeline.
    if not Settings.from_env().workspace_save:
        raise HTTPException(503, "Workspace saving is not enabled")
    runtime = _owned_runtime(db, owner, runtime_id)
    if body.generation != runtime.generation or body.parent_revision_id != runtime.revision_id:
        raise HTTPException(409, "Workspace source changed; reload before submitting")
    request_hash = _hash(body.model_dump())
    existing = db.query(Submission).filter_by(runtime_id=runtime.id, generation=runtime.generation, request_key=request_key).first()
    if existing:
        if existing.request_hash != request_hash:
            raise HTTPException(409, "Idempotency key reused")
        return submission_public(existing)
    if not runtime.editor_capable or not ready(db, runtime):
        raise HTTPException(409, "Workspace runtime is unavailable")
    save = create_save(db, owner, runtime_id, request_key, body, purpose="TRAIN")
    item = Submission(
        id=new_id(), owner_user_id=owner, workspace_id=runtime.workspace_id, runtime_id=runtime.id,
        generation=runtime.generation, save_operation_id=save["id"], request_key=request_key,
        request_hash=request_hash, settings=body.settings.model_dump(), state="SAVING",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return submission_public(item)


def create_revision_submission(db, owner, workspace_id, revision_id, request_key, settings):
    if not Settings.from_env().workspace_training_submission:
        raise HTTPException(503, "Workspace training submission is not enabled")
    workspace = db.query(Workspace).filter_by(id=workspace_id, owner_user_id=owner).with_for_update().first()
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    revision = db.query(Revision).filter_by(id=revision_id, workspace_id=workspace_id,
                                           state="IMAGE_READY").first()
    if not revision or not revision.image_digest_ref:
        raise HTTPException(409, "Saved revision is not ready")
    hashed = _hash(settings.model_dump())
    existing = db.query(Submission).filter_by(owner_user_id=owner, workspace_id=workspace_id,
                                              revision_id=revision_id, request_key=request_key).first()
    if existing:
        if existing.request_hash != hashed:
            raise HTTPException(409, "Idempotency key reused")
        return submission_public(existing)
    live = db.query(Runtime).filter(Runtime.workspace_id == workspace_id,
                                    Runtime.state.notin_(("STOPPED", "FAILED"))).first()
    if live:
        raise HTTPException(409, "Stop the live runtime before training a saved revision")
    item = Submission(id=new_id(), owner_user_id=owner, workspace_id=workspace_id,
                      revision_id=revision_id, request_key=request_key, request_hash=hashed,
                      settings=settings.model_dump(), state="PREPARING_JOB")
    db.add(item)
    db.commit()
    return submission_public(item)


def _job_for_submission(db, item):
    revision = db.query(Revision).filter_by(id=item.revision_id, workspace_id=item.workspace_id,
                                            state="IMAGE_READY").with_for_update().first()
    digest = revision.image_digest_ref if revision else None
    if not digest or not re.fullmatch(r"[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}", digest):
        item.state, item.failure_code = "FAILED", "REVISION_UNAVAILABLE"
        return
    settings = item.settings or {}
    job = Job(
        id=new_id(), user_id=item.owner_user_id, source_kind="WORKSPACE_REVISION",
        source_workspace_id=item.workspace_id, source_revision_id=revision.id,
        source_image_digest_ref=digest, executable_image_digest_ref=digest,
        image_tag=digest, docker_base_image=digest,
        name=settings["name"], command=settings["command"],
        resume_command=settings.get("resume_command"),
        priority=JobPriority(settings.get("priority", "NORMAL")),
        reason_for_priority=settings.get("reason_for_priority"),
        status=JobStatus.VRAM_ESTIMATION_PENDING,
    )
    db.add(job)
    db.flush()
    item.job_id, item.state = job.id, "JOB_CREATED"


def reconcile(db):
    """Advance durable submissions after Save, release and Builder callbacks."""
    active = db.query(Submission).filter(Submission.state.notin_(("JOB_CREATED", "FAILED"))).order_by(
        Submission.created_at).with_for_update(skip_locked=True).limit(50).all()
    changed = 0
    for item in active:
        if item.save_operation_id and item.state in ("SAVING", "WAITING_FOR_REVISION"):
            save = db.get(Save, item.save_operation_id)
            if save.state in ("FAILED", "CANCELLED"):
                item.state, item.failure_code = "FAILED", save.failure_code or "SAVE_FAILED"
            elif save.state == "SUCCEEDED":
                item.revision_id, item.state = save.target_revision_id, "STOPPING_RUNTIME"
            else:
                item.state = "WAITING_FOR_REVISION"
        if item.state == "STOPPING_RUNTIME":
            from app.services.scheduling.claims import stop_runtime
            runtime = db.get(Runtime, item.runtime_id)
            if runtime and runtime.desired_state == "RUNNING":
                stop_runtime(runtime)
            item.state = "WAITING_FOR_RELEASE"
        if item.state == "WAITING_FOR_RELEASE":
            runtime = db.get(Runtime, item.runtime_id)
            assignment = db.get(Assignment, runtime.assignment_id) if runtime and runtime.assignment_id else None
            if runtime and runtime.state in ("STOPPED", "FAILED") and (not assignment or assignment.released_at):
                item.state = "PREPARING_JOB"
        if item.state == "PREPARING_JOB" and not item.job_id:
            _job_for_submission(db, item)
        changed += 1
    if changed:
        db.commit()
    # A lost Worker cannot complete an unpublished capture. Preserve the old
    # saved head and give the owner a terminal status.
    pending = db.query(Save).filter(Save.state.in_(("REQUESTED", "CAPTURING", "UPLOADING"))).limit(50).all()
    for save in pending:
        assignment = db.get(Assignment, save.assignment_id) if save.assignment_id else None
        if not assignment or assignment.released_at or assignment.state == "LOST":
            save.state, save.failure_code = "FAILED", "WORKER_LOST"
    if pending:
        db.commit()
    # Bound a permanently stalled upload or publication. The Builder lease
    # handles normal retries; this is the final owner-visible failure after
    # prolonged infrastructure loss, and leaves the previous head intact.
    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    stale = db.query(Save).filter(
        Save.state.in_(("REQUESTED", "CAPTURING", "UPLOADING", "PUBLISH_QUEUED", "PUBLISHING")),
        Save.created_at < cutoff,
    ).with_for_update(skip_locked=True).limit(50).all()
    for save in stale:
        save.state, save.failure_code = "FAILED", "SAVE_TIMEOUT"
        if save.target_revision_id:
            revision = db.get(Revision, save.target_revision_id)
            if revision and revision.state != "IMAGE_READY":
                revision.state = "FAILED"
                revision.failure_type = "system"
                revision.failure_reason = "Workspace save timed out"
                revision.builder_id = revision.attempt_id = None
                revision.started_at = revision.lease_until = None
    if stale:
        db.commit()
    from app.services import interactive_runtime_service as runtimes
    stops = db.query(Save).filter_by(state="SUCCEEDED", stop_after_save=True).limit(50).all()
    for save in stops:
        runtime = db.get(Runtime, save.runtime_id)
        if runtime and runtime.desired_state == "RUNNING":
            runtimes.stop(db, save.owner_user_id, runtime.id)
    return changed


def revisions(db, owner, workspace_id, limit=50, after=None):
    from app.models.interactive_workspace_model import InteractiveImageRevision as Revision
    workspace = db.query(Workspace).filter_by(id=workspace_id, owner_user_id=owner).first()
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    query = db.query(Revision).filter_by(workspace_id=workspace_id).order_by(Revision.revision_number.desc())
    if after is not None:
        query = query.filter(Revision.revision_number < after)
    rows = query.limit(limit).all()
    return {"saved_revision_id": workspace.saved_revision_id, "items": [{
        **{key: getattr(row, key) for key in ("id", "revision_number", "origin", "state", "image_digest_ref", "created_at")},
        "is_saved_head": row.id == workspace.saved_revision_id,
    } for row in rows]}
