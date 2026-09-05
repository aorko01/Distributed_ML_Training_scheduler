"""Unit tests for Scheduler/app/services/job_service.py."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock  # noqa: E402
import pytest  # noqa: E402
from app.services.job_service import (  # noqa: E402
    _format_job_response,
    set_job_pending,
    set_job_vram_estimation_pending,
    mark_interactive_ready,
    mark_job_failed,
    BUILD_IN_PROGRESS_STATUSES,
)
from app.models.job_model import JobStatus  # noqa: E402


class TestFormatJobResponse:
    def test_basic_fields(self, sample_job):
        result = _format_job_response(sample_job, "training")
        assert result["flag"] == "training"
        assert result["id"] == "test-job-123"
        assert result["user_id"] == "user-456"
        assert result["object_key"] == "user-456/test.zip"
        assert result["name"] == "test-training-job"
        assert result["command"] == "python train.py"
        assert result["resume_command"] == "python train.py --resume"
        assert result["docker_base_image"] == "pytorch/pytorch:2.0.0-cuda11.7-cudnn8-runtime"
        assert result["config"] == {"lr": 0.001, "epochs": 10}

    def test_status_and_priority_values(self, sample_job):
        result = _format_job_response(sample_job, "training")
        assert result["status"] == "IN_PROGRESS"
        assert result["priority"] == "NORMAL"

    def test_numeric_fields(self, sample_job):
        result = _format_job_response(sample_job, "training")
        assert result["vram_required"] == 8.0
        assert result["ram_required"] == 16.0
        assert result["reason_for_priority"] is None

    def test_datetime_fields(self, sample_job):
        result = _format_job_response(sample_job, "training")
        assert result["created_at"] == sample_job.created_at
        assert result["updated_at"] == sample_job.updated_at

    def test_device_field(self, sample_job):
        result = _format_job_response(sample_job, "training")
        assert result["device"] == "NVIDIA A100"

    def test_different_flags(self, sample_job):
        for flag in ["training", "vram_estimation", "retry", "interactive"]:
            result = _format_job_response(sample_job, flag)
            assert result["flag"] == flag

    def test_none_fields_handled(self, sample_job):
        sample_job.reason_for_priority = None
        sample_job.device = None
        result = _format_job_response(sample_job, "training")
        assert result["reason_for_priority"] is None
        assert result["device"] is None


# ---------------------------------------------------------------------------
# set_job_pending
# ---------------------------------------------------------------------------
class TestSetJobPending:
    def _make_db_with_job(self, status: JobStatus):
        """Create a mock DB session with a job in the given status."""
        job = MagicMock()
        job.id = "test-job-001"
        job.status = status

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = job
        return db, job

    def test_happy_path(self):
        db, job = self._make_db_with_job(JobStatus.NOT_RUNNABLE)
        result = set_job_pending(db, "test-job-001")
        assert job.status == JobStatus.PENDING
        db.commit.assert_called_once()
        db.refresh.assert_called_once_with(job)
        assert result == job

    def test_error_wrong_state(self):
        db, job = self._make_db_with_job(JobStatus.IN_PROGRESS)
        with pytest.raises(Exception, match="Job is not in NOT_RUNNABLE state"):
            set_job_pending(db, "test-job-001")

    def test_error_job_not_found(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(Exception, match="Job not found"):
            set_job_pending(db, "nonexistent-id")


# ---------------------------------------------------------------------------
# set_job_vram_estimation_pending — now accepts PENDING status
# ---------------------------------------------------------------------------
class TestSetJobVramEstimationPending:
    def _make_db_with_job(self, status: JobStatus):
        job = MagicMock()
        job.id = "test-job-002"
        job.status = status
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = job
        return db, job

    def test_accepts_not_runnable(self):
        db, job = self._make_db_with_job(JobStatus.NOT_RUNNABLE)
        result = set_job_vram_estimation_pending(db, "test-job-002")
        assert job.status == JobStatus.VRAM_ESTIMATION_PENDING
        assert result == job

    def test_accepts_pending(self):
        db, job = self._make_db_with_job(JobStatus.PENDING)
        result = set_job_vram_estimation_pending(db, "test-job-002")
        assert job.status == JobStatus.VRAM_ESTIMATION_PENDING
        assert result == job

    def test_rejects_wrong_state(self):
        db, job = self._make_db_with_job(JobStatus.RUNNABLE)
        with pytest.raises(Exception, match="Job is not in NOT_RUNNABLE state"):
            set_job_vram_estimation_pending(db, "test-job-002")


# ---------------------------------------------------------------------------
# mark_interactive_ready — now accepts PENDING status
# ---------------------------------------------------------------------------
class TestMarkInteractiveReady:
    def _make_db_with_job(self, status: JobStatus):
        job = MagicMock()
        job.id = "test-job-003"
        job.status = status
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = job
        return db, job

    def test_accepts_not_runnable(self):
        db, job = self._make_db_with_job(JobStatus.NOT_RUNNABLE)
        result = mark_interactive_ready(db, "test-job-003")
        assert job.status == JobStatus.INTERACTIVE_READY
        assert result == job

    def test_accepts_pending(self):
        db, job = self._make_db_with_job(JobStatus.PENDING)
        result = mark_interactive_ready(db, "test-job-003")
        assert job.status == JobStatus.INTERACTIVE_READY
        assert result == job

    def test_rejects_wrong_state(self):
        db, job = self._make_db_with_job(JobStatus.RUNNABLE)
        with pytest.raises(Exception, match="Job is not in NOT_RUNNABLE state"):
            mark_interactive_ready(db, "test-job-003")


# ---------------------------------------------------------------------------
# BUILD_IN_PROGRESS_STATUSES guard set
# ---------------------------------------------------------------------------
class TestBuildInProgressStatuses:
    def test_includes_not_runnable(self):
        assert JobStatus.NOT_RUNNABLE in BUILD_IN_PROGRESS_STATUSES

    def test_includes_pending(self):
        assert JobStatus.PENDING in BUILD_IN_PROGRESS_STATUSES


# ---------------------------------------------------------------------------
# mark_job_failed
# ---------------------------------------------------------------------------
class TestMarkJobFailed:
    def _make_db_with_job(self, status: JobStatus):
        """Create a mock DB session with a job in the given status."""
        job = MagicMock()
        job.id = "test-job-fail"
        job.status = status
        job.started_at = None
        job.failure_reason = None

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = job
        return db, job

    def test_system_failure_on_pending_resets_to_not_runnable(self):
        db, job = self._make_db_with_job(JobStatus.PENDING)
        mark_job_failed(db, "test-job-fail", "system", "infra error")
        assert job.status == JobStatus.NOT_RUNNABLE

    def test_system_failure_on_in_progress_stays_retry_needed(self):
        db, job = self._make_db_with_job(JobStatus.IN_PROGRESS)
        mark_job_failed(db, "test-job-fail", "system", "infra error")
        assert job.status == JobStatus.RETRY_NEEDED

    def test_user_failure_on_pending_marks_failed(self):
        db, job = self._make_db_with_job(JobStatus.PENDING)
        mark_job_failed(db, "test-job-fail", "user", "code error")
        assert job.status == JobStatus.FAILED

    def test_system_failure_on_not_runnable_keeps_not_runnable(self):
        db, job = self._make_db_with_job(JobStatus.NOT_RUNNABLE)
        mark_job_failed(db, "test-job-fail", "system", "infra error")
        assert job.status == JobStatus.NOT_RUNNABLE
