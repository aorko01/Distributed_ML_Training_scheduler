"""Durable owner-facing save and submission requests.

Capture/publish completion is deliberately Worker/Builder driven.  These
methods never report a revision or job as successful merely because a browser
clicked a button.
"""
import hashlib
import json
from fastapi import HTTPException
from sqlalchemy import and_

from app.models.interactive_workspace_model import (
    InteractiveWorkspace as Workspace, WorkspaceSaveOperation as Save,
    WorkspaceTrainingSubmission as Submission, new_id,
)
from app.models.interactive_runtime_model import InteractiveRuntime as Runtime, WorkerAssignment as Assignment
from app.services.interactive_runtime_service import ready
from app.services.scheduling.config import Settings


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _owned_runtime(db, owner, runtime_id):
    runtime = db.query(Runtime).filter_by(id=runtime_id, owner_user_id=owner).with_for_update().first()
    if not runtime:
        raise HTTPException(404, "Runtime not found")
    if not runtime.editor_capable or not ready(db, runtime):
        raise HTTPException(409, "Workspace runtime is unavailable")
    return runtime


def save_public(item):
    return {key: getattr(item, key) for key in (
        "id", "workspace_id", "runtime_id", "generation", "parent_revision_id", "purpose", "state",
        "target_revision_id", "failure_code", "failure_detail", "created_at", "updated_at",
    )}


def submission_public(item):
    return {key: getattr(item, key) for key in (
        "id", "workspace_id", "runtime_id", "generation", "save_operation_id", "state", "job_id",
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
        return save_public(existing)
    active = db.query(Save).filter(
        Save.runtime_id == runtime.id, Save.generation == runtime.generation,
        Save.state.in_(("REQUESTED", "CAPTURING", "UPLOADING", "PUBLISH_QUEUED", "PUBLISHING")),
    ).first()
    if active:
        raise HTTPException(409, "A workspace save is already in progress")
    assignment = db.get(Assignment, runtime.assignment_id)
    if not assignment or assignment.released_at:
        raise HTTPException(409, "Workspace runtime is unavailable")
    item = Save(
        id=new_id(), owner_user_id=owner, workspace_id=runtime.workspace_id, runtime_id=runtime.id,
        generation=runtime.generation, assignment_id=assignment.id, attempt_token=assignment.attempt_token,
        parent_revision_id=runtime.revision_id, purpose=purpose, request_key=request_key,
        request_hash=request_hash, state="REQUESTED",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return save_public(item)


def get_save(db, owner, save_id):
    item = db.query(Save).filter_by(id=save_id, owner_user_id=owner).first()
    if not item:
        raise HTTPException(404, "Save not found")
    return save_public(item)


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
        key: getattr(row, key) for key in ("id", "revision_number", "origin", "state", "image_digest_ref", "created_at")
    } for row in rows]}
