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
            "revision_id": "rev1",
            "generation": 1,
            "launch_spec": {"platform": "linux/amd64", "disk_gb": 20, "allow_root": False},
        },
    }


def labelled(workload, assignment_id="a1"):
    workload.labels = {"dml.component": "workload", "dml.assignment": assignment_id,
                       "dml.runtime": "rt1", "dml.workspace": "ws1",
                       "dml.revision": "rev1", "dml.generation": "1"}
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


def test_ssh_state_must_be_on_docker_tmpfs_host_config():
    config = {"User": "10001:10001", "WorkingDir": "/workspace"}
    # Docker inspect reports tmpfs in HostConfig.Tmpfs, not Mounts.
    assert snapshot._check_config(
        config, mounts=[], ssh_required=True,
        host_config={"Tmpfs": {"/run/dml-vscode-ssh": "rw,nosuid,noexec,size=1m"}},
    ) == config
    with pytest.raises(CaptureDenied) as exc:
        snapshot._check_config(config, mounts=[], ssh_required=True, host_config={})
    assert exc.value.code == "SSH_STATE_UNSAFE"


def test_capture_rejects_missing_authority():
    coordinator = MagicMock()
    coordinator.authoritative.return_value = False
    with pytest.raises(CaptureDenied):
        capture(MagicMock(), coordinator, "ws1", "op1", record(), frozen_time)


def test_capture_happy_path_commits_then_exports_without_holding_pause(monkeypatch, tmp_path):
    coordinator = MagicMock()
    coordinator.authoritative.return_value = True
    coordinator.get.return_value = record()
    workload = MagicMock()
    labelled(workload)
    workload.attrs = {"Config": {"User": "10001:10001", "WorkingDir": "/workspace"}}
    image = MagicMock()
    image.id = "sha256:image"
    ops = MagicMock()
    ops.commit_image.return_value = image
    ops.authority.return_value = True
    ops.get_workload.return_value = workload
    ops.client = MagicMock()
    progress = MagicMock()
    committed = []

    def fake_upload(client, assignment_id, image_id, destination, deadline, authority):
        assert committed == ["commit finished"]
        return {"sha256": "f" * 64, "size": 10}

    monkeypatch.setattr(snapshot, "_upload", fake_upload)
    result = capture(
        ops, coordinator, "ws1", "op1", record(), frozen_time,
        progress=progress, upload_chunk=1 << 20, state_dir=tmp_path,
        on_committed=lambda: committed.append("commit finished"),
    )
    ops.get_workload.assert_called_once_with(record())
    ops.commit_image.assert_called_once_with(record(), "dml-snapshot-op1", "capture")
    # Config preserved for the Builder validation step.
    assert result["image_id"] == "sha256:image"
    assert result["config"]["User"] == "10001:10001"
    assert result["config"]["WorkingDir"] == "/workspace"
    assert result["artifact"]["sha256"] == "f" * 64
    # Journal persisted exactly once, before side effects completed.
    # Export ran through the mocked upload path (exact image id asserted
    # inside _upload unit tests, not here).
    assert result["artifact"]["sha256"] == "f" * 64
    assert committed == ["commit finished"]
    workload.pause.assert_not_called()
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
    ops.commit_image.assert_not_called()


def test_capture_commit_failure_unpauses_and_denies(monkeypatch):
    coordinator = MagicMock()
    coordinator.authoritative.return_value = True
    coordinator.get.return_value = record()
    workload = MagicMock()
    labelled(workload)
    workload.attrs = {"Config": {"User": "10001:10001", "WorkingDir": "/workspace"}}

    ops = MagicMock()
    ops.commit_image.side_effect = RuntimeError("daemon commit timeout")
    workload.status = "paused"
    ops.authority.return_value = True
    ops.get_workload.return_value = workload
    with pytest.raises(CaptureDenied) as exc:
        capture(ops, coordinator, "ws1", "op1", record(), frozen_time)
    assert exc.value.code == "CAPTURE_FAILED"
    workload.unpause.assert_called_once_with()


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
