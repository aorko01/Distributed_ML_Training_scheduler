"""Unit tests for docker_ops.py."""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import docker.errors
import pytest

import docker_ops


@pytest.fixture()
def no_network(monkeypatch):
    """Neutralise all network / DB side effects of a build."""
    monkeypatch.setattr(docker_ops, "update_base_image_usage", lambda *a, **k: None)
    monkeypatch.setattr(docker_ops, "emit_build_lines", lambda *a, **k: None)
    monkeypatch.setattr(docker_ops, "maybe_upload_build_logs", lambda *a, **k: 0.0)
    monkeypatch.setattr(docker_ops, "save_debug_copy", lambda *a, **k: None)
    monkeypatch.setattr(docker_ops, "DEBUG_SAVE_LOCAL", False)


def _mock_client(build_result=None, build_error=None, push_chunks=None):
    client = MagicMock()
    if build_error is not None:
        client.images.build.side_effect = build_error
    else:
        client.images.build.return_value = (MagicMock(), build_result or [])
    client.images.push.return_value = iter(push_chunks or [])
    return client


class TestDockerLogin:
    def test_login_when_password_set(self):
        client = MagicMock()
        with patch.object(docker_ops, "DOCKER_HUB_PASSWORD", "secret"), patch.object(
            docker_ops, "DOCKER_HUB_USERNAME", "user"
        ):
            docker_ops.docker_login(client)
        client.login.assert_called_once_with(username="user", password="secret")

    def test_no_login_without_password(self):
        client = MagicMock()
        with patch.object(docker_ops, "DOCKER_HUB_PASSWORD", ""):
            docker_ops.docker_login(client)
        client.login.assert_not_called()


