"""Private snapshot artifact handoff (plan.md Phase 2b).

Browser never sees capabilities: only the fenced Worker holding the live
assignment gets a single-use staging key, and only the Builder reads it back.
Generic object upload/presign routes are never used for snapshots.
"""
import os
import re
import stat
import requests

from fastapi import HTTPException
from sqlalchemy import func

from app.models.interactive_runtime_model import WorkerAssignment as Assignment
from app.models.interactive_workspace_model import (
    InteractiveImageRevision as Revision,
    WorkspaceSaveOperation as Save,
    WorkspaceSnapshotArtifact as Artifact,
    new_id,
)

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_ARTIFACT = 8 * 1024 * 1024 * 1024
CAPABILITY_TTL_SECONDS = 3600


def _storage(body, path):
    """Only Scheduler can ask the private object service to mint/attest."""
    secret_file = os.getenv("SNAPSHOT_SERVICE_SECRET_FILE")
    base = os.getenv("OBJECT_STORE_URL", "http://localhost:8010").rstrip("/")
    if not secret_file:
        raise HTTPException(503, "Snapshot storage unavailable")
    try:
        fd = os.open(secret_file, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or not 32 <= info.st_size <= 256:
                raise ValueError()
            secret = os.read(fd, 257).strip().decode("ascii")
        finally:
            os.close(fd)
        response = requests.post(base + "/objects/internal/snapshots/" + path,
                                 json=body, headers={"Authorization": "Bearer " + secret},
                                 timeout=(5, 60))
        if response.status_code == 404:
            raise HTTPException(409, "Snapshot object is not available")
        response.raise_for_status()
        value = response.json()
        for field in ("upload_url", "download_url"):
            if field in value:
                value[field] = base + value[field]
        return value
    except HTTPException:
        raise
    except (OSError, ValueError, UnicodeError, requests.RequestException):
        raise HTTPException(503, "Snapshot storage unavailable") from None


def staging_key(workspace_id, parent_revision_id, operation_id, sha256):
    """Immutable artifact key; no overwrite, abort only the exact upload."""
    for value in (workspace_id, parent_revision_id, operation_id):
        if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
            raise HTTPException(422, "Invalid snapshot identifier")
    if not isinstance(sha256, str) or not SHA_RE.fullmatch(sha256):
        raise HTTPException(422, "Invalid snapshot hash")
    return f"snapshots/{workspace_id}/{parent_revision_id}/{operation_id}/{sha256}.tar.gz"


def _fenced_save(db, worker_id, operation_id, assignment_id, attempt_token, generation, instance_id=None):
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
        or (instance_id is not None and assignment.instance_id != instance_id)
        or assignment.attempt_token != attempt_token
        or assignment.generation != generation
        or op.generation != generation
        or assignment.released_at
    ):
        raise HTTPException(409, "Stale assignment")
    return op, assignment


def issue_capability(db, worker_id, operation_id, assignment_id, attempt_token, generation, sha256, size, image_id, instance_id=None):
    """Return a single-use staging descriptor for the fenced Worker."""
    op, _ = _fenced_save(db, worker_id, operation_id, assignment_id, attempt_token, generation, instance_id)
    if op.state not in ("CAPTURING", "UPLOADING"):
        raise HTTPException(409, "Save is not capturable")
    if op.artifact_sha256 and (op.artifact_sha256 != sha256 or op.artifact_size != size or op.image_id != image_id):
        raise HTTPException(409, "Snapshot artifact conflict")
    key = staging_key(op.workspace_id, op.parent_revision_id, op.id, sha256)
    capability = _storage({"operation_id": op.id, "object_key": key, "sha256": sha256, "size": size}, "capability")
    op.artifact_sha256, op.artifact_size, op.image_id = sha256, size, image_id
    op.state = "UPLOADING"
    op.capture_attempt_id = op.capture_attempt_id or new_id()
    db.commit()
    return {
        "operation_id": op.id,
        "capture_attempt_id": op.capture_attempt_id,
        "upload_url": capability["upload_url"],
        "upload_token": capability["upload_token"],
        "expires_seconds": capability["expires_seconds"],
    }


