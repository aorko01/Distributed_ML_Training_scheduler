from unittest.mock import MagicMock, patch
import base64
import json
import pytest
from interactive.docker_ops import DockerOps, RuntimeFailure, canonical_registry_reference
from test.unit.test_execution_coordinator import assignment


def record():
    r = assignment()
    r.update(containers={})
    r["payload"] = {
        "image_digest_ref": "registry.example/test@sha256:" + "a" * 64,
        "runtime_id": "runtime",
        "workspace_id": "workspace",
        "revision_id": "revision",
        "owner_id": "owner",
        "generation": 1,
        "gpu_uuid": "GPU-assigned",
        "launch_spec": {
            "platform": "linux/amd64",
            "allow_root": False,
            "disk_gb": 20,
            "pull_headroom_gb": 40,
            "memory_gb": 8,
            "cpu": 2,
            "pids": 256,
        },
    }
    return r


def test_docker_hub_references_have_one_canonical_form():
    digest = "sha256:" + "a" * 64
    assert canonical_registry_reference("aorko123/workspace@" + digest) == (
        "docker.io/aorko123/workspace@" + digest
    )
    assert canonical_registry_reference("docker.io/aorko123/workspace@" + digest) == (
        "docker.io/aorko123/workspace@" + digest
    )
    assert canonical_registry_reference("registry.example/team/workspace@" + digest) == (
        "registry.example/team/workspace@" + digest
    )


def test_implicit_docker_hub_image_matches_explicit_allowlist_and_credential(monkeypatch, tmp_path):
    r = record()
    r["payload"]["image_digest_ref"] = "aorko123/workspace@sha256:" + "a" * 64
    credential = tmp_path / "registry.json"
    credential.write_text(json.dumps({"server": "docker.io", "username": "worker", "password": "token"}))
    credential.chmod(0o600)
    monkeypatch.setenv("INTERACTIVE_REGISTRY_PREFIXES", "docker.io/aorko123")
    monkeypatch.setenv("INTERACTIVE_REGISTRY_CREDENTIAL_FILE", str(credential))
    canonical = canonical_registry_reference(r["payload"]["image_digest_ref"])
    client = MagicMock()
    client.images.get.return_value.attrs = {
        # Docker may report either Docker Hub spelling in RepoDigests.
        "RepoDigests": [r["payload"]["image_digest_ref"]],
        "Os": "linux", "Architecture": "amd64",
        "Config": {"User": "1000", "WorkingDir": "/workspace"},
    }
    coordinator = MagicMock()
    coordinator.authoritative.return_value = True
    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0
    with patch("interactive.docker_ops.subprocess.Popen", return_value=proc) as launch, patch(
        "interactive.docker_ops.tempfile.TemporaryDirectory"
    ) as directory, patch("interactive.docker_ops.Path.mkdir"):
        directory.return_value.__enter__.return_value = str(tmp_path)
        DockerOps(coordinator, "worker", client).pull(r)
    assert canonical in launch.call_args.args[0]
    client.images.get.assert_called_once_with(canonical)


@pytest.mark.parametrize(
    "change",
    [
        {"RepoDigests": ["wrong@sha256:" + "b" * 64]},
        {"Architecture": "arm64"},
        {
            "Config": {
                "Volumes": {"/workspace": {}},
                "User": "1000",
                "WorkingDir": "/workspace",
            }
        },
        {"Config": {"User": "root", "WorkingDir": "/workspace"}},
    ],
)
def test_image_digest_platform_volumes_and_user_rejected(change, monkeypatch, tmp_path):
    r = record()
    coordinator = MagicMock()
    coordinator.authoritative.return_value = True
    client = MagicMock()
    attrs = {
        "RepoDigests": [r["payload"]["image_digest_ref"]],
        "Os": "linux",
        "Architecture": "amd64",
        "Config": {"User": "1000", "WorkingDir": "/workspace"},
    }
    client.images.get.return_value.attrs = {**attrs, **change}
    monkeypatch.setenv("INTERACTIVE_REGISTRY_PREFIXES", "registry.example")
    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0
    with patch("interactive.docker_ops.subprocess.Popen", return_value=proc), patch(
        "interactive.docker_ops.tempfile.TemporaryDirectory"
    ) as directory, patch("interactive.docker_ops.Path.mkdir"):
        directory.return_value.__enter__.return_value = str(tmp_path)
        with pytest.raises(RuntimeFailure) as exc:
            DockerOps(coordinator, "worker", client).pull(r)
        assert exc.value.code == "UNSUPPORTED_IMAGE"
    client.containers.create.assert_not_called()


