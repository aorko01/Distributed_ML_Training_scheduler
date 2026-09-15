"""Unit tests for builder.py (polling orchestration)."""
import io
import zipfile
from unittest.mock import MagicMock, patch

import pytest

import builder


def _zip_bytes(files=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in (files or {"train.py": "x"}).items():
            zf.writestr(name, content)
    return buf.getvalue()


def _training_job(job_id="j1", **overrides):
    job = {
        "id": job_id,
        "object_key": f"{job_id}/a.zip",
        "command": "python train.py",
        "docker_base_image": "base:1",
    }
    job.update(overrides)
    return job


@pytest.fixture()
def mocked_env():
    with (
        patch.object(builder, "docker") as mock_docker,
        patch.object(builder, "docker_login") as mock_login,
        patch.object(builder, "prune_old_base_images") as mock_prune,
        patch.object(builder, "fetch_unbuilt_jobs") as mock_fetch,
        patch.object(builder, "download_job_archive") as mock_dl,
        patch.object(builder, "extract_job_archive") as mock_extract,
        patch.object(builder, "find_project_dir") as mock_find,
        patch.object(builder, "build_push_and_clean") as mock_build,
        patch.object(builder, "notify_scheduler_job_ready") as mock_ready,
        patch.object(builder, "notify_scheduler_job_failed") as mock_failed,
    ):
        mock_docker.from_env.return_value = MagicMock()
        mock_fetch.return_value = []
        yield {
            "docker": mock_docker, "fetch": mock_fetch,
            "download": mock_dl, "extract": mock_extract, "find": mock_find,
            "build": mock_build, "ready": mock_ready,
            "failed": mock_failed,
        }


class TestFindProjectDir:
    def test_single_nested_dir(self, tmp_path):
        (tmp_path / "myproj").mkdir()
        (tmp_path / "myproj" / "train.py").write_text("x")
        assert builder.find_project_dir(str(tmp_path)) == str(tmp_path / "myproj")

    def test_sorted_first_dir_wins(self, tmp_path):
        (tmp_path / "b").mkdir()
        (tmp_path / "a").mkdir()
        assert builder.find_project_dir(str(tmp_path)) == str(tmp_path / "a")

    def test_hidden_and_dunder_skipped(self, tmp_path):
        (tmp_path / "__MACOSX").mkdir()
        (tmp_path / ".hidden").mkdir()
        (tmp_path / "real").mkdir()
        assert builder.find_project_dir(str(tmp_path)) == str(tmp_path / "real")

    def test_files_ignored_no_dirs_returns_self(self, tmp_path):
        (tmp_path / "train.py").write_text("x")
        assert builder.find_project_dir(str(tmp_path)) == str(tmp_path)

    def test_empty_dir_returns_self(self, tmp_path):
        assert builder.find_project_dir(str(tmp_path)) == str(tmp_path)


class TestExtractJobArchive:
    def test_valid_zip_extracts(self):
        out = builder.extract_job_archive(_zip_bytes({"a/train.py": "x"}), "j1")
        import os

        try:
            assert os.path.exists(os.path.join(out, "a", "train.py"))
        finally:
            import shutil

            shutil.rmtree(out, ignore_errors=True)

    def test_invalid_bytes_raise_and_cleanup(self):
        import tempfile

        before = set()
        with pytest.raises(Exception):
            builder.extract_job_archive(b"not a zip", "j1")
        # extract dir must have been removed (no leftover job_j1_ dirs)
        leftovers = [
            d for d in __import__("os").listdir(tempfile.gettempdir())
            if d.startswith("job_j1_")
        ]
        assert leftovers == []


class TestScanAndProcess:
    def test_fetch_failure_returns_quietly(self, mocked_env):
        mocked_env["fetch"].side_effect = Exception("scheduler down")
        builder.scan_and_process(mocked_env["docker"].from_env.return_value)  # no raise

    def test_malformed_jobs_skipped(self, mocked_env):
        mocked_env["fetch"].return_value = [
            {"id": "", "object_key": "k", "docker_base_image": "b"},
            {"id": "j2"},  # missing object_key/base_image
        ]
        builder.scan_and_process(mocked_env["docker"].from_env.return_value)
        mocked_env["build"].assert_not_called()

    def test_training_success(self, mocked_env):
        mocked_env["fetch"].return_value = [_training_job("j1")]
        mocked_env["download"].return_value = _zip_bytes()
        mocked_env["extract"].return_value = "/tmp/extract"
        mocked_env["find"].return_value = "/tmp/extract/proj"
        mocked_env["build"].return_value = None
        mocked_env["ready"].return_value = True
        with patch.object(builder.shutil, "rmtree") as mock_rmtree:
            builder.scan_and_process(mocked_env["docker"].from_env.return_value)
        mocked_env["build"].assert_called_once()
        args = mocked_env["build"].call_args[0]
        assert args[1] == "j1" and args[3] == "python train.py"

    def test_notify_failure(self, mocked_env):
        mocked_env["fetch"].return_value = [_training_job("j1")]
        mocked_env["download"].return_value = _zip_bytes()
        mocked_env["extract"].return_value = "/tmp/extract"
        mocked_env["find"].return_value = "/tmp/extract/proj"
        mocked_env["build"].return_value = None
        mocked_env["ready"].return_value = False
        with patch.object(builder.shutil, "rmtree"):
            builder.scan_and_process(mocked_env["docker"].from_env.return_value)

    def test_user_failure_reported(self, mocked_env):
        mocked_env["fetch"].return_value = [_training_job("j1")]
        mocked_env["download"].return_value = _zip_bytes()
        mocked_env["extract"].return_value = "/tmp/extract"
        mocked_env["find"].return_value = "/tmp/extract/proj"
        mocked_env["build"].return_value = ("user", "pip failed")
        mocked_env["failed"].return_value = True
        with patch.object(builder.shutil, "rmtree"):
            builder.scan_and_process(mocked_env["docker"].from_env.return_value)
        mocked_env["failed"].assert_called_once_with("j1", "user", "pip failed")

    def test_system_failure_kept_pending(self, mocked_env):
        mocked_env["fetch"].return_value = [_training_job("j1")]
        mocked_env["download"].return_value = _zip_bytes()
        mocked_env["extract"].return_value = "/tmp/extract"
        mocked_env["find"].return_value = "/tmp/extract/proj"
        mocked_env["build"].return_value = ("system", "daemon down")
        with patch.object(builder.shutil, "rmtree"):
            builder.scan_and_process(mocked_env["docker"].from_env.return_value)
        mocked_env["failed"].assert_not_called()

    def test_download_exception_becomes_system_result(self, mocked_env):
        mocked_env["fetch"].return_value = [_training_job("j1")]
        mocked_env["download"].side_effect = Exception("store down")
        with patch.object(builder.shutil, "rmtree"):
            builder.scan_and_process(mocked_env["docker"].from_env.return_value)
        mocked_env["failed"].assert_not_called()  # system failures are not reported


class TestMain:
    def test_main_loops_and_sleeps(self):
        with (
            patch.object(builder, "init_db") as mock_init,
            patch.object(builder, "docker") as mock_docker,
            patch.object(builder, "docker_login") as mock_login,
            patch.object(builder, "prune_old_base_images") as mock_prune,
            patch.object(builder, "scan_and_process") as mock_scan,
            patch.object(builder.time, "sleep", side_effect=[None, KeyboardInterrupt]),
        ):
            mock_docker.from_env.return_value = MagicMock()
            with pytest.raises(KeyboardInterrupt):
                builder.main()
        mock_init.assert_called_once()
        mock_login.assert_called_once()
        assert mock_prune.call_count >= 1
        assert mock_scan.call_count == 2

    def test_main_survives_scan_errors(self):
        with (
            patch.object(builder, "init_db"),
            patch.object(builder, "docker") as mock_docker,
            patch.object(builder, "docker_login"),
            patch.object(builder, "prune_old_base_images"),
            patch.object(
                builder, "scan_and_process", side_effect=Exception("boom")
            ),
            patch.object(builder.time, "sleep", side_effect=KeyboardInterrupt),
        ):
            mock_docker.from_env.return_value = MagicMock()
            with pytest.raises(KeyboardInterrupt):
                builder.main()