class TestGenerateDockerfile:
    def test_with_requirements_and_command(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("torch")
        out = docker_ops.generate_dockerfile(str(tmp_path), "python train.py", "base:1")
        assert "FROM base:1" in out
        assert "RUN pip install --no-cache-dir -r requirements.txt" in out
        assert "CMD python train.py" in out
        assert "WORKDIR /workspace" in out

    def test_without_requirements_default_cmd(self, tmp_path):
        out = docker_ops.generate_dockerfile(str(tmp_path), "", "base:1")
        assert "pip install" not in out
        assert 'CMD ["python"]' in out

    def test_packages_text_takes_precedence_over_requirements(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("torch==1.0")
        out = docker_ops.generate_dockerfile(
            str(tmp_path), "python train.py", "base:1", "numpy pandas==2.0.3"
        )
        assert "RUN pip install --no-cache-dir 'numpy' 'pandas==2.0.3'" in out
        assert "requirements.txt" not in out

    def test_packages_accepts_commas_and_lists(self, tmp_path):
        out = docker_ops.generate_dockerfile(
            str(tmp_path), "", "base:1", "numpy, pandas scikit-learn"
        )
        assert "'numpy' 'pandas' 'scikit-learn'" in out
        out_list = docker_ops.generate_dockerfile(
            str(tmp_path), "", "base:1", ["torchmetrics"]
        )
        assert "'torchmetrics'" in out_list


class TestParsePackagesText:
    def test_splits_lines_commas_and_spaces(self):
        assert docker_ops.parse_packages_text("numpy\npandas==2.0.3") == ["numpy", "pandas==2.0.3"]
        assert docker_ops.parse_packages_text("numpy, pandas scikit-learn") == [
            "numpy", "pandas", "scikit-learn",
        ]

    def test_empty_and_none(self):
        assert docker_ops.parse_packages_text("") == []
        assert docker_ops.parse_packages_text(None) == []


class TestSaveDebugCopy:
    def test_copies_tree(self, tmp_path):
        src = tmp_path / "build"
        src.mkdir()
        (src / "a.txt").write_text("hi")
        with patch.object(docker_ops, "DEBUG_LOCAL_DIR", str(tmp_path / "debug")):
            docker_ops.save_debug_copy("j1", str(src))
        assert (tmp_path / "debug" / "j1" / "a.txt").read_text() == "hi"

    def test_overwrites_existing(self, tmp_path):
        src = tmp_path / "build"
        src.mkdir()
        (src / "a.txt").write_text("new")
        debug_root = tmp_path / "debug"
        (debug_root / "j1").mkdir(parents=True)
        (debug_root / "j1" / "stale.txt").write_text("stale")
        with patch.object(docker_ops, "DEBUG_LOCAL_DIR", str(debug_root)):
            docker_ops.save_debug_copy("j1", str(src))
        assert not (debug_root / "j1" / "stale.txt").exists()

    def test_missing_src_does_not_raise(self, tmp_path):
        with patch.object(docker_ops, "DEBUG_LOCAL_DIR", str(tmp_path / "debug")):
            docker_ops.save_debug_copy("j1", str(tmp_path / "nope"))  # no raise


class TestUploadBuildLogs:
    def test_success_posts_multipart(self):
        resp = MagicMock()
        with patch.object(docker_ops.requests, "post", return_value=resp) as mock_post:
            key = docker_ops.upload_build_logs("j1", "line1\nline2")
        assert key == "j1/build.log"
        _, kwargs = mock_post.call_args
        assert kwargs["data"]["object_key"] == "j1/build.log"
        resp.raise_for_status.assert_called_once()

    def test_failure_still_returns_key(self):
        # Fixed: upload failure must return None so callers don't advance the
        # throttle timestamp and pretend logs were persisted.
        with patch.object(
            docker_ops.requests, "post", side_effect=Exception("down")
        ):
            assert docker_ops.upload_build_logs("j1", "logs") is None

    def test_bucket_fallback(self):
        resp = MagicMock()
        with (
            patch.object(docker_ops, "OBJECT_OUTPUT_BUCKET", ""),
            patch.object(docker_ops.requests, "post", return_value=resp) as mock_post,
        ):
            docker_ops.upload_build_logs("j1", "logs")
        assert mock_post.call_args[1]["data"]["bucket"] == docker_ops.OBJECT_STORE_BUCKET


class TestShouldUploadBuildLine:
    @pytest.mark.parametrize(
        "line",
        ["", "   ", "Pulling from library/python", "Downloading layer",
         "Extracting", "Digest: sha256:abc", "Pull complete",
         "Download complete", "Downloaded newer image", "Verifying Checksum",
         "waiting", "Waiting for connection", "Pushing layer", "Pushed",
         "#5 pulling fs layer"],
    )
    def test_noise_filtered(self, line):
        assert docker_ops.should_upload_build_line(line) is False

    @pytest.mark.parametrize(
        "line",
        ["Step 1/5 : FROM python:3.11", "Successfully built abc123",
         "RUN pip install torch", "#5 [stage] Step 2/5 : RUN pip install x"],
    )
    def test_real_output_kept(self, line):
        assert docker_ops.should_upload_build_line(line) is True


class TestMaybeUploadBuildLogs:
    def test_empty_text_returns_last_unchanged(self):
        with patch.object(docker_ops, "upload_build_logs") as mock_upload:
            assert docker_ops.maybe_upload_build_logs("j", "", 5.0) == 5.0
            mock_upload.assert_not_called()

    def test_first_upload_runs(self):
        with (
            patch.object(docker_ops, "upload_build_logs") as mock_upload,
            patch.object(docker_ops.time, "monotonic", return_value=100.0),
        ):
            assert docker_ops.maybe_upload_build_logs("j", "logs", None) == 100.0
            mock_upload.assert_called_once()

    def test_throttled_within_window(self):
        with (
            patch.object(docker_ops, "upload_build_logs") as mock_upload,
            patch.object(docker_ops.time, "monotonic", return_value=110.0),
        ):
            assert docker_ops.maybe_upload_build_logs("j", "logs", 100.0) == 100.0
            mock_upload.assert_not_called()

    def test_force_bypasses_throttle(self):
        with (
            patch.object(docker_ops, "upload_build_logs") as mock_upload,
            patch.object(docker_ops.time, "monotonic", return_value=110.0),
        ):
            assert docker_ops.maybe_upload_build_logs("j", "logs", 100.0, force=True) == 110.0
            mock_upload.assert_called_once()


class TestEmitBuildLines:
    def test_splits_embedded_newlines(self):
        buf = []
        with patch.object(docker_ops, "send_log_lines") as mock_send:
            docker_ops.emit_build_lines("j", buf, ["a\nb\nc"])
        assert buf == ["a", "b", "c"]
        mock_send.assert_called_once_with("j", ["a", "b", "c"])

    def test_empty_is_noop(self):
        buf = []
        with patch.object(docker_ops, "send_log_lines") as mock_send:
            docker_ops.emit_build_lines("j", buf, [])
        assert buf == []
        mock_send.assert_not_called()

    def test_non_strings_coerced(self):
        buf = []
        with patch.object(docker_ops, "send_log_lines"):
            docker_ops.emit_build_lines("j", buf, [123])
        assert buf == ["123"]


class TestExtractBuildLogLines:
    def _err(self, build_log):
        return docker.errors.BuildError("failed", build_log=build_log)

    def test_extracts_streams_errors_statuses(self):
        err = self._err([
            {"stream": "Step 1\n"},
            {"error": "pip failed\r\nconflict"},
            {"status": "Downloading"},
            "not-a-dict",
            {},
        ])
        lines = docker_ops._extract_build_log_lines(err)
        assert "Step 1" in lines
        assert "pip failed" in lines
        assert "conflict" in lines

    def test_dedupes_consecutive(self):
        err = self._err([{"stream": "a\na\nb"}])
        assert docker_ops._extract_build_log_lines(err) == ["a", "b"]

    def test_missing_build_log(self):
        err = docker.errors.BuildError("failed", None)
        assert docker_ops._extract_build_log_lines(err) == []


class TestBuildPushAndClean:
    def test_training_success(self, tmp_path, no_network):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "train.py").write_text("print(1)")
        client = _mock_client(
            build_result=[{"stream": "Step 1 done\n"}],
            push_chunks=[{"status": "Pushed", "progress": "100%"}],
        )
        out = docker_ops.build_push_and_clean(
            client, "job1", str(proj), "python train.py", "base:1"
        )
        assert out is None
        client.images.build.assert_called_once()
        assert client.images.build.call_args[1]["tag"].endswith("job1:latest")
        client.images.remove.assert_called_once()

    def test_training_build_error_is_user_failure(self, tmp_path, no_network):
        proj = tmp_path / "proj"
        proj.mkdir()
        err = docker.errors.BuildError("boom", build_log=[{"error": "pip failed"}])
        client = _mock_client(build_error=err)
        out = docker_ops.build_push_and_clean(
            client, "job1", str(proj), "python train.py", "base:1"
        )
        assert out[0] == "user"
        assert "Build failed" in out[1]

    def test_push_error_is_system_failure(self, tmp_path, no_network):
        proj = tmp_path / "proj"
        proj.mkdir()
        client = _mock_client(
            build_result=[], push_chunks=[{"error": "denied"}]
        )
        out = docker_ops.build_push_and_clean(
            client, "job1", str(proj), "python train.py", "base:1"
        )
        assert out[0] == "system"
        assert "denied" in out[1]

    def test_failed_relogin_returns_system_failure_without_crashing(
        self, tmp_path, no_network
    ):
        proj = tmp_path / "proj-login"
        proj.mkdir()
        client = _mock_client(
            build_result=[],
            push_chunks=[{"error": "unauthorized: authentication required"}],
        )
        with patch.object(docker_ops, "docker_login", return_value=False):
            out = docker_ops.build_push_and_clean(
                client, "job1", str(proj), "python train.py", "base:1"
            )
        assert out[0] == "system"
        assert "re-login" in out[1]

    def test_image_not_found(self, tmp_path, no_network):
        proj = tmp_path / "proj"
        proj.mkdir()
        client = _mock_client(
            build_error=docker.errors.ImageNotFound("no such base")
        )
        out = docker_ops.build_push_and_clean(
            client, "job1", str(proj), "cmd", "missing:1"
        )
        assert out[0] == "system"
        assert "Base image unavailable" in out[1]

    def test_api_error(self, tmp_path, no_network):
        proj = tmp_path / "proj"
        proj.mkdir()
        client = _mock_client(
            build_error=docker.errors.APIError("daemon down")
        )
        out = docker_ops.build_push_and_clean(
            client, "job1", str(proj), "cmd", "base:1"
        )
        assert out[0] == "system"
        assert "Docker API error" in out[1]

    def test_cleans_build_dir_on_failure(self, tmp_path, no_network):
        proj = tmp_path / "proj"
        proj.mkdir()
        client = _mock_client(
            build_error=docker.errors.BuildError("x", build_log=[])
        )
        created = []
        real_mkdtemp = docker_ops.tempfile.mkdtemp

        def _recording_mkdtemp(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs)
            created.append(path)
            return path

        with patch.object(
            docker_ops.tempfile, "mkdtemp", side_effect=_recording_mkdtemp
        ):
            docker_ops.build_push_and_clean(client, "jobX", str(proj), "c", "b")
        assert len(created) == 1
        assert not os.path.exists(created[0])

    def test_scheduler_attempt_uses_unique_tag_and_cancellable_build(
        self, tmp_path, no_network
    ):
        proj = tmp_path / "proj-attempt"
        proj.mkdir()
        client = _mock_client(push_chunks=[])
        with patch.object(
            docker_ops,
            "_run_cancellable_docker_build",
            return_value=(0, ["done"], False),
        ) as run_build:
            result = docker_ops.build_push_and_clean(
                client,
                "job1",
                str(proj),
                "python train.py",
                "base:1",
                build_attempt_id="attempt-1",
                should_cancel=lambda: False,
            )
        assert result is None
        image_tag = docker_ops.image_tag_for_attempt("job1", "attempt-1")
        assert run_build.call_args.args[1] == image_tag
        assert client.images.push.call_args.kwargs["tag"] == "build-attempt-1"
        client.images.build.assert_not_called()

    def test_cancelled_attempt_is_not_pushed(self, tmp_path, no_network):
        proj = tmp_path / "proj-cancel"
        proj.mkdir()
        client = _mock_client()
        with patch.object(
            docker_ops,
            "_run_cancellable_docker_build",
            return_value=(-15, [], True),
        ):
            result = docker_ops.build_push_and_clean(
                client,
                "job1",
                str(proj),
                "python train.py",
                "base:1",
                build_attempt_id="attempt-1",
                should_cancel=lambda: False,
            )
        assert result[0] == "cancelled"
        client.images.push.assert_not_called()


class TestPruneOldBaseImages:
    def test_empty_list_does_nothing(self):
        client = MagicMock()
        with patch.object(docker_ops, "get_old_base_images", return_value=[]):
            docker_ops.prune_old_base_images(client)
        client.images.remove.assert_not_called()

    def test_success_removes_record(self):
        client = MagicMock()
        with (
            patch.object(docker_ops, "get_old_base_images", return_value=["img:old"]),
            patch.object(docker_ops, "remove_base_image_record") as mock_rm,
        ):
            docker_ops.prune_old_base_images(client)
        client.images.remove.assert_called_once_with(image="img:old", force=True)
        mock_rm.assert_called_once_with("img:old")

    def test_image_not_found_still_removes_record(self):
        client = MagicMock()
        client.images.remove.side_effect = docker.errors.ImageNotFound("gone")
        with (
            patch.object(docker_ops, "get_old_base_images", return_value=["img:gone"]),
            patch.object(docker_ops, "remove_base_image_record") as mock_rm,
        ):
            docker_ops.prune_old_base_images(client)
        mock_rm.assert_called_once_with("img:gone")

    def test_generic_error_keeps_record(self):
        client = MagicMock()
        client.images.remove.side_effect = Exception("in use")
        with (
            patch.object(docker_ops, "get_old_base_images", return_value=["img:busy"]),
            patch.object(docker_ops, "remove_base_image_record") as mock_rm,
        ):
            docker_ops.prune_old_base_images(client)  # no raise
        mock_rm.assert_not_called()

    def test_prune_waits_for_build_using_same_base(self):
        client = MagicMock()
        build_lock = docker_ops._get_build_lock("img:busy")
        build_lock.acquire()
        try:
            with patch.object(
                docker_ops, "get_old_base_images", return_value=["img:busy"]
            ), patch.object(docker_ops, "remove_base_image_record"):
                thread = threading.Thread(
                    target=docker_ops.prune_old_base_images, args=(client,)
                )
                thread.start()
                time.sleep(0.05)
                client.images.remove.assert_not_called()
        finally:
            build_lock.release()
        thread.join(timeout=1)
        client.images.remove.assert_called_once_with(image="img:busy", force=True)


class TestTransientBuildErrorDetection:
    @pytest.mark.parametrize(
        "msg",
        [
            "failed to export image: No such image: sha256:abc",
            "no such image: sha256:7e2c08b3e4d4",
            "connection reset by peer",
            "tls handshake timeout",
            "unexpected eof",
        ],
    )
    def test_transient_patterns(self, msg):
        assert docker_ops._is_transient_build_error(msg) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "Step 4/5 : RUN pip install torch\nCould not find a version",
            "permission denied while trying to connect",
            "build failed",
        ],
    )
    def test_non_transient(self, msg):
        assert docker_ops._is_transient_build_error(msg) is False


