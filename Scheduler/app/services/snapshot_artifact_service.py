"""Fenced snapshot artifact handoff between Worker and Builder.

The browser never receives the object key. A live Worker receives an
operation-scoped prefix; the Builder receives the immutable hash-addressed
key only after the Worker reports completion.
"""
import re
from datetime import datetime, timezone, timedelta

from fastapi import HTTPException
from sqlalchemy import func

from app.models.interactive_runtime_model import WorkerAssignment as Assignment
from app.models.interactive_runtime_model import InteractiveRuntime as Runtime
from app.models.interactive_workspace_model import (
    InteractiveImageRevision as Revision,
    WorkspaceSaveOperation as Save,
    WorkspaceTrainingSubmission as Submission,
    new_id,
)

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_ARTIFACT = 8 * 1024 * 1024 * 1024
CAPABILITY_TTL_SECONDS = 1200


def staging_key(workspace_id, parent_revision_id, operation_id, sha256):
    """Immutable artifact key; no overwrite, abort only the exact upload."""
    for value in (workspace_id, parent_revision_id, operation_id):
        if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
            raise HTTPException(422, "Invalid snapshot identifier")
    if not isinstance(sha256, str) or not SHA_RE.fullmatch(sha256):
        raise HTTPException(422, "Invalid snapshot hash")
    return f"snapshots/{workspace_id}/{parent_revision_id}/{operation_id}/{sha256}.tar.gz"


def _fenced_save(db, worker_id, operation_id, assignment_id, attempt_token, generation):
    op = (
        db.query(Save)
        .filter_by(id=operation_id)
        .with_for_update()
        .first()
    )
    if not op:
        raise HTTPException(404, "Save not found")
    assignment = db.get(Assignment, op.assignment_id)
    if (
        not assignment
        or assignment.id != assignment_id
        or assignment.worker_id != worker_id
        or assignment.attempt_token != attempt_token
        or assignment.generation != generation
        or op.generation != generation
        or assignment.released_at
        or (assignment.lease_until.replace(tzinfo=timezone.utc)
            if assignment.lease_until.tzinfo is None else assignment.lease_until) <= datetime.now(timezone.utc)
    ):
        raise HTTPException(409, "Stale assignment")
    return op, assignment


def issue_capability(db, worker_id, operation_id, assignment_id, attempt_token, generation):
    """Return an operation-scoped staging descriptor for the fenced Worker."""
    op, _ = _fenced_save(db, worker_id, operation_id, assignment_id, attempt_token, generation)
    if op.state not in ("REQUESTED", "CAPTURING"):
        raise HTTPException(409, "Save is not capturable")
    op.state = "CAPTURING"
    op.capture_attempt_id = op.capture_attempt_id or new_id()
    db.commit()
    db.refresh(op)
    # Placeholder hash segment keeps the key stable until complete() pins the
    # real sha; the final key is immutable and never overwritten.
    return {
        "operation_id": op.id,
        "capture_attempt_id": op.capture_attempt_id,
        "bucket": "uploads",
        "object_key_prefix": f"snapshots/{op.workspace_id}/{op.parent_revision_id}/{op.id}/",
        "expires_seconds": CAPABILITY_TTL_SECONDS,
    }


def pending(db, worker_id, assignment_id, attempt_token, generation):
    """Offer only saves attached to this worker's live, ready assignment."""
    assignment = db.get(Assignment, assignment_id)
    if (not assignment or assignment.worker_id != worker_id or
            assignment.attempt_token != attempt_token or
            assignment.generation != generation or assignment.released_at):
        raise HTTPException(409, "Stale assignment")
    runtime = db.query(Runtime).filter_by(assignment_id=assignment_id, generation=generation,
                                           state="READY", desired_state="RUNNING").first()
    from app.services.interactive_runtime_service import ready
    if not runtime or not ready(db, runtime):
        return {"operation": None}
    op = db.query(Save).filter_by(assignment_id=assignment_id, generation=generation).filter(
        Save.state.in_(("REQUESTED", "CAPTURING"))).order_by(Save.created_at).first()
    return {"operation": {"id": op.id, "workspace_id": op.workspace_id,
                           "parent_revision_id": op.parent_revision_id} if op else None}


def fail(db, worker_id, operation_id, assignment_id, attempt_token, generation, code):
    op, _ = _fenced_save(db, worker_id, operation_id, assignment_id, attempt_token, generation)
    if op.state in ("REQUESTED", "CAPTURING", "UPLOADING"):
        op.state = "FAILED"
        op.failure_code = code
        op.failure_detail = "Could not capture this workspace. Check the session and try again."
        submission = db.query(Submission).filter_by(save_operation_id=op.id).first()
        if submission:
            submission.state = "FAILED"
            submission.failure_code = code
            submission.failure_detail = op.failure_detail
        db.commit()
    return {"state": op.state}


