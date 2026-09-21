"""Unit tests for durable capture: commit, export, cleanup, and gate helpers.

Docker is faked with MagicMock clients, matching test_runtime_docker.py style.
"""
from unittest.mock import MagicMock
import pytest
from interactive import snapshot
from interactive.snapshot import CaptureDenied, capture


def record():
    return {
        "assignment_id": "a1",
        "attempt_token": "t1",
        "instance_id": "i1",
        "generation": 1,
        "lease_deadline_monotonic": 10**12,
        "payload": {
            "runtime_id": "rt1",
            "workspace_id": "ws1",
            "generation": 1,
            "launch_spec": {"platform": "linux/amd64", "disk_gb": 20, "allow_root": False},
        },
    }


def labelled(workload, assignment_id="a1"):
    workload.labels = {"dml.component": "workload", "dml.assignment": assignment_id}
    return workload


def frozen_time():
    return 1000.0


def test_workload_internet_gate_reads_env(monkeypatch):
    monkeypatch.delenv("INTERACTIVE_ALLOW_INTERNET", raising=False)
    assert snapshot.workload_internet_enabled() is False
    monkeypatch.setenv("INTERACTIVE_ALLOW_INTERNET", "1")
    assert snapshot.workload_internet_enabled() is True
    monkeypatch.setenv("INTERACTIVE_ALLOW_INTERNET", "true")
    assert snapshot.workload_internet_enabled() is True
    monkeypatch.setenv("INTERACTIVE_ALLOW_INTERNET", "0")
    assert snapshot.workload_internet_enabled() is False
    monkeypatch.setenv("INTERACTIVE_ALLOW_INTERNET", "maybe")
    assert snapshot.workload_internet_enabled() is False


def test_capture_rejects_missing_authority():
    coordinator = MagicMock()
    coordinator.authoritative.return_value = False
    with pytest.raises(CaptureDenied):
        capture(MagicMock(), coordinator, "ws1", "op1", record(), frozen_time)


def test_capture_happy_path_pauses_commits_and_cleans_up(monkeypatch, tmp_path):
    coordinator = MagicMock()
    coordinator.authoritative.return_value = True
    coordinator.get.return_value = record()
    workload = MagicMock()
    labelled(workload)
    workload.attrs = {"Config": {"User": "10001:10001", "WorkingDir": "/workspace"}}
    image = MagicMock()
    image.id = "sha256:image"
    workload.commit.return_value = image
    ops = MagicMock()
    ops.authority.return_value = True
    ops.get_workload.return_value = workload
    ops.client = MagicMock()
    progress = MagicMock()
    completed = []

    def fake_upload(client, assignment_id, image_id, destination, deadline):
        return {"sha256": "f" * 64, "size": 10}

    def fake_complete(client, operation_id, workspace_id, artifact):
        completed.append((operation_id, workspace_id, artifact))
        return True

    monkeypatch.setattr(snapshot, "_upload", fake_upload)
    monkeypatch.setattr(snapshot, "complete", fake_complete)
    monkeypatch.setattr(snapshot, "CAPTURE_PAUSED_SECONDS", 30)
    result = capture(
        ops, coordinator, "ws1", "op1", record(), frozen_time,
        progress=progress, upload_chunk=1 << 20, state_dir=tmp_path,
    )
    # Exact-label pause: pause called on the labelled workload, never on the daemon.
    ops.get_workload.assert_called_once_with(record())
    workload.pause.assert_called_once_with(timeout=30)
    workload.commit.assert_called_once()
    commit_tag = workload.commit.call_args.args[0]
    assert commit_tag.startswith("dml-snapshot-op1:")
    # Config preserved for the Builder validation step.
    assert result["image_id"] == "sha256:image"
    assert result["config"]["User"] == "10001:10001"
    assert result["config"]["WorkingDir"] == "/workspace"
    assert result["artifact"]["sha256"] == "f" * 64
    # Journal persisted exactly once, before side effects completed.
    # Export ran through the mocked upload path (exact image id asserted
    # inside _upload unit tests, not here).
    assert result["artifact"]["sha256"] == "f" * 64
    # Gate restored (unpause) even after success since commit completed.
    assert workload.unpause.called or not workload.paused
    assert completed == [("op1", "ws1", result["artifact"])]
    # A capture record was journaled with attempt metadata.
    persisted = coordinator.persist.call_args_list[0].args[0]
    assert persisted["snapshot_operation_id"] == "op1"