class TestBuildErrorClassification:
    def _proj(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "train.py").write_text("print(1)")
        return proj

    def test_transient_export_error_is_system(self, tmp_path, no_network):
        proj = self._proj(tmp_path)
        err = docker.errors.BuildError(
            "Step 3/5: COPY failed to export image: No such image: sha256:abc",
            build_log=[{"errorDetail": {"message": "failed to export image: No such image"}}],
        )
        client = _mock_client(build_error=err)
        out = docker_ops.build_push_and_clean(
            client, "job1", str(proj), "python train.py", "base:1"
        )
        assert out[0] == "system"
        assert "infra race" in out[1]

    def test_real_dockerfile_error_is_user(self, tmp_path, no_network):
        proj = self._proj(tmp_path)
        err = docker.errors.BuildError("boom", build_log=[{"error": "pip failed"}])
        client = _mock_client(build_error=err)
        out = docker_ops.build_push_and_clean(
            client, "job1", str(proj), "python train.py", "base:1"
        )
        assert out[0] == "user"


class TestBuildLock:
    def _proj(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "train.py").write_text("print(1)")
        return proj

    def _counting_client(self):
        client = MagicMock()
        state = {"max": 0, "current": 0, "lock": threading.Lock()}

        def _build_blocker(*args, **kwargs):
            with state["lock"]:
                state["current"] += 1
                state["max"] = max(state["max"], state["current"])
                time.sleep(0.05)
                state["current"] -= 1
            return (MagicMock(), [{"stream": "done\n"}])

        client.images.build.side_effect = _build_blocker
        client.images.push.return_value = iter([])
        return client, state

    def test_different_base_images_get_distinct_locks(self):
        base_locks = [docker_ops._get_build_lock(f"base{i}:1") for i in range(6)]
        assert len(set(id(l) for l in base_locks)) == 6

    def test_same_base_images_are_serialized(self, tmp_path, no_network):
        proj = self._proj(tmp_path)
        client, state = self._counting_client()
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [
                ex.submit(
                    docker_ops.build_push_and_clean,
                    client, f"job{i}", str(proj), "python train.py", "same-base:1"
                )
                for i in range(4)
            ]
            for f in futures:
                f.result()
        assert state["max"] == 1  # never more than one build in flight

    def test_build_lock_is_per_base_image(self):
        a1 = docker_ops._get_build_lock("base-a:1")
        a2 = docker_ops._get_build_lock("base-a:1")
        b1 = docker_ops._get_build_lock("base-b:1")
        assert a1 is a2
        assert b1 is not a1
