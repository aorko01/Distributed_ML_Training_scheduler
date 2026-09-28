"""Phase 2b: private snapshot artifact handoff stays fenced and immutable."""
import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.models.interactive_runtime_model import WorkerAssignment as Assignment
from app.models.interactive_workspace_model import (
    InteractiveWorkspace as Workspace,
    InteractiveImageRevision as Revision,
    WorkspaceSaveOperation as Save,
    WorkspaceTrainingSubmission as Submission,
    new_id,
)
from app.services import snapshot_artifact_service as snapshots
from app.services import interactive_workspace_service as workspaces
from app.services import workspace_editor_service as editor
from app.schemas.interactive_workspace_schema import Ready
from app.schemas.workspace_editor_schema import TrainingRequest
from app.models.job_model import Job, JobStatus
from app.models.interactive_runtime_model import InteractiveRuntime as Runtime
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
        request_key=new_id(), request_hash="h", created_at=now, health_at=now,
        health={"workload": True, "broker": True, "access": True, "endpoint": True},
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
        created_at=now, lease_until=now + timedelta(seconds=45),
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


def test_live_snapshot_submission_creates_batch_job_and_stops_runtime(db):
    worker, assignment, op = _save(db)
    op.purpose = "TRAIN"
    submission = Submission(
        id=new_id(), owner_user_id=op.owner_user_id, workspace_id=op.workspace_id,
        runtime_id=op.runtime_id, generation=1, save_operation_id=op.id,
        request_key=op.request_key, request_hash="h",
        settings={"name": "fine tune", "command": "python train.py --epochs 2",
                  "resume_command": "python resume.py"}, state="SAVING",
    )
    db.add(submission)
    db.commit()

    offered = snapshots.pending(db, worker.worker_id, assignment.id, assignment.attempt_token, 1)
    assert offered["operation"]["id"] == op.id
    snapshots.issue_capability(db, worker.worker_id, op.id, assignment.id, assignment.attempt_token, 1)
    completed = snapshots.complete(db, worker.worker_id, op.id, assignment.id,
                                   assignment.attempt_token, 1, "c" * 64, 123)
    claimed = workspaces.claim(db, "builder-a")
    assert claimed["id"] == completed["target_revision_id"]
    tag = (f"user/interactive-{op.workspace_id}:revision-{claimed['id']}"
           f"-attempt-{claimed['attempt_id']}")
    digest = f"user/interactive-{op.workspace_id}@sha256:" + "d" * 64
    workspaces.mark_ready(db, Ready(
        builder_id="builder-a", revision_id=claimed["id"],
        attempt_id=claimed["attempt_id"], image_tag=tag,
        image_digest_ref=digest, resolved_base_digest=digest,
    ))

    db.refresh(op)
    db.refresh(submission)
    job = db.get(Job, submission.job_id)
    assert op.state == "SUCCEEDED"
    assert submission.state == "JOB_CREATED"
    assert job.source_kind == "WORKSPACE_REVISION"
    assert job.source_revision_id == claimed["id"]
    assert job.image_tag == digest
    assert job.status == JobStatus.VRAM_ESTIMATION_PENDING
    assert job.command == "python train.py --epochs 2"
    assert job.resume_command == "python resume.py"
    assert db.get(Runtime, op.runtime_id).desired_state == "STOPPED"
    workspaces.mark_ready(db, Ready(
        builder_id="builder-a", revision_id=claimed["id"],
        attempt_id=claimed["attempt_id"], image_tag=tag,
        image_digest_ref=digest, resolved_base_digest=digest,
    ))
    assert db.query(Job).filter_by(source_revision_id=claimed["id"]).count() == 1


def test_save_for_later_publishes_revision_without_training_job(db):
    worker, assignment, op = _save(db)
    snapshots.issue_capability(db, worker.worker_id, op.id, assignment.id, assignment.attempt_token, 1)
    result = snapshots.complete(db, worker.worker_id, op.id, assignment.id,
                                assignment.attempt_token, 1, "e" * 64, 123)
    claimed = workspaces.claim(db, "builder-a")
    tag = (f"user/interactive-{op.workspace_id}:revision-{claimed['id']}"
           f"-attempt-{claimed['attempt_id']}")
    digest = f"user/interactive-{op.workspace_id}@sha256:" + "f" * 64
    workspaces.mark_ready(db, Ready(
        builder_id="builder-a", revision_id=claimed["id"],
        attempt_id=claimed["attempt_id"], image_tag=tag,
        image_digest_ref=digest, resolved_base_digest=digest,
    ))
    db.refresh(op)
    assert op.state == "SUCCEEDED"
    assert db.query(Workspace).filter_by(id=op.workspace_id).one().saved_revision_id == result["target_revision_id"]
    assert db.query(Job).filter_by(source_revision_id=result["target_revision_id"]).count() == 0
    assert db.get(Runtime, op.runtime_id).desired_state == "RUNNING"


def test_failed_capture_marks_training_submission_failed(db):
    worker, assignment, op = _save(db)
    submission = Submission(
        id=new_id(), owner_user_id=op.owner_user_id, workspace_id=op.workspace_id,
        runtime_id=op.runtime_id, generation=1, save_operation_id=op.id,
        request_key=op.request_key, request_hash="h",
        settings={"name": "run", "command": "python train.py"}, state="SAVING",
    )
    db.add(submission)
    db.commit()
    snapshots.issue_capability(db, worker.worker_id, op.id, assignment.id, assignment.attempt_token, 1)
    snapshots.fail(db, worker.worker_id, op.id, assignment.id, assignment.attempt_token, 1, "CAPTURE_FAILED")
    db.refresh(submission)
    assert submission.state == "FAILED"
    assert snapshots.pending(db, worker.worker_id, assignment.id, assignment.attempt_token, 1)["operation"] is None


def test_submission_replay_survives_runtime_stop(db, monkeypatch):
    _, _, first_save = _save(db)
    first_save.state = "SUCCEEDED"
    db.commit()
    monkeypatch.setenv("WORKSPACE_SAVE_ENABLED", "1")
    monkeypatch.setenv("WORKSPACE_TRAINING_SUBMISSION_ENABLED", "1")
    body = TrainingRequest(
        generation=1, parent_revision_id=first_save.parent_revision_id,
        settings={"name": "run", "command": "python train.py"},
    )
    key = new_id()
    created = editor.create_submission(db, first_save.owner_user_id, first_save.runtime_id, key, body)
    runtime = db.get(Runtime, first_save.runtime_id)
    runtime.state = "STOPPING"
    runtime.desired_state = "STOPPED"
    db.commit()
    replay = editor.create_submission(db, first_save.owner_user_id, first_save.runtime_id, key, body)
    assert replay["id"] == created["id"]


def test_reconcile_fails_save_after_assignment_release(db):
    _, assignment, op = _save(db)
    from datetime import datetime, timezone
    assignment.released_at = datetime.now(timezone.utc)
    assignment.state = "RELEASED"
    assignment.cleanup_ack = True
    db.commit()
    assert snapshots.reconcile_stale(db) == 1
    db.refresh(op)
    assert op.state == "FAILED"
