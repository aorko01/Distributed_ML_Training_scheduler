"""Unit tests for api.py (SchedulerAPI HTTP client)."""
from unittest.mock import MagicMock, patch

import pytest

import api
from api import SchedulerAPI


def _resp(json_body=None):
    resp = MagicMock()
    resp.json.return_value = json_body if json_body is not None else {}
    return resp


@pytest.fixture()
def client():
    return SchedulerAPI("worker-1")


class TestUrl:
    def test_prefixes_scheduler_url(self):
        import config

        with patch.object(config, "_scheduler_url", "http://sched:1234"):
            assert api._url("/jobs/pull_job") == "http://sched:1234/jobs/pull_job"


class TestRegisterWorker:
    def test_posts_merged_payload(self, client):
        with patch.object(api.requests, "post", return_value=_resp({})) as mock_post:
            client.register_worker("A100", 2, 80.0, {"hostname": "h"})
        _, kwargs = mock_post.call_args
        assert kwargs["json"] == {
            "worker_id": "worker-1", "gpu_type": "A100", "num_gpus": 2,
            "total_vram": 80.0, "hostname": "h",
        }

    def test_exception_swallowed(self, client):
        with patch.object(api.requests, "post", side_effect=Exception("down")):
            assert client.register_worker("A100", 2, 80.0, {}) is None


class TestSendHeartbeat:
    def test_posts_payload(self, client):
        with patch.object(api.requests, "post", return_value=_resp({})) as mock_post:
            client.send_heartbeat("A100", 40.0, {"cpu_load": 1.0})
        assert mock_post.call_args[1]["json"]["available_vram"] == 40.0

    def test_error_propagates(self, client):
        with patch.object(api.requests, "post", side_effect=Exception("down")):
            with pytest.raises(Exception):
                client.send_heartbeat("A100", 1.0, {})


class TestPullJob:
    def test_returns_job_dict(self, client):
        with patch.object(
            api.requests, "post", return_value=_resp({"id": "j1", "flag": "training"})
        ):
            assert client.pull_job("A100", 10.0)["id"] == "j1"

    def test_message_returns_none(self, client):
        with patch.object(
            api.requests, "post", return_value=_resp({"message": "empty"})
        ):
            assert client.pull_job("A100", 10.0) is None

    def test_error_returns_none(self, client):
        with patch.object(
            api.requests, "post", return_value=_resp({"error": "bad"})
        ):
            assert client.pull_job("A100", 10.0) is None

    def test_exception_returns_none(self, client):
        with patch.object(api.requests, "post", side_effect=Exception("down")):
            assert client.pull_job("A100", 10.0) is None


class TestMarkCompleted:
    def test_posts_job_id(self, client):
        with patch.object(api.requests, "post", return_value=_resp({})) as mock_post:
            client.mark_job_completed("j1")
        assert mock_post.call_args[1]["json"] == {"job_id": "j1"}

    def test_exception_swallowed(self, client):
        with patch.object(api.requests, "post", side_effect=Exception("down")):
            assert client.mark_job_completed("j1") is None


class TestMarkFailed:
    def test_truncates_reason(self, client):
        with patch.object(api.requests, "post", return_value=_resp({})) as mock_post:
            client.mark_job_failed("j1", "user", "r" * 5000)
        payload = mock_post.call_args[1]["json"]
        assert len(payload["failure_reason"]) == 2000
        assert payload["failure_type"] == "user"

    def test_exception_swallowed(self, client):
        with patch.object(api.requests, "post", side_effect=Exception("down")):
            assert client.mark_job_failed("j1", "system", "r") is None


class TestResumeJob:
    def test_returns_payload(self, client):
        with patch.object(
            api.requests, "post", return_value=_resp({"id": "j1", "flag": "retry"})
        ):
            out = client.resume_job("j1", "A100")
        assert out["flag"] == "retry"

    def test_error_or_message_returns_none(self, client):
        with patch.object(api.requests, "post", return_value=_resp({"error": "x"})):
            assert client.resume_job("j1", "A100") is None
        with patch.object(api.requests, "post", return_value=_resp({"message": "y"})):
            assert client.resume_job("j1", "A100") is None

    def test_network_error_reraises(self, client):
        with patch.object(api.requests, "post", side_effect=Exception("down")):
            with pytest.raises(Exception):
                client.resume_job("j1", "A100")

    def test_sends_device(self, client):
        with patch.object(api.requests, "post", return_value=_resp({})) as mock_post:
            client.resume_job("j1", "H100")
        assert mock_post.call_args[1]["json"]["device"] == "H100"


class TestSendLogs:
    def test_empty_lines_no_request(self, client):
        with patch.object(api.requests, "post") as mock_post:
            client.send_logs("j1", [])
            mock_post.assert_not_called()

    def test_posts_lines(self, client):
        with patch.object(api.requests, "post", return_value=_resp({})) as mock_post:
            client.send_logs("j1", ["a"])
        assert mock_post.call_args[1]["json"] == {"lines": ["a"]}

    def test_exception_swallowed(self, client):
        with patch.object(api.requests, "post", side_effect=Exception("down")):
            assert client.send_logs("j1", ["a"]) is None


class TestSaveVramEstimation:
    def test_maps_report_fields(self, client):
        report = {"peak_reserved_memory": 4.0, "peak_ram_memory": 8.0,
                  "step_wall_time": 1.5}
        with patch.object(api.requests, "post", return_value=_resp({})) as mock_post:
            client.save_vram_estimation("j1", report)
        assert mock_post.call_args[1]["json"] == {
            "job_id": "j1", "vram_required": 4.0, "ram_required": 8.0,
            "step_time": 1.5,
        }

    def test_missing_key_raises(self, client):
        with pytest.raises(KeyError):
            client.save_vram_estimation("j1", {})

    def test_exception_swallowed(self, client):
        with patch.object(api.requests, "post", side_effect=Exception("down")):
            report = {"peak_reserved_memory": 1.0, "peak_ram_memory": 1.0,
                      "step_wall_time": 1.0}
            assert client.save_vram_estimation("j1", report) is None