def reconcile_stale(db):
    """Finish saves whose live assignment disappeared during capture."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
    changed = 0
    operations = db.query(Save).filter(Save.state.in_(("REQUESTED", "CAPTURING", "UPLOADING"))).all()
    for op in operations:
        assignment = db.get(Assignment, op.assignment_id) if op.assignment_id else None
        runtime = db.get(Runtime, op.runtime_id)
        expired = op.created_at and (op.created_at.replace(tzinfo=timezone.utc)
                                     if op.created_at.tzinfo is None else op.created_at) < cutoff
        if assignment and not assignment.released_at and runtime and runtime.state not in ("STOPPED", "FAILED", "LOST") and not expired:
            continue
        op.state = "FAILED"
        op.failure_code = "RUNTIME_UNAVAILABLE"
        op.failure_detail = "Interactive session ended before the workspace was saved."
        submission = db.query(Submission).filter_by(save_operation_id=op.id).first()
        if submission:
            submission.state = "FAILED"
            submission.failure_code = op.failure_code
            submission.failure_detail = op.failure_detail
        changed += 1
    if changed:
        db.commit()
    return changed


def complete(db, worker_id, operation_id, assignment_id, attempt_token, generation, sha256, size):
    """Record the fenced upload claim; Builder verifies bytes before publishing."""
    if not isinstance(sha256, str) or not SHA_RE.fullmatch(sha256):
        raise HTTPException(422, "Invalid snapshot hash")
    if not isinstance(size, int) or not 0 < size <= MAX_ARTIFACT:
        raise HTTPException(422, "Invalid snapshot size")
    op, assignment = _fenced_save(db, worker_id, operation_id, assignment_id, attempt_token, generation)
    if op.state not in ("CAPTURING", "UPLOADING"):
        raise HTTPException(409, "Save is not uploadable")
    if op.artifact_sha256 and (op.artifact_sha256 != sha256 or op.artifact_size != size):
        raise HTTPException(409, "Snapshot receipt conflict")
    key = staging_key(op.workspace_id, op.parent_revision_id, op.id, sha256)
    op.artifact_sha256, op.artifact_size = sha256, size
    op.state = "UPLOADING"
    db.flush()
    # Enqueue one immutable SNAPSHOT revision (CAS on late callbacks is in
    # mark_ready/saved_revision_id). Never clobber a newer head here.
    number = (
        db.query(func.max(Revision.revision_number))
        .filter_by(workspace_id=op.workspace_id)
        .scalar()
        or 0
    ) + 1
    launch = (assignment.payload or {}).get("launch_spec", {}) if isinstance(assignment.payload, dict) else {}
    rev = Revision(
        id=new_id(),
        workspace_id=op.workspace_id,
        revision_number=number,
        origin="SNAPSHOT",
        state="QUEUED",
        source_object_key=key,
        parent_revision_id=op.parent_revision_id,
        snapshot_operation_id=op.id,
        source_image_metadata={
            "sha256": sha256,
            "size": size,
            "platform": launch.get("platform", "linux/amd64"),
            "user": "10001:10001",
            "workdir": "/workspace",
        },
    )
    db.add(rev)
    db.flush()
    op.target_revision_id = rev.id
    op.state = "PUBLISH_QUEUED"
    db.commit()
    db.refresh(op)
    return {"operation_id": op.id, "state": op.state, "target_revision_id": rev.id, "object_key": key}


def builder_descriptor(db, operation_id):
    """Builder-side read descriptor; Builder auth enforced at the route."""
    op = db.query(Save).filter_by(id=operation_id).first()
    if not op or not op.artifact_sha256 or not op.target_revision_id:
        raise HTTPException(404, "Snapshot not found")
    rev = db.query(Revision).filter_by(id=op.target_revision_id).first()
    if not rev or not rev.source_object_key:
        raise HTTPException(404, "Snapshot not found")
    meta = rev.source_image_metadata or {}
    return {
        "operation_id": op.id,
        "bucket": "uploads",
        "object_key": rev.source_object_key,
        "sha256": op.artifact_sha256,
        "size": op.artifact_size,
        "platform": meta.get("platform", "linux/amd64"),
        "user": meta.get("user", "10001:10001"),
        "workdir": meta.get("workdir", "/workspace"),
        "target_revision_id": rev.id,
    }
