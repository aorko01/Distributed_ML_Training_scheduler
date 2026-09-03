"""Unit tests for Scheduler/app/services/job_service.py _format_job_response."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.job_service import _format_job_response  # noqa: E402


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
