"""Unit tests for Docker_Image_Builder/builder.py — pure functions."""

import os
import sys
import shutil
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402
from builder import find_project_dir, extract_job_archive, _claim_job, _release_job, _active_jobs  # noqa: E402


# ---------------------------------------------------------------------------
# find_project_dir
# ---------------------------------------------------------------------------
class TestFindProjectDir:
    def test_returns_first_non_hidden_dir(self, tmp_path):
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / ".git").mkdir()
        (tmp_path / "alpha_project").mkdir()
        (tmp_path / "z_project").mkdir()
        result = find_project_dir(str(tmp_path))
        # sorted() picks alphabetically first: alpha_project
        assert result == str(tmp_path / "alpha_project")

    def test_returns_root_if_no_dirs(self, tmp_path):
        (tmp_path / "file.txt").write_text("hello")
        result = find_project_dir(str(tmp_path))
        assert result == str(tmp_path)

    def test_returns_root_if_only_hidden_dirs(self, tmp_path):
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / ".git").mkdir()
        result = find_project_dir(str(tmp_path))
        assert result == str(tmp_path)

    def test_skips_underscore_prefixed(self, tmp_path):
        (tmp_path / "__init__").mkdir()
        project = tmp_path / "src"
        project.mkdir()
        result = find_project_dir(str(tmp_path))
        assert result == str(project)


