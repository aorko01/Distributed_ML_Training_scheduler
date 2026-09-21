"""Phase 2b: private snapshot artifact handoff stays fenced and immutable."""
import uuid

import pytest
from fastapi import HTTPException

from app.models.interactive_runtime_model import WorkerAssignment as Assignment
from app.models.interactive_workspace_model import (
    InteractiveWorkspace as Workspace,
    InteractiveImageRevision as Revision,
    WorkspaceSaveOperation as Save,
    new_id,
)
from app.services import snapshot_artifact_service as snapshots
from test.helpers import make_user, make_worker


def _save(db):
    user = make_user(db)
    worker = make_worker(db)
    ws = Workspace(
        id=new_id(), owner_user_id=user.user_id, name="ws",
        source_type="UPLOAD", request_key=new_id(), request_hash="h",
    )
    db.add(ws)
    db.flush()
    rev = Revision(
        id=new_id(), workspace_id=ws.id, revision_number=1, origin="UPLOAD",
        state="IMAGE_READY", source_object_key="k", requested_base_image="pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime",
        image_tag="user/t:1", image_digest_ref="user/t@sha256:" + "a" * 64,
        resolved_base_digest="pytorch/pytorch@sha256:" + "b" * 64,
    )
    db.add(rev)
    db.flush()
    ws.current_revision_id = rev.id
    ws.saved_revision_id = rev.id
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    runtime_id, assignment_id = new_id(), new_id()
    token = new_id()
    from app.models.interactive_runtime_model import InteractiveRuntime as Runtime
    rt = Runtime(
        id=runtime_id, workspace_id=ws.id, owner_user_id=user.user_id,
        revision_id=rev.id, image_digest_ref=rev.image_digest_ref, generation=1,
        profile_version="gpu-v1", launch_spec={"platform": "linux/amd64"},
        request_key=new_id(), request_hash="h", created_at=now,
        desired_state="RUNNING", state="READY", assignment_id=assignment_id,
        access_service="workspace", application_protocol="workspace-stream-v1",
        editor_capable=True,
    )
    db.add(rt)
    db.flush()
    assignment = Assignment(
        id=assignment_id, worker_id=worker.worker_id, instance_id=new_id(),
        kind="interactive_access", runtime_id=runtime_id, generation=1,
        exclusive=True, payload={"launch_spec": {"platform": "linux/amd64"}},
        attempt_token=token, request_id=new_id(), request_hash="h",
        created_at=now, lease_until=now,
    )
    db.add(assignment)
    db.flush()
    op = Save(
        id=new_id(), owner_user_id=user.user_id, workspace_id=ws.id,
        runtime_id=runtime_id, generation=1, assignment_id=assignment_id,
        attempt_token=token, parent_revision_id=rev.id, purpose="SAVE",
        request_key=new_id(), request_hash="h", state="REQUESTED",
    )
    db.add(op)
    db.commit()
    return worker, assignment, op


def test_staging_key_rejects_traversal():
    with pytest.raises(HTTPException):
        snapshots.staging_key("ws", "rev", "op", "z" * 64)
    with pytest.raises(HTTPException):
        snapshots.staging_key("../x", "rev", "op", "a" * 64)
    key = snapshots.staging_key("ws1", "rev1", "op1", "a" * 64)
    assert key == "snapshots/ws1/rev1/op1/" + "a" * 64 + ".tar.gz"


def test_capability_and_complete_enqueue_snapshot_revision(db):
    worker, assignment, op = _save(db)
    cap = snapshots.issue_capability(
        db, worker.worker_id, op.id, assignment.id, assignment.attempt_token, 1,
    )
    assert cap["object_key_prefix"].startswith(f"snapshots/{op.workspace_id}/")
    assert cap["bucket"] == "uploads"
    result = snapshots.complete(
        db, worker.worker_id, op.id, assignment.id, assignment.attempt_token, 1,
        "b" * 64, 123,
    )
    assert result["state"] == "PUBLISH_QUEUED"
    assert result["object_key"].endswith("b" * 64 + ".tar.gz")
    rev = db.query(Revision).filter_by(id=result["target_revision_id"]).one()
    assert rev.origin == "SNAPSHOT" and rev.state == "QUEUED"
    desc = snapshots.builder_descriptor(db, op.id)
    assert desc["object_key"] == result["object_key"]
    assert desc["sha256"] == "b" * 64 and desc["platform"] == "linux/amd64"


def test_stale_worker_cannot_complete(db):
    worker, assignment, op = _save(db)
    snapshots.issue_capability(db, worker.worker_id, op.id, assignment.id, assignment.attempt_token, 1)
    other = make_worker(db)
    with pytest.raises(HTTPException) as exc:
        snapshots.complete(db, other.worker_id, op.id, assignment.id, assignment.attempt_token, 1, "b" * 64, 1)
    assert exc.value.status_code == 409


def test_double_complete_with_different_hash_conflicts(db):
    worker, assignment, op = _save(db)
    snapshots.issue_capability(db, worker.worker_id, op.id, assignment.id, assignment.attempt_token, 1)
    snapshots.complete(db, worker.worker_id, op.id, assignment.id, assignment.attempt_token, 1, "b" * 64, 10)
    with pytest.raises(HTTPException) as exc:
        snapshots.complete(db, worker.worker_id, op.id, assignment.id, assignment.attempt_token, 1, "b" * 64, 10)
    assert exc.value.status_code == 409
