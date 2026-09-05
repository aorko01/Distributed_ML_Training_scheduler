"""Unit tests for Docker_Image_Builder/api.py — HTTP interactions."""

import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api import notify_scheduler_job_pending  # noqa: E402


class TestNotifySchedulerJobPending:
    @patch("api.requests.post")
    def test_returns_true_on_200_success(self, mock_post):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"status": "ok"}
        mock_post.return_value = response

        result = notify_scheduler_job_pending("job-123")
        assert result is True
        mock_post.assert_called_once()

    @patch("api.requests.post")
    def test_returns_false_on_200_with_error(self, mock_post):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"error": "job not found"}
        mock_post.return_value = response

        result = notify_scheduler_job_pending("job-123")
        assert result is False

    @patch("api.requests.post")
    def test_returns_false_on_non_200_status(self, mock_post):
        response = MagicMock()
        response.status_code = 500
        response.text = "Internal Server Error"
        mock_post.return_value = response

        result = notify_scheduler_job_pending("job-123")
        assert result is False

    @patch("api.requests.post")
    def test_returns_false_on_connection_error(self, mock_post):
        mock_post.side_effect = ConnectionError("refused")

        result = notify_scheduler_job_pending("job-123")
        assert result is False