def complete(db, worker_id, operation_id, assignment_id, attempt_token, generation, sha256, size, image_id, storage_version, instance_id=None):
    """Verify receipt/hash/size, enqueue Builder SNAPSHOT publication."""
    if not isinstance(sha256, str) or not SHA_RE.fullmatch(sha256):
        raise HTTPException(422, "Invalid snapshot hash")
    if not isinstance(size, int) or not 0 < size <= MAX_ARTIFACT:
        raise HTTPException(422, "Invalid snapshot size")
    op, assignment = _fenced_save(db, worker_id, operation_id, assignment_id, attempt_token, generation, instance_id)
    if op.state in ("PUBLISH_QUEUED", "PUBLISHING", "SUCCEEDED"):
        if (op.artifact_sha256, op.artifact_size, op.image_id, op.storage_version) != (sha256, size, image_id, storage_version):
            raise HTTPException(409, "Snapshot receipt conflict")
        return {"operation_id": op.id, "state": op.state, "target_revision_id": op.target_revision_id}
    if op.state != "UPLOADING":
        raise HTTPException(409, "Save is not uploadable")
    if (op.artifact_sha256, op.artifact_size, op.image_id) != (sha256, size, image_id):
        raise HTTPException(409, "Snapshot receipt conflict")
    key = staging_key(op.workspace_id, op.parent_revision_id, op.id, sha256)
    receipt = _storage({"operation_id": op.id, "object_key": key, "sha256": sha256, "size": size}, "receipt")
    if receipt.get("storage_version") != storage_version or receipt.get("sha256") != sha256 or receipt.get("size") != size:
        raise HTTPException(409, "Snapshot receipt conflict")
    op.storage_version = storage_version
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
        parent_revision_id=op.expected_saved_revision_id or op.parent_revision_id,
        source_runtime_revision_id=op.parent_revision_id,
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
    artifact = Artifact(
        id=new_id(), operation_id=op.id, upload_attempt_id=op.capture_attempt_id,
        storage_version=storage_version, sha256=sha256, size=size,
        platform=launch.get("platform", "linux/amd64"), image_id=image_id, state="ACCEPTED",
    )
    db.add(artifact)
    db.flush()
    op.artifact_id = artifact.id
    op.target_revision_id = rev.id
    op.state = "PUBLISH_QUEUED"
    db.commit()
    db.refresh(op)
    return {"operation_id": op.id, "state": op.state, "target_revision_id": rev.id}


def builder_descriptor(db, operation_id):
    """Builder-side read descriptor; Builder auth enforced at the route."""
    op = db.query(Save).filter_by(id=operation_id).first()
    if not op or not op.artifact_sha256 or not op.target_revision_id:
        raise HTTPException(404, "Snapshot not found")
    rev = db.query(Revision).filter_by(id=op.target_revision_id).first()
    if not rev or not rev.source_object_key:
        raise HTTPException(404, "Snapshot not found")
    meta = rev.source_image_metadata or {}
    capability = _storage({"operation_id": op.id, "object_key": rev.source_object_key,
                           "sha256": op.artifact_sha256, "size": op.artifact_size}, "download-capability")
    return {
        "operation_id": op.id,
        "download_url": capability["download_url"],
        "download_token": capability["download_token"],
        "sha256": op.artifact_sha256,
        "size": op.artifact_size,
        "storage_version": op.storage_version,
        "platform": meta.get("platform", "linux/amd64"),
        "user": meta.get("user", "10001:10001"),
        "workdir": meta.get("workdir", "/workspace"),
        "target_revision_id": rev.id,
    }


def fail(db, worker_id, operation_id, assignment_id, attempt_token, generation, code, instance_id=None):
    op, _ = _fenced_save(db, worker_id, operation_id, assignment_id, attempt_token, generation, instance_id)
    if op.state in ("SUCCEEDED", "PUBLISH_QUEUED", "PUBLISHING"):
        raise HTTPException(409, "Save already published")
    if op.state != "FAILED":
        op.state, op.failure_code = "FAILED", code
        db.commit()
    return {"operation_id": op.id, "state": op.state}