def test_workload_has_only_selected_gpu_and_no_mount_or_credentials(monkeypatch):
    r = record()
    coordinator = MagicMock()
    coordinator.authoritative.return_value = True
    ops = DockerOps(coordinator, "worker", MagicMock())
    ops.create = MagicMock()
    inv = {
        "complete": True,
        "gpus": [{"uuid": "GPU-assigned", "busy": False, "processes": []}],
        "free_disk_gb": 200,
        "free_ram_gb": 64,
    }
    monkeypatch.setattr("hardware.execution_inventory", lambda *args, **kwargs: inv)
    ops.workload(r, "sha256:image", "1000", "/workspace")
    kwargs = ops.create.call_args.kwargs
    assert kwargs.get("network_mode") == "none" and kwargs["working_dir"] == "/workspace"
    assert kwargs["device_requests"][0]["DeviceIDs"] == ["GPU-assigned"]
    assert kwargs["cap_drop"] == ["ALL"] and kwargs["init"]
    assert (
        "volumes" not in kwargs
        and "environment" not in kwargs
        and "ports" not in kwargs
    )


@pytest.mark.parametrize(
    "flag, offline",
    [
        (None, True),
        ("0", True),
        ("", True),
        ("junk", True),
        ("1", False),
        ("true", False),
        ("YES", False),
        ("yes", False),
    ],
)
def test_workload_network_gate_honors_worker_environment_only(
    monkeypatch, flag, offline
):
    r = record()
    r["payload"]["launch_spec"]["allow_internet"] = True
    if flag is None:
        monkeypatch.delenv("INTERACTIVE_ALLOW_INTERNET", raising=False)
    else:
        monkeypatch.setenv("INTERACTIVE_ALLOW_INTERNET", flag)
    # The Scheduler hint is advisory: launch_spec.allow_internet=True alone must
    # never open egress; the Worker env gate decides.
    monkeypatch.delenv("INTERACTIVE_INTERNET_ENABLED", raising=False)
    coordinator = MagicMock()
    coordinator.authoritative.return_value = True
    ops = DockerOps(coordinator, "worker", MagicMock())
    ops.create = MagicMock()
    inv = {
        "complete": True,
        "gpus": [{"uuid": "GPU-assigned", "busy": False, "processes": []}],
        "free_disk_gb": 200,
        "free_ram_gb": 64,
    }
    monkeypatch.setattr("hardware.execution_inventory", lambda *args, **kwargs: inv)
    ops.workload(r, "sha256:image", "1000", "/workspace")
    kwargs = ops.create.call_args.kwargs
    if offline:
        assert kwargs.get("network_mode") == "none"
    else:
        # docker-py: network_mode=None means "unset" so the daemon default
        # bridge applies; the key may be present with a None value.
        assert kwargs.get("network_mode") is None
    assert kwargs["cap_drop"] == ["ALL"] and kwargs["init"]
    assert "volumes" not in kwargs and "ports" not in kwargs


def test_busy_gpu_fails_before_create(monkeypatch):
    r = record()
    ops = DockerOps(MagicMock(), "worker", MagicMock())
    ops.create = MagicMock()
    monkeypatch.setattr(
        "hardware.execution_inventory",
        lambda *args, **kwargs: {
            "complete": True,
            "gpus": [{"uuid": "GPU-assigned", "busy": True, "processes": [12]}],
        },
    )
    with pytest.raises(RuntimeFailure) as exc:
        ops.workload(r, "id", "1000", "/workspace")
    assert exc.value.code == "GPU_BUSY"
    ops.create.assert_not_called()


def test_private_pull_uses_protected_long_registry_token(monkeypatch, tmp_path):
    r = record()
    credential = tmp_path / "registry.json"
    password = "long-registry-token-" * 40
    credential.write_text(
        json.dumps(
            {
                "server": "registry.example",
                "username": "worker",
                "password": password,
            }
        )
    )
    credential.chmod(0o600)
    config_dir = tmp_path / "temporary-auth"
    config_dir.mkdir()
    monkeypatch.setenv("INTERACTIVE_REGISTRY_CREDENTIAL_FILE", str(credential))
    monkeypatch.setenv("INTERACTIVE_REGISTRY_PREFIXES", "registry.example")
    client = MagicMock()
    client.images.get.return_value.attrs = {
        "RepoDigests": [r["payload"]["image_digest_ref"]],
        "Os": "linux",
        "Architecture": "amd64",
        "Config": {"User": "1000", "WorkingDir": "/workspace"},
    }
    coordinator = MagicMock()
    coordinator.authoritative.return_value = True
    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0

    def launch(args, **kwargs):
        assert password not in repr(args) + repr(kwargs)
        config = json.loads((config_dir / "config.json").read_text())
        assert (
            base64.b64decode(config["auths"]["registry.example"]["auth"]).decode()
            == "worker:" + password
        )
        return proc

    with patch("interactive.docker_ops.subprocess.Popen", side_effect=launch), patch(
        "interactive.docker_ops.tempfile.TemporaryDirectory"
    ) as directory, patch("interactive.docker_ops.Path.mkdir"):
        directory.return_value.__enter__.return_value = str(config_dir)
        DockerOps(coordinator, "worker", client).pull(r)

    credential.chmod(0o644)
    with patch("interactive.docker_ops.subprocess.Popen") as launch, patch(
        "interactive.docker_ops.tempfile.TemporaryDirectory"
    ) as directory, patch("interactive.docker_ops.Path.mkdir"):
        directory.return_value.__enter__.return_value = str(config_dir)
        with pytest.raises(ValueError):
            DockerOps(coordinator, "worker", client).pull(r)
        launch.assert_not_called()