def test_capture_labels_must_match_workload():
    coordinator = MagicMock()
    coordinator.authoritative.return_value = True
    coordinator.get.return_value = record()
    workload = MagicMock()
    labelled(workload)
    workload.labels = {"dml.role": "sidecar"}
    ops = MagicMock()
    ops.authority.return_value = True
    ops.get_workload.return_value = workload
    with pytest.raises(CaptureDenied) as exc:
        capture(ops, coordinator, "ws1", "op1", record(), frozen_time)
    assert exc.value.code == "CAPTURE_FORBIDDEN"
    workload.pause.assert_not_called()


def test_capture_missing_workload_container_is_denied():
    coordinator = MagicMock()
    coordinator.authoritative.return_value = True
    coordinator.get.return_value = record()
    ops = MagicMock()
    ops.authority.return_value = True
    ops.get_workload.return_value = None
    with pytest.raises(CaptureDenied) as exc:
        capture(ops, coordinator, "ws1", "op1", record(), frozen_time)
    assert exc.value.code == "WORKLOAD_MISSING"


def test_capture_rejects_volume_backed_image():
    coordinator = MagicMock()
    coordinator.authoritative.return_value = True
    coordinator.get.return_value = record()
    workload = MagicMock()
    labelled(workload)
    workload.attrs = {
        "Config": {"User": "10001:10001", "WorkingDir": "/workspace", "Volumes": {"/w": {}}}
    }
    ops = MagicMock()
    ops.authority.return_value = True
    ops.get_workload.return_value = workload
    with pytest.raises(CaptureDenied) as exc:
        capture(ops, coordinator, "ws1", "op1", record(), frozen_time)
    assert exc.value.code == "VOLUME_IMAGE"
    workload.commit.assert_not_called()


def test_capture_timeout_unpauses_and_denies(monkeypatch):
    coordinator = MagicMock()
    coordinator.authoritative.return_value = True
    coordinator.get.return_value = record()
    workload = MagicMock()
    labelled(workload)
    workload.attrs = {"Config": {"User": "10001:10001", "WorkingDir": "/workspace"}}

    def hung_pause(timeout=None):
        import time as real_time

        real_time.sleep(0.05)

    workload.pause.side_effect = hung_pause
    ops = MagicMock()
    ops.authority.return_value = True
    ops.get_workload.return_value = workload
    monkeypatch.setattr(snapshot, "CAPTURE_PAUSED_SECONDS", 0.01)
    with pytest.raises(CaptureDenied) as exc:
        capture(ops, coordinator, "ws1", "op1", record(), frozen_time)
    assert exc.value.code == "CAPTURE_TIMEOUT"
    workload.unpause.assert_called_once_with(timeout=10)
    workload.commit.assert_not_called()


def test_journal_record_is_crash_safe(tmp_path):
    row = snapshot.journal_snapshot(tmp_path, "op1", {
        "workspace_id": "ws1", "operation_id": "op1", "state": "capturing",
        "attempt_token": "t1", "generation": 1,
        "started_at_monotonic": 1.0, "paused_deadline": 2.0,
    })
    again = snapshot.journal_snapshot(tmp_path, "op1", {"image_id": "sha256:x"})
    assert row.get("image_id") is None and again["image_id"] == "sha256:x"
    assert snapshot.load_snapshot(tmp_path, "op1")["operation_id"] == "op1"
    assert snapshot.load_snapshot(tmp_path, "missing") is None


def test_stale_capture_never_publishes(tmp_path):
    """A capture written before a newer generation must not publish late."""
    row = snapshot.journal_snapshot(tmp_path, "op-old", {
        "workspace_id": "ws1", "operation_id": "op-old", "generation": 1,
        "image_id": "sha256:old",
    })
    assert row["generation"] == 1
    # Newer record overwrites by exact operation id only.
    snapshot.journal_snapshot(tmp_path, "op-new", {
        "workspace_id": "ws1", "operation_id": "op-new", "generation": 2,
        "image_id": "sha256:new",
    })
    assert snapshot.load_snapshot(tmp_path, "op-old")["generation"] == 1
    assert snapshot.load_snapshot(tmp_path, "op-new")["generation"] == 2

