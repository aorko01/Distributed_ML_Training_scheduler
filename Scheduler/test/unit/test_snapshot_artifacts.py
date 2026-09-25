"""Phase 2b: private snapshot artifact handoff stays fenced and immutable."""
import uuid
from datetime import datetime, timezone

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


def test_saved_head_cas_keeps_newer_publication(db):
    from app.services.interactive_workspace_service import _publish_snapshot_head
    worker, assignment, op = _save(db)
    ws = db.get(Workspace, op.workspace_id)
    original = ws.saved_revision_id
    op.expected_saved_revision_id = original
    first = Revision(id=new_id(), workspace_id=ws.id, revision_number=2,
                     origin="SNAPSHOT", state="IMAGE_READY", snapshot_operation_id=op.id,
                     parent_revision_id=original, source_runtime_revision_id=original,
                     image_tag="repo:first", image_digest_ref="repo@sha256:" + "c" * 64,
                     resolved_base_digest="repo@sha256:" + "c" * 64)
    db.add(first)
    db.flush()
    op.target_revision_id, op.state = first.id, "PUBLISH_QUEUED"
    db.flush()
    _publish_snapshot_head(db, first)
    db.commit()
    assert ws.saved_revision_id == first.id and op.head_advanced is True

    second_op = Save(id=new_id(), owner_user_id=op.owner_user_id,
                     workspace_id=ws.id, runtime_id=op.runtime_id, generation=op.generation,
                     assignment_id=assignment.id, attempt_token=assignment.attempt_token,
                     parent_revision_id=original, expected_saved_revision_id=original,
                     purpose="SAVE", request_key=new_id(), request_hash="h", state="PUBLISH_QUEUED")
    db.add(second_op)
    db.flush()
    second = Revision(id=new_id(), workspace_id=ws.id, revision_number=3,
                      origin="SNAPSHOT", state="IMAGE_READY", snapshot_operation_id=second_op.id,
                      parent_revision_id=original, source_runtime_revision_id=original,
                      image_tag="repo:second", image_digest_ref="repo@sha256:" + "d" * 64,
                      resolved_base_digest="repo@sha256:" + "d" * 64)
    db.add(second)
    db.flush()
    second_op.target_revision_id = second.id
    db.flush()
    _publish_snapshot_head(db, second)
    db.commit()
    assert ws.saved_revision_id == first.id
    assert second_op.state == "SUCCEEDED" and second_op.head_advanced is False


def test_direct_saved_revision_submission_creates_one_digest_pinned_job(db, monkeypatch):
    from app.models.interactive_runtime_model import InteractiveRuntime as Runtime
    from app.models.job_model import Job, JobStatus
    from app.schemas.workspace_editor_schema import TrainingSettings
    from app.services import workspace_editor_service as editor
    worker, assignment, op = _save(db)
    runtime = db.get(Runtime, op.runtime_id)
    runtime.state = "STOPPED"
    assignment.state = "RELEASED"
    assignment.cleanup_ack = True
    assignment.released_at = datetime.now(timezone.utc)
    db.commit()
    monkeypatch.setenv("WORKSPACE_TRAINING_SUBMISSION_ENABLED", "1")
    settings = TrainingSettings(name="Saved training", command="python train.py", priority="NORMAL")
    first = editor.create_revision_submission(db, op.owner_user_id, op.workspace_id,
                                               op.parent_revision_id, "a" * 16, settings)
    editor.reconcile(db)
    again = editor.create_revision_submission(db, op.owner_user_id, op.workspace_id,
                                               op.parent_revision_id, "a" * 16, settings)
    assert first["id"] == again["id"]
    job = db.get(Job, again["job_id"])
    assert job.status == JobStatus.VRAM_ESTIMATION_PENDING
    assert job.source_kind == "WORKSPACE_REVISION"
    assert job.image_tag == job.source_image_digest_ref == job.executable_image_digest_ref
    assert db.query(Job).filter_by(source_revision_id=op.parent_revision_id).count() == 1


def _trusted_storage(body, path):
    if path == "capability":
        return {"upload_url": "https://store.test/upload", "upload_token": "scoped", "expires_seconds": 3600}
    if path == "receipt":
        return {"sha256": body["sha256"], "size": body["size"], "storage_version": "immutable-v1"}
    if path == "download-capability":
        return {"download_url": "https://store.test/download", "download_token": "scoped", "expires_seconds": 3600}
    raise AssertionError(path)


def test_capability_and_complete_enqueue_snapshot_revision(db, monkeypatch):
    worker, assignment, op = _save(db)
    op.state = "CAPTURING"
    db.commit()
    monkeypatch.setattr(snapshots, "_storage", _trusted_storage)
    image_id = "sha256:" + "c" * 64
    cap = snapshots.issue_capability(
        db, worker.worker_id, op.id, assignment.id, assignment.attempt_token, 1,
        "b" * 64, 123, image_id,
    )
    assert cap["upload_url"] == "https://store.test/upload"
    assert cap["upload_token"] == "scoped"
    result = snapshots.complete(
        db, worker.worker_id, op.id, assignment.id, assignment.attempt_token, 1,
        "b" * 64, 123, image_id, "immutable-v1",
    )
    assert result["state"] == "PUBLISH_QUEUED"
    rev = db.query(Revision).filter_by(id=result["target_revision_id"]).one()
    assert rev.origin == "SNAPSHOT" and rev.state == "QUEUED"
    desc = snapshots.builder_descriptor(db, op.id)
    assert desc["download_url"] == "https://store.test/download"
    assert desc["sha256"] == "b" * 64 and desc["platform"] == "linux/amd64"
    again = snapshots.complete(db, worker.worker_id, op.id, assignment.id, assignment.attempt_token, 1,
                               "b" * 64, 123, image_id, "immutable-v1")
    assert again["target_revision_id"] == rev.id


def test_stale_worker_cannot_complete(db, monkeypatch):
    worker, assignment, op = _save(db)
    op.state = "CAPTURING"
    db.commit()
    monkeypatch.setattr(snapshots, "_storage", _trusted_storage)
    snapshots.issue_capability(db, worker.worker_id, op.id, assignment.id, assignment.attempt_token, 1,
                               "b" * 64, 1, "sha256:" + "c" * 64)
    other = make_worker(db)
    with pytest.raises(HTTPException) as exc:
        snapshots.complete(db, other.worker_id, op.id, assignment.id, assignment.attempt_token, 1,
                           "b" * 64, 1, "sha256:" + "c" * 64, "immutable-v1")
    assert exc.value.status_code == 409


def test_double_complete_with_different_hash_conflicts(db, monkeypatch):
    worker, assignment, op = _save(db)
    op.state = "CAPTURING"
    db.commit()
    monkeypatch.setattr(snapshots, "_storage", _trusted_storage)
    image_id = "sha256:" + "c" * 64
    snapshots.issue_capability(db, worker.worker_id, op.id, assignment.id, assignment.attempt_token, 1,
                               "b" * 64, 10, image_id)
    snapshots.complete(db, worker.worker_id, op.id, assignment.id, assignment.attempt_token, 1,
                       "b" * 64, 10, image_id, "immutable-v1")
    with pytest.raises(HTTPException) as exc:
        snapshots.complete(db, worker.worker_id, op.id, assignment.id, assignment.attempt_token, 1,
                           "a" * 64, 10, image_id, "immutable-v1")
    assert exc.value.status_code == 409
