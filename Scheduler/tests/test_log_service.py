"""Unit tests for Scheduler/app/services/log_service.py _stream_key."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.log_service import _stream_key  # noqa: E402


class TestStreamKey:
    def test_returns_prefixed_key(self):
        result = _stream_key("job-123")
        assert result == "logs:job-123"

    def test_empty_job_id(self):
        result = _stream_key("")
        assert result == "logs:"

    def test_uuid_job_id(self):
        job_id = "550e8400-e29b-41d4-a716-446655440000"
        result = _stream_key(job_id)
        assert result == f"logs:{job_id}"

    def test_returns_string(self):
        result = _stream_key("any-job")
        assert isinstance(result, str)