def _dev_record():
    r = record()
    r["payload"]["launch_spec"]["developer_mode"] = True
    r["payload"]["launch_spec"]["allow_internet"] = True
    return r


def _dev_attrs():
    r = record()
    return {
        "RepoDigests": [r["payload"]["image_digest_ref"]],
        "Os": "linux",
        "Architecture": "amd64",
        "Config": {
            "User": "10001:10001",
            "WorkingDir": "/workspace",
            "Labels": {"io.dml.developer-profile": "v1"},
        },
    }


def test_developer_pull_requires_gates_and_profile(monkeypatch, tmp_path):
    r = _dev_record()
    coordinator = MagicMock()
    coordinator.authoritative.return_value = True
    client = MagicMock()
    client.images.get.return_value.attrs = _dev_attrs()
    monkeypatch.setenv("INTERACTIVE_REGISTRY_PREFIXES", "registry.example")
    monkeypatch.setenv("INTERACTIVE_ALLOW_DEVELOPER_MODE", "1")
    monkeypatch.setenv("INTERACTIVE_ALLOW_INTERNET", "1")
    proc = MagicMock()
    proc.poll.return_value = 0
    proc.returncode = 0
    with patch("interactive.docker_ops.subprocess.Popen", return_value=proc), patch(
        "interactive.docker_ops.tempfile.TemporaryDirectory"
    ) as directory, patch("interactive.docker_ops.Path.mkdir"):
        directory.return_value.__enter__.return_value = str(tmp_path)
        _, user, workdir = DockerOps(coordinator, "worker", client).pull(r)
        assert (user, workdir) == ("10001:10001", "/workspace")
    # Missing local developer gate fails before workload start.
    monkeypatch.setenv("INTERACTIVE_ALLOW_DEVELOPER_MODE", "0")
    with patch("interactive.docker_ops.subprocess.Popen", return_value=proc), patch(
        "interactive.docker_ops.tempfile.TemporaryDirectory"
    ) as directory, patch("interactive.docker_ops.Path.mkdir"):
        directory.return_value.__enter__.return_value = str(tmp_path)
        with pytest.raises(RuntimeFailure) as exc:
            DockerOps(coordinator, "worker", client).pull(r)
        assert exc.value.code == "START_FAILED"
    # Missing image label is an image failure even with gates on.
    monkeypatch.setenv("INTERACTIVE_ALLOW_DEVELOPER_MODE", "1")
    bad = _dev_attrs()
    bad["Config"] = {"User": "10001:10001", "WorkingDir": "/workspace", "Labels": {}}
    client.images.get.return_value.attrs = bad
    with patch("interactive.docker_ops.subprocess.Popen", return_value=proc), patch(
        "interactive.docker_ops.tempfile.TemporaryDirectory"
    ) as directory, patch("interactive.docker_ops.Path.mkdir"):
        directory.return_value.__enter__.return_value = str(tmp_path)
        with pytest.raises(RuntimeFailure) as exc:
            DockerOps(coordinator, "worker", client).pull(r)
        assert exc.value.code == "UNSUPPORTED_IMAGE"


def test_workload_developer_omits_privilege_drops_but_keeps_limits(monkeypatch):
    r = _dev_record()
    coordinator = MagicMock()
    coordinator.authoritative.return_value = True
    ops = DockerOps(coordinator, "worker", MagicMock())
    ops.create = MagicMock()
    inv = {
        "complete": True,
        "gpus": [{"uuid": "GPU-assigned", "busy": False, "processes": []}],
        "free_disk_gb": 200,
        "free_ram_gb": 64,
    }
    monkeypatch.setattr("hardware.execution_inventory", lambda *args, **kwargs: inv)
    monkeypatch.setenv("INTERACTIVE_ALLOW_DEVELOPER_MODE", "1")
    monkeypatch.setenv("INTERACTIVE_ALLOW_INTERNET", "1")
    ops.workload(r, "sha256:image", "10001:10001", "/workspace")
    kwargs = ops.create.call_args.kwargs
    assert "cap_drop" not in kwargs and "security_opt" not in kwargs
    assert kwargs.get("network_mode") is None
    assert kwargs.get("privileged", False) is not True
    assert "volumes" not in kwargs and "ports" not in kwargs
    assert kwargs["device_requests"][0]["DeviceIDs"] == ["GPU-assigned"]
    # Strict mode keeps drops even when developer gate is on.
    ops.create.reset_mock()
    ops.workload(record(), "sha256:image", "1000", "/workspace")
    strict = ops.create.call_args.kwargs
    assert strict["cap_drop"] == ["ALL"]
    assert strict["security_opt"] == ["no-new-privileges:true"]
