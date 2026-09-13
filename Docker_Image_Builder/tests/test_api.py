"""Unit tests for api.py (Scheduler + object-store HTTP client)."""
from unittest.mock import MagicMock, patch

import pytest

import api


def _resp(status=200, json_body=None, content=b"data"):
    resp = MagicMock()
    resp.status_code = status
    resp.text = "text"
    resp.content = content
    resp.json.return_value = json_body if json_body is not None else {}
    return resp


class TestFetchUnbuiltJobs:
    def test_returns_jobs_list(self):
        with patch.object(
            api.requests, "get", return_value=_resp(json_body={"jobs": [{"id": "j1"}]})
        ) as mock_get:
            assert api.fetch_unbuilt_jobs() == [{"id": "j1"}]
            assert mock_get.call_args[0][0] == api.SCHEDULER_QUEUE_URL

    def test_missing_jobs_key_returns_empty(self):
        with patch.object(api.requests, "get", return_value=_resp(json_body={})):
            assert api.fetch_unbuilt_jobs() == []

    def test_http_error_propagates(self):
        resp = _resp()
        resp.raise_for_status.side_effect = Exception("500")
        with patch.object(api.requests, "get", return_value=resp):
            with pytest.raises(Exception):
                api.fetch_unbuilt_jobs()


class TestDownloadJobArchive:
    def test_returns_bytes_and_encodes_key(self):
        with patch.object(
            api.requests, "get", return_value=_resp(content=b"zip-bytes")
        ) as mock_get:
            out = api.download_job_archive("job 1/archive.zip")
        assert out == b"zip-bytes"
        url = mock_get.call_args[0][0]
        assert "job%201/archive.zip" in url
        assert url.startswith(api.OBJECT_STORE_URL)

    def test_error_propagates(self):
        resp = _resp()
        resp.raise_for_status.side_effect = Exception("404")
        with patch.object(api.requests, "get", return_value=resp):
            with pytest.raises(Exception):
                api.download_job_archive("k")


class TestSendLogLines:
    def test_empty_lines_make_no_request(self):
        with patch.object(api.requests, "post") as mock_post:
            assert api.send_log_lines("j", []) is None
            mock_post.assert_not_called()

    def test_success_posts_to_job_url(self):
        with patch.object(
            api.requests, "post", return_value=_resp()
        ) as mock_post:
            api.send_log_lines("j1", ["a", "b"])
        url = mock_post.call_args[0][0]
        assert url == f"{api.SCHEDULER_LOG_URL}/j1"
        assert mock_post.call_args[1]["json"] == {"lines": ["a", "b"]}

    def test_failure_swallowed(self):
        with patch.object(api.requests, "post", side_effect=Exception("down")):
            assert api.send_log_lines("j1", ["a"]) is None


class TestNotifyReady:
    def test_success_true(self):
        with patch.object(api.requests, "post", return_value=_resp(200, {})):
            assert api.notify_scheduler_job_ready("j1") is True

    def test_error_key_false(self):
        with patch.object(
            api.requests, "post", return_value=_resp(200, {"error": "bad"})
        ):
            assert api.notify_scheduler_job_ready("j1") is False

    def test_non_200_false(self):
        with patch.object(api.requests, "post", return_value=_resp(500, {})):
            assert api.notify_scheduler_job_ready("j1") is False

    def test_exception_false(self):
        with patch.object(api.requests, "post", side_effect=Exception("down")):
            assert api.notify_scheduler_job_ready("j1") is False

    def test_interactive_success_true(self):
        with patch.object(api.requests, "post", return_value=_resp(200, {})) as m:
            assert api.notify_scheduler_interactive_ready("j1") is True
            assert m.call_args[0][0] == api.SCHEDULER_INTERACTIVE_UPDATE_URL

    def test_interactive_error_key_false(self):
        with patch.object(
            api.requests, "post", return_value=_resp(200, {"error": "x"})
        ):
            assert api.notify_scheduler_interactive_ready("j1") is False

    def test_interactive_non_200_false(self):
        with patch.object(api.requests, "post", return_value=_resp(502, {})):
            assert api.notify_scheduler_interactive_ready("j1") is False

    def test_interactive_exception_false(self):
        with patch.object(api.requests, "post", side_effect=Exception("down")):
            assert api.notify_scheduler_interactive_ready("j1") is False


class TestNotifyFailed:
    def test_success_and_truncation(self):
        with patch.object(
            api.requests, "post", return_value=_resp(200, {})
        ) as mock_post:
            assert api.notify_scheduler_job_failed("j1", "user", "r" * 5000) is True
        payload = mock_post.call_args[1]["json"]
        assert payload["failure_type"] == "user"
        assert len(payload["failure_reason"]) == 2000

    def test_error_key_false(self):
        with patch.object(
            api.requests, "post", return_value=_resp(200, {"error": "rejected"})
        ):
            assert api.notify_scheduler_job_failed("j", "system", "r") is False

    def test_non_200_false(self):
        with patch.object(api.requests, "post", return_value=_resp(500, {})):
            assert api.notify_scheduler_job_failed("j", "user", "r") is False

    def test_exception_false(self):
        with patch.object(api.requests, "post", side_effect=Exception("down")):
            assert api.notify_scheduler_job_failed("j", "user", "r") is False