# ---------------------------------------------------------------------------
# extract_job_archive
# ---------------------------------------------------------------------------
class TestExtractJobArchive:
    def _make_zip(self, directory: str, files: dict) -> str:
        """Create a zip file containing the given files."""
        zip_path = os.path.join(directory, "test.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        return zip_path

    def test_extracts_files(self, tmp_path):
        zip_path = self._make_zip(str(tmp_path), {
            "train.py": "print('train')",
            "model.py": "print('model')",
        })
        extract_dir = extract_job_archive(zip_path, "test-job")
        try:
            assert os.path.exists(os.path.join(extract_dir, "train.py"))
            assert os.path.exists(os.path.join(extract_dir, "model.py"))
            assert open(os.path.join(extract_dir, "train.py")).read() == "print('train')"
        finally:
            shutil.rmtree(extract_dir)

    def test_extracts_nested_dirs(self, tmp_path):
        zip_path = self._make_zip(str(tmp_path), {
            "src/train.py": "print('nested')",
        })
        extract_dir = extract_job_archive(zip_path, "test-job")
        try:
            assert os.path.exists(os.path.join(extract_dir, "src", "train.py"))
        finally:
            shutil.rmtree(extract_dir)

    def test_creates_temp_dir_with_prefix(self, tmp_path):
        zip_path = self._make_zip(str(tmp_path), {"a.txt": "a"})
        extract_dir = extract_job_archive(zip_path, "abc123")
        try:
            assert "job_abc123_" in extract_dir
            assert os.path.isdir(extract_dir)
        finally:
            shutil.rmtree(extract_dir)

    def test_invalid_zip_raises(self, tmp_path):
        bad_zip = os.path.join(str(tmp_path), "bad.zip")
        with open(bad_zip, "w") as f:
            f.write("not a zip file")
        with pytest.raises(zipfile.BadZipFile):
            extract_job_archive(bad_zip, "test-job")


# ---------------------------------------------------------------------------
# _claim_job / _release_job
# ---------------------------------------------------------------------------
class TestClaimReleaseJob:
    def setup_method(self):
        """Clear active jobs before each test."""
        _active_jobs.clear()

    def test_claim_returns_true_first_time(self):
        assert _claim_job("job-aaa") is True
        assert "job-aaa" in _active_jobs

    def test_claim_returns_false_when_already_active(self):
        assert _claim_job("job-bbb") is True
        assert _claim_job("job-bbb") is False

    def test_release_allows_reclaim(self):
        assert _claim_job("job-ccc") is True
        _release_job("job-ccc")
        assert "job-ccc" not in _active_jobs
        assert _claim_job("job-ccc") is True

    def test_release_nonexistent_is_safe(self):
        # Should not raise
        _release_job("nonexistent-id")

    def test_different_jobs_can_be_claimed_independently(self):
        assert _claim_job("job-1") is True
        assert _claim_job("job-2") is True
        assert len(_active_jobs) == 2


# ---------------------------------------------------------------------------
# process_job lifecycle tests
# ---------------------------------------------------------------------------
class TestProcessJobLifecycle:
    """Tests that process_job properly releases claims and notifies on
    failure/success paths."""

    def setup_method(self):
        """Clear active jobs before each test."""
        _active_jobs.clear()

    def test_malformed_job_releases_claim(self):
        """A job with only id (no object_key/base_image) must release the claim."""
        from builder import process_job

        _claim_job("job-1")
        client = MagicMock()
        push_executor = MagicMock()
        delete_executor = MagicMock()

        process_job({"id": "job-1"}, client, push_executor, delete_executor)

        assert "job-1" not in _active_jobs

    def test_already_processed_releases_claim(self):
        """A job that is already processed must be re-notified and released."""
        from builder import process_job

        _claim_job("job-1")
        client = MagicMock()
        push_executor = MagicMock()
        delete_executor = MagicMock()

        job = {
            "id": "job-1",
            "object_key": "user/test.zip",
            "docker_base_image": "pytorch/pytorch:latest",
            "command": "python train.py",
            "build_type": "training",
        }

        with patch("builder.is_job_processed", return_value=True), \
             patch("builder.notify_scheduler_job_ready", return_value=True) as mock_notify:
            process_job(job, client, push_executor, delete_executor)
            mock_notify.assert_called_once_with("job-1")

        assert "job-1" not in _active_jobs

    def test_push_keeps_job_active_until_push_finishes(self):
        """After process_job returns, the job stays active until the push
        function finishes and releases it."""
        from builder import process_job

        _claim_job("job-1")
        client = MagicMock()
        push_executor = MagicMock()
        delete_executor = MagicMock()

        job = {
            "id": "job-1",
            "object_key": "user/test.zip",
            "docker_base_image": "pytorch/pytorch:latest",
            "command": "python train.py",
            "build_type": "training",
        }

        with patch("builder.is_job_processed", return_value=False), \
             patch("builder.notify_scheduler_job_pending", return_value=True), \
             patch("builder.download_job_archive", return_value="/tmp/archive.zip"), \
             patch("builder.extract_job_archive", return_value="/tmp/extracted"), \
             patch("builder.find_project_dir", return_value="/tmp/project"), \
             patch("builder.build_image", return_value="job-1:latest"), \
             patch("builder.shutil"), \
             patch("builder.os"):

            # Capture the function submitted to push_executor
            def capture_submit(fn):
                push_executor._push_fn = fn
            push_executor.submit.side_effect = capture_submit

            process_job(job, client, push_executor, delete_executor)

            # Job should still be active after process_job returns
            assert "job-1" in _active_jobs

            # Now simulate the push completing
            with patch("builder.push_image", return_value=None), \
                 patch("builder.notify_scheduler_job_ready", return_value=True), \
                 patch("builder.mark_job_processed"), \
                 patch("builder.time"):
                push_executor._push_fn()

            # Now the job should be released
            assert "job-1" not in _active_jobs

    def test_build_failure_notifies_scheduler_and_releases(self):
        """When build_image returns a tuple (failure_type, reason), the
        scheduler is notified with the correct failure info and the job is
        released."""
        from builder import process_job

        _claim_job("job-1")
        client = MagicMock()
        push_executor = MagicMock()
        delete_executor = MagicMock()

        job = {
            "id": "job-1",
            "object_key": "user/test.zip",
            "docker_base_image": "pytorch/pytorch:latest",
            "command": "python train.py",
            "build_type": "training",
        }

        with patch("builder.is_job_processed", return_value=False), \
             patch("builder.notify_scheduler_job_pending", return_value=True), \
             patch("builder.download_job_archive", return_value="/tmp/archive.zip"), \
             patch("builder.extract_job_archive", return_value="/tmp/extracted"), \
             patch("builder.find_project_dir", return_value="/tmp/project"), \
             patch("builder.build_image", return_value=("system", "error")), \
             patch("builder.notify_scheduler_job_failed", return_value=True) as mock_fail, \
             patch("builder.shutil"), \
             patch("builder.os"):
            process_job(job, client, push_executor, delete_executor)

        mock_fail.assert_called_once_with("job-1", "system", "error")
        assert "job-1" not in _active_jobs

    def test_push_failure_notifies_scheduler(self):
        """When push_image returns a failure tuple, the scheduler is notified
        with a system failure."""
        from builder import process_job

        _claim_job("job-1")
        client = MagicMock()
        push_executor = MagicMock()
        delete_executor = MagicMock()

        job = {
            "id": "job-1",
            "object_key": "user/test.zip",
            "docker_base_image": "pytorch/pytorch:latest",
            "command": "python train.py",
            "build_type": "training",
        }

        with patch("builder.is_job_processed", return_value=False), \
             patch("builder.notify_scheduler_job_pending", return_value=True), \
             patch("builder.download_job_archive", return_value="/tmp/archive.zip"), \
             patch("builder.extract_job_archive", return_value="/tmp/extracted"), \
             patch("builder.find_project_dir", return_value="/tmp/project"), \
             patch("builder.build_image", return_value="job-1:latest"), \
             patch("builder.shutil"), \
             patch("builder.os"):

            def capture_submit(fn):
                push_executor._push_fn = fn
            push_executor.submit.side_effect = capture_submit

            process_job(job, client, push_executor, delete_executor)

            # Simulate push failure
            with patch("builder.push_image", return_value=("system", "auth failed")), \
                 patch("builder.notify_scheduler_job_failed", return_value=True) as mock_fail, \
                 patch("builder.time"):
                push_executor._push_fn()

        mock_fail.assert_called_once_with("job-1", "system", "auth failed")
        assert "job-1" not in _active_jobs


# ---------------------------------------------------------------------------
# scan_and_process tests
# ---------------------------------------------------------------------------
class TestScanAndProcess:
    def setup_method(self):
        """Clear active jobs before each test."""
        _active_jobs.clear()

    def test_submit_failure_releases_job(self):
        """If job_executor.submit raises, the job claim is released and the
        function does not crash."""
        from builder import scan_and_process

        client = MagicMock()
        job_executor = MagicMock()
        push_executor = MagicMock()
        delete_executor = MagicMock()

        job_executor.submit.side_effect = RuntimeError("queue full")

        jobs = [
            {
                "id": "job-1",
                "object_key": "user/test.zip",
                "docker_base_image": "pytorch/pytorch:latest",
                "command": "python train.py",
                "build_type": "training",
            }
        ]

        with patch("builder.fetch_unbuilt_jobs", return_value=jobs), \
             patch("builder.notify_scheduler_job_pending", return_value=True):
            # Should not raise
            scan_and_process(client, job_executor, push_executor, delete_executor)

        assert "job-1" not in _active_jobs
