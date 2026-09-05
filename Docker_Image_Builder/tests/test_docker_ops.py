"""Unit tests for Docker_Image_Builder/docker_ops.py — pure and mockable functions."""

import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from docker_ops import (  # noqa: E402
    _split_requirements,
    _write_dockerignore,
    _is_containerd_export_error,
    generate_dockerfile,
    resolve_interactive_base_image,
    generate_env_dockerfile,
    generate_access_dockerfile,
    generate_access_entrypoint,
    should_upload_build_line,
    maybe_upload_build_logs,
    emit_build_lines,
    _extract_build_log_lines,
    docker_login,
    delete_local_image,
    prune_old_base_images,
    _in_flight_base_images,
)


# ---------------------------------------------------------------------------
# _split_requirements
# ---------------------------------------------------------------------------
class TestSplitRequirements:
    def test_no_requirements_file(self, tmp_path):
        result = _split_requirements(str(tmp_path), str(tmp_path), 25)
        assert result == []

    def test_empty_requirements_file(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("")
        result = _split_requirements(str(tmp_path), str(tmp_path), 25)
        assert result == []

    def test_single_chunk(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("numpy\ntorch\n")
        result = _split_requirements(str(tmp_path), str(tmp_path), 25)
        assert result == ["requirements.txt"]

    def test_multiple_chunks(self, tmp_path):
        lines = "\n".join([f"pkg{i}" for i in range(60)])
        (tmp_path / "requirements.txt").write_text(lines)
        result = _split_requirements(str(tmp_path), str(tmp_path), 25)
        assert len(result) == 3
        assert all("requirements-part-" in r for r in result)

    def test_pip_options_skip_split(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("-r extra.txt\nnumpy\n")
        result = _split_requirements(str(tmp_path), str(tmp_path), 25)
        assert result == ["requirements.txt"]

    def test_reference_skip_split(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("-c constraints.txt\nnumpy\n")
        result = _split_requirements(str(tmp_path), str(tmp_path), 25)
        assert result == ["requirements.txt"]


# ---------------------------------------------------------------------------
# _write_dockerignore
# ---------------------------------------------------------------------------
class TestWriteDockerignore:
    def test_creates_file(self, tmp_path):
        _write_dockerignore(str(tmp_path))
        ignore_path = tmp_path / ".dockerignore"
        assert ignore_path.exists()
        content = ignore_path.read_text()
        assert ".git" in content
        assert "__pycache__" in content
        assert "*.pyc" in content


# ---------------------------------------------------------------------------
# _is_containerd_export_error
# ---------------------------------------------------------------------------
class TestIsContainerdExportError:
    def test_failed_to_export_layer(self):
        assert _is_containerd_export_error("failed to export layer") is True

    def test_creatediff(self):
        assert _is_containerd_export_error("Creatediff failed") is True

    def test_mount_callback_failed(self):
        assert _is_containerd_export_error("mount callback failed") is True

    def test_lstat_no_such_file(self):
        assert _is_containerd_export_error("lstat: no such file or directory") is True

    def test_lstat_snapshot(self):
        assert _is_containerd_export_error("lstat snapshot failed") is True

    def test_unrelated_error(self):
        assert _is_containerd_export_error("pip install failed") is False

    def test_empty_string(self):
        assert _is_containerd_export_error("") is False

    def test_parent_snapshot_does_not_exist(self):
        # Exact failure from the duplicate-build incident: concurrent builds
        # of the same tag corrupt the snapshotter; the loser fails here.
        assert _is_containerd_export_error(
            "NotFound: parent snapshot sha256:f90f078694f480d42f5283287a300608540df07e115a2addacceae800dff7b70 does not exist: not found"
        ) is True

    def test_snapshot_not_found(self):
        assert _is_containerd_export_error("snapshot sha256:abc not found") is True


# ---------------------------------------------------------------------------
# generate_dockerfile
# ---------------------------------------------------------------------------
class TestGenerateDockerfile:
    def test_basic_with_command(self, tmp_project_dir):
        result = generate_dockerfile(tmp_project_dir, "python train.py", "python:3.11")
        assert "FROM python:3.11" in result
        assert 'CMD python train.py' in result
        assert "WORKDIR /workspace" in result

    def test_basic_without_command(self, tmp_project_dir):
        result = generate_dockerfile(tmp_project_dir, "", "python:3.11")
        assert 'CMD ["python"]' in result

    def test_with_requirements(self, tmp_project_dir_with_requirements):
        result = generate_dockerfile(tmp_project_dir_with_requirements, "", "python:3.11")
        assert "COPY . /workspace/" in result
        assert "pip install --no-cache-dir -r requirements.txt" in result

    def test_with_chunked_requirements(self, tmp_path):
        # Create project with chunked req files
        project = tmp_path / "proj"
        project.mkdir()
        (project / "requirements-part-001.txt").write_text("numpy\n")
        (project / "requirements-part-002.txt").write_text("torch\n")
        result = generate_dockerfile(
            str(project), "", "python:3.11",
            requirement_files=["requirements-part-001.txt", "requirements-part-002.txt"],
        )
        assert "COPY requirements-part-001.txt requirements-part-002.txt /workspace/" in result
        assert "pip install --no-cache-dir -r requirements-part-001.txt" in result
        assert "pip install --no-cache-dir -r requirements-part-002.txt" in result


# ---------------------------------------------------------------------------
# resolve_interactive_base_image
# ---------------------------------------------------------------------------
class TestResolveInteractiveBaseImage:
    def test_explicit_base_image(self):
        assert resolve_interactive_base_image({"base_image": "myimage:latest"}) == "myimage:latest"

    def test_explicit_base_image_strips_whitespace(self):
        assert resolve_interactive_base_image({"base_image": "  myimage:v1  "}) == "myimage:v1"

    def test_pytorch_only(self):
        result = resolve_interactive_base_image({"pytorch_version": "2.1.0"})
        assert result == "pytorch/pytorch:2.1.0"

    def test_pytorch_with_cuda_short(self):
        result = resolve_interactive_base_image({"pytorch_version": "2.1.0", "cuda_version": "12.1"})
        assert result == "pytorch/pytorch:2.1.0-cuda12.1-cudnn9-runtime"

    def test_pytorch_with_cuda_full_variant(self):
        result = resolve_interactive_base_image({"pytorch_version": "2.1.0", "cuda_version": "12.1-cudnn9-devel"})
        assert result == "pytorch/pytorch:2.1.0-cuda12.1-cudnn9-devel"

    def test_python_fallback(self):
        result = resolve_interactive_base_image({"python_version": "3.10"})
        assert result == "python:3.10-slim"

    def test_empty_config(self):
        result = resolve_interactive_base_image({})
        assert result == "python:3.11-slim"

    def test_none_config(self):
        result = resolve_interactive_base_image(None)
        assert result == "python:3.11-slim"


# ---------------------------------------------------------------------------
# generate_env_dockerfile
# ---------------------------------------------------------------------------
class TestGenerateEnvDockerfile:
    def test_derived_no_project(self):
        result = generate_env_dockerfile("myimage:latest", with_project=False)
        assert "FROM myimage:latest" in result
        assert "USER root" in result
        assert "sleep" in result and "infinity" in result
        assert "COPY" not in result  # No COPY for derived builds

    def test_direct_with_project(self, tmp_project_dir):
        result = generate_env_dockerfile("python:3.11", with_project=True)
        assert "FROM python:3.11" in result
        assert "COPY . /workspace/" in result

    def test_direct_with_project_and_requirements(self, tmp_project_dir_with_requirements):
        result = generate_env_dockerfile("python:3.11", with_project=True)
        assert "pip install" in result

    def test_root_bash_profile_sources_bashrc_unconditionally(self):
        result = generate_env_dockerfile("python:3.11", with_project=False)
        assert ". \"$HOME/.bashrc\"" in result
        # The profile is baked unconditionally (not gated on `[ ! -f ... ]`).
        assert "RUN mkdir -p /root && printf" in result
        assert "if [ ! -f /root/.bash_profile ]" not in result

    def test_no_sudo_documented(self):
        result = generate_env_dockerfile("python:3.11", with_project=False)
        assert "no sudo is" in result


# ---------------------------------------------------------------------------
# generate_access_dockerfile / generate_access_entrypoint
# ---------------------------------------------------------------------------
class TestGenerateAccessArtifacts:
    def test_dockerfile_copies_enter_env_script(self):
        dockerfile = generate_access_dockerfile()
        assert "COPY enter-env.sh /usr/local/bin/enter-env.sh" in dockerfile
        assert "chmod +x /usr/local/bin/enter-env.sh" in dockerfile

    def test_dockerfile_notes_operator_build(self):
        dockerfile = generate_access_dockerfile()
        assert "AccessContainer" in dockerfile

    def test_entrypoint_forcecommand_uses_enter_env_wrapper(self):
        entrypoint = generate_access_entrypoint()
        assert "ForceCommand /usr/local/bin/enter-env.sh" in entrypoint
        # The old static bash -c ForceCommand must be gone.
        assert "ForceCommand nsenter -t 1 -m -u -i -n -p -- /bin/bash -c" not in entrypoint

    def test_entrypoint_reconstructs_env_container_environment(self):
        entrypoint = generate_access_entrypoint()
        assert "/proc/1/environ" in entrypoint
        assert "setuid" in entrypoint or "setuid" in entrypoint.lower()


# ---------------------------------------------------------------------------
# should_upload_build_line
# ---------------------------------------------------------------------------
class TestShouldUploadBuildLine:
    def test_empty_line(self):
        assert should_upload_build_line("") is False
        assert should_upload_build_line("  ") is False
        assert should_upload_build_line(None) is False

    def test_waiting_filtered(self):
        assert should_upload_build_line("Waiting for runner...") is False

    def test_push_filtered(self):
        assert should_upload_build_line("Pushing layer...") is False
        assert should_upload_build_line("pushing to registry") is False

    def test_pull_filtered(self):
        assert should_upload_build_line("Pulling from library/python") is False
        assert should_upload_build_line("Pull complete") is False

    def test_download_filtered(self):
        assert should_upload_build_line("Downloading...") is False
        assert should_upload_build_line("Download complete") is False

    def test_step_prefix_filtered(self):
        assert should_upload_build_line("#1 downloading") is False

    def test_normal_build_line_passes(self):
        assert should_upload_build_line("Step 1/5 : FROM python:3.11") is True
        assert should_upload_build_line("Successfully built abc123") is True


# ---------------------------------------------------------------------------
# maybe_upload_build_logs
# ---------------------------------------------------------------------------
class TestMaybeUploadBuildLogs:
    @patch("docker_ops.upload_build_logs")
    def test_empty_text_returns_same_time(self, mock_upload):
        result = maybe_upload_build_logs("job1", "", 100.0)
        assert result == 100.0
        mock_upload.assert_not_called()

    @patch("docker_ops.upload_build_logs")
    def test_force_upload(self, mock_upload):
        mock_upload.return_value = "key"
        result = maybe_upload_build_logs("job1", "log text", None, force=True)
        assert result is not None
        mock_upload.assert_called_once()

    @patch("docker_ops.upload_build_logs")
    @patch("time.monotonic", return_value=200.0)
    def test_throttled_within_60s(self, mock_time, mock_upload):
        mock_upload.return_value = "key"
        result = maybe_upload_build_logs("job1", "log text", 150.0, force=False)
        assert result == 150.0  # Not updated
        mock_upload.assert_not_called()

    @patch("docker_ops.upload_build_logs")
    @patch("time.monotonic", return_value=250.0)
    def test_upload_after_60s(self, mock_time, mock_upload):
        mock_upload.return_value = "key"
        result = maybe_upload_build_logs("job1", "log text", 150.0, force=False)
        assert result == 250.0
        mock_upload.assert_called_once()


# ---------------------------------------------------------------------------
# emit_build_lines
# ---------------------------------------------------------------------------
class TestEmitBuildLines:
    @patch("docker_ops.send_log_lines")
    def test_appends_to_buffer_and_sends(self, mock_send):
        buffer = []
        emit_build_lines("job1", buffer, ["line1", "line2"])
        assert buffer == ["line1", "line2"]
        mock_send.assert_called_once_with("job1", ["line1", "line2"])

    @patch("docker_ops.send_log_lines")
    def test_splits_multiline(self, mock_send):
        buffer = []
        emit_build_lines("job1", buffer, ["line1\nline2\nline3"])
        assert buffer == ["line1", "line2", "line3"]
        mock_send.assert_called_once_with("job1", ["line1", "line2", "line3"])

    @patch("docker_ops.send_log_lines")
    def test_empty_lines_noop(self, mock_send):
        buffer = []
        emit_build_lines("job1", buffer, [])
        assert buffer == []
        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# _extract_build_log_lines
# ---------------------------------------------------------------------------
class TestExtractBuildLogLines:
    def test_empty_build_log(self):
        error = MagicMock()
        error.build_log = []
        assert _extract_build_log_lines(error) == []

    def test_no_build_log_attribute(self):
        error = MagicMock(spec=[])  # No build_log attribute
        assert _extract_build_log_lines(error) == []

    def test_extracts_stream_lines(self):
        error = MagicMock()
        error.build_log = [
            {"stream": "Step 1/3 : FROM python:3.11\n"},
            {"stream": "Step 2/3 : RUN pip install numpy\n"},
        ]
        result = _extract_build_log_lines(error)
        assert "Step 1/3 : FROM python:3.11" in result
        assert "Step 2/3 : RUN pip install numpy" in result

    def test_deduplicates_consecutive(self):
        error = MagicMock()
        error.build_log = [
            {"stream": "error line\n"},
            {"stream": "error line\n"},
        ]
        result = _extract_build_log_lines(error)
        assert result.count("error line") == 1

    def test_extracts_error_field(self):
        error = MagicMock()
        error.build_log = [{"error": "something went wrong"}]
        result = _extract_build_log_lines(error)
        assert "something went wrong" in result


# ---------------------------------------------------------------------------
# docker_login
# ---------------------------------------------------------------------------
class TestDockerLogin:
    def setup_method(self):
        import docker_ops
        docker_ops._docker_authenticated = False

    @patch("docker_ops.DOCKER_HUB_PASSWORD", "testpass")
    @patch("docker_ops.DOCKER_HUB_USERNAME", "testuser")
    def test_login_success(self, mock_docker_client):
        docker_login(mock_docker_client)
        mock_docker_client.login.assert_called_once_with(username="testuser", password="testpass")

    @patch("docker_ops.DOCKER_HUB_PASSWORD", "testpass")
    @patch("docker_ops.DOCKER_HUB_USERNAME", "testuser")
    def test_login_skipped_when_already_authenticated(self, mock_docker_client):
        import docker_ops
        docker_ops._docker_authenticated = True
        docker_login(mock_docker_client)
        mock_docker_client.login.assert_not_called()

    @patch("docker_ops.DOCKER_HUB_PASSWORD", "")
    def test_login_no_password_assumes_logged_in(self, mock_docker_client):
        docker_login(mock_docker_client)
        mock_docker_client.login.assert_not_called()

    @patch("docker_ops.DOCKER_HUB_PASSWORD", "testpass")
    @patch("docker_ops.DOCKER_HUB_USERNAME", "testuser")
    def test_login_failure_does_not_set_flag(self, mock_docker_client):
        mock_docker_client.login.side_effect = Exception("auth failed")
        docker_login(mock_docker_client)
        import docker_ops
        assert docker_ops._docker_authenticated is False


# ---------------------------------------------------------------------------
# delete_local_image
# ---------------------------------------------------------------------------
class TestDeleteLocalImage:
    def test_successful_delete(self, mock_docker_client):
        delete_local_image(mock_docker_client, "job1", "user/job1:latest")
        mock_docker_client.images.remove.assert_called_once_with(image="user/job1:latest", force=True)

    def test_delete_failure_logs_warning(self, mock_docker_client):
        mock_docker_client.images.remove.side_effect = Exception("not found")
        # Should not raise
        delete_local_image(mock_docker_client, "job1", "user/job1:latest")


# ---------------------------------------------------------------------------
# prune_old_base_images
# ---------------------------------------------------------------------------
class TestPruneOldBaseImages:
    @patch("docker_ops.remove_base_image_record")
    @patch("docker_ops.get_old_base_images", return_value=["old-image:latest"])
    def test_prune_removes_image(self, mock_get_old, mock_remove, mock_docker_client):
        prune_old_base_images(mock_docker_client)
        mock_docker_client.images.remove.assert_called_once_with(image="old-image:latest", force=True)
        mock_remove.assert_called_once_with("old-image:latest")

    @patch("docker_ops.remove_base_image_record")
    @patch("docker_ops.get_old_base_images", return_value=["old-image:latest"])
    def test_prune_image_not_found_cleans_record(self, mock_get_old, mock_remove, mock_docker_client):
        import docker.errors
        mock_docker_client.images.remove.side_effect = docker.errors.ImageNotFound("not found")
        prune_old_base_images(mock_docker_client)
        mock_remove.assert_called_once_with("old-image:latest")

    @patch("docker_ops.remove_base_image_record")
    @patch("docker_ops.get_old_base_images", return_value=[])
    def test_prune_no_old_images(self, mock_get_old, mock_remove, mock_docker_client):
        prune_old_base_images(mock_docker_client)
        mock_docker_client.images.remove.assert_not_called()
        mock_remove.assert_not_called()

    @patch("docker_ops.remove_base_image_record")
    @patch("docker_ops.get_old_base_images", return_value=["old-image:latest", "inflight-image:latest"])
    def test_prune_skips_in_flight_images(self, mock_get_old, mock_remove, mock_docker_client):
        """Prune should skip images currently being built (in-flight)."""
        _in_flight_base_images.add("inflight-image:latest")
        try:
            prune_old_base_images(mock_docker_client)
            # Only the non-in-flight image should have been removed
            mock_docker_client.images.remove.assert_called_once_with(image="old-image:latest", force=True)
            mock_remove.assert_called_once_with("old-image:latest")
        finally:
            _in_flight_base_images.discard("inflight-image:latest")


# ---------------------------------------------------------------------------
# Per-tag build serialization (duplicate-build guard)
# ---------------------------------------------------------------------------
class TestPerTagBuildSerialization:
    def test_same_tag_builds_serialize(self, mock_docker_client):
        """Two concurrent build_image calls for the same job tag must not run
        the underlying docker build concurrently (which corrupts the daemon).
        The second waits for the first instead of interleaving layers."""
        import threading
        import time
        import docker_ops
        from docker_ops import build_image, _tag_lock

        entered = []
        release_first = threading.Event()

        real_uncached = docker_ops._build_image_uncached

        def slow_uncached(*args, **kwargs):
            entered.append(time.monotonic())
            if len(entered) == 1:
                # First build holds the lock; let the second start and block.
                time.sleep(0.2)
            return "testuser/dup-job-env:latest"

        with patch.object(docker_ops, "_build_image_uncached", side_effect=slow_uncached):
            t1 = threading.Thread(
                target=lambda: build_image(
                    mock_docker_client, "dup-job", "/tmp", "", "python:3.11",
                    build_type="interactive", include_project=False,
                )
            )
            t2 = threading.Thread(
                target=lambda: build_image(
                    mock_docker_client, "dup-job", "/tmp", "", "python:3.11",
                    build_type="interactive", include_project=False,
                )
            )
            t1.start()
            time.sleep(0.05)  # Ensure t1 acquires the tag lock first.
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)
            assert not t1.is_alive()
            assert not t2.is_alive()

        assert len(entered) == 2
        # Serialized: second entry happens after first had time to hold lock.
        assert entered[1] - entered[0] >= 0.1

    def test_different_tags_build_concurrently(self):
        """Different job tags use different locks and must not block each other."""
        from docker_ops import _tag_lock
        assert _tag_lock("testuser/a-env:latest") is not _tag_lock("testuser/b-env:latest")
        assert _tag_lock("testuser/a-env:latest") is _tag_lock("testuser/a-env:latest")

    def test_delete_skipped_while_build_in_progress(self, mock_docker_client):
        """delete_local_image must skip when the same tag is being built —
        deleting mid-build causes 'parent snapshot does not exist'."""
        from docker_ops import _tag_lock, delete_local_image

        tag = "testuser/dup-job-env:latest"
        lock = _tag_lock(tag)
        lock.acquire()  # Simulate an in-progress build holding the lock.
        try:
            delete_local_image(mock_docker_client, "dup-job", tag)
            mock_docker_client.images.remove.assert_not_called()
        finally:
            lock.release()

    def test_delete_proceeds_when_no_build_in_progress(self, mock_docker_client):
        """Normal path: delete removes the image when no build holds the lock."""
        from docker_ops import delete_local_image
        delete_local_image(mock_docker_client, "job1", "testuser/job1:latest")
        mock_docker_client.images.remove.assert_called_once_with(
            image="testuser/job1:latest", force=True
        )
