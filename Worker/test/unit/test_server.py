"""Unit tests for server.py (Worker Agent API)."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture()
def client():
    return TestClient(server.app)


class TestHealth:
    def test_health(self, client):
        assert client.get("/health").json() == {"status": "ok"}


class TestWorkerInfo:
    def test_build_worker_info(self):
        with (
            patch.object(server, "get_gpu_info", return_value=("A100", 80.0, 40.0, 2, 10.0)),
            patch.object(server, "get_or_create_worker_id", return_value="w1"),
            patch.object(server, "get_hostname", return_value="h"),
            patch.object(server, "get_ip_address", return_value="1.2.3.4"),
            patch.object(server, "get_os_info", return_value="Linux 6.1"),
            patch.object(server, "get_mem_total_gb", return_value=32.0),
            patch.object(server, "docker_available", return_value=True),
            patch.object(server, "cuda_available", return_value=True),
        ):
            info = server._build_worker_info()
        assert info.workerId == "w1"
        assert info.gpuCount == 2
        assert info.gpuName == "A100"

    def test_get_worker_endpoint(self, client):
        with patch.object(
            server, "_build_worker_info",
            return_value=server.WorkerInfo(
                workerId="w", hostname="h", ipAddress="1.1.1.1", os="Linux",
                platform="linux", arch="x86_64", schedulerUrl="http://s",
                heartbeatIntervalSec=5, jobPollIntervalSec=10,
                maxConcurrentJobs=2,
                dockerAvailable=True, cudaAvailable=False, cpus=8,
                memTotalGb=32.0, gpuCount=1, gpuName="A100", gpuVramTotalGb=80.0,
            ),
        ):
            resp = client.get("/api/worker")
        assert resp.status_code == 200
        assert resp.json()["workerId"] == "w"


class TestMetrics:
    def test_build_metrics(self):
        io = {"diskReadBytesPerS": 1.0, "diskWriteBytesPerS": 2.0,
              "netRecvBytesPerS": 3.0, "netSentBytesPerS": 4.0}
        with (
            patch.object(server, "get_gpu_info", return_value=("A100", 80.0, 30.0, 1, 25.0)),
            patch.object(server.io_monitor, "sample", return_value=io),
            patch.object(server, "get_cpu_load", return_value=10.0),
            patch.object(server, "get_mem_usage", return_value=20.0),
            patch.object(server, "get_mem_total_gb", return_value=32.0),
            patch.object(server, "get_gpu_temperature", return_value=60.0),
            patch.object(server, "count_gpus_in_use", return_value=1),
        ):
            metrics = server._build_metrics()
        assert metrics.vramUsedGb == 50.0
        assert metrics.vramFreeGb == 30.0
        assert metrics.gpusInUse == 1

    def test_metrics_endpoint(self, client):
        with patch.object(server, "_build_metrics") as mock_build:
            mock_build.return_value = server.Metrics(
                cpuLoad=1.0, memUsage=2.0, memTotalGb=32.0, gpuLoad=3.0,
                vramUsedGb=1.0, vramFreeGb=79.0, vramTotalGb=80.0, gpuTempC=60.0,
                gpusInUse=0, diskReadBytesPerS=0.0, diskWriteBytesPerS=0.0,
                netRecvBytesPerS=0.0, netSentBytesPerS=0.0, timestamp=0.0,
            )
            resp = client.get("/api/metrics")
        assert resp.status_code == 200


class TestGpusJobsEvents:
    def test_gpus_endpoint(self, client):
        gpu = {"index": 0, "name": "A100", "load": 10.0, "vramUsedGb": 1.0,
               "vramFreeGb": 79.0, "vramTotalGb": 80.0, "temperatureC": 60.0,
               "inUse": True}
        with patch.object(server, "get_gpus_info", return_value=[gpu]):
            resp = client.get("/api/gpus")
        assert resp.status_code == 200
        assert resp.json()[0]["name"] == "A100"

    def test_jobs_and_events_endpoints(self, client, reset_telemetry):
        import telemetry

        telemetry.record_job({"id": "j1", "image": "i", "type": "training",
                              "status": "completed", "vramEstimateGb": 0.0,
                              "startedAt": "t", "durationSec": 1})
        telemetry.record_event("info", "hello")
        assert client.get("/api/jobs").json()[0]["id"] == "j1"
        assert client.get("/api/events").json()[-1]["message"] == "hello"

    def test_worker_logs_endpoint_never_reads_container_logs(self, client, reset_telemetry):
        import logging
        import telemetry

        handler = telemetry._WorkerLogHandler()
        handler.emit(logging.LogRecord(
            "executor", logging.INFO, "", 0, "[job j1] secret training output", (), None
        ))
        handler.emit(logging.LogRecord(
            "managed_worker", logging.INFO, "", 0, "worker polling", (), None
        ))
        response = client.get("/api/logs?limit=20")
        assert response.status_code == 200
        assert response.json()[-1]["message"] == "worker polling"
        assert all("secret training output" not in item["message"] for item in response.json())

    def test_jobs_include_live_coordinator_assignments(self, client):
        coordinator = MagicMock()
        coordinator.records.return_value = [{
            "assignment_id": "assignment-1",
            "kind": "batch_training",
            "payload": {"id": "job-1", "image_name": "repo/train:1", "vram_required": 4},
            "accepted_at": 1,
            "released": False,
        }]
        worker = MagicMock(coordinator=coordinator)
        server.set_managed_worker(worker)
        try:
            job = client.get("/api/jobs").json()[0]
        finally:
            server.set_managed_worker(None)
        assert job["id"] == "job-1"
        assert job["status"] == "running"
        assert job["assignmentId"] == "assignment-1"


class TestStatus:
    def test_connected_when_recent(self, reset_telemetry):
        import telemetry

        telemetry.record_heartbeat(True)
        with patch.object(server.runtime_config, "get", return_value=5.0):
            status = server._build_status()
        assert status.connected is True
        assert status.lastHeartbeatAt is not None

    def test_disconnected_when_stale(self, reset_telemetry):
        import time as time_mod

        import telemetry

        telemetry.record_heartbeat(True)
        with telemetry._lock:
            telemetry._last_heartbeat_success = time_mod.time() - 3600
        with patch.object(server.runtime_config, "get", return_value=5.0):
            status = server._build_status()
        assert status.connected is False

    def test_never_heartbeat_disconnected(self, reset_telemetry):
        status = server._build_status()
        assert status.connected is False
        assert status.lastHeartbeatAt is None

    def test_status_endpoint(self, client):
        assert client.get("/api/status").status_code == 200


class TestConfig:
    def test_get_config(self, client, restore_runtime_config):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        assert "schedulerUrl" in resp.json()

    def test_update_and_roundtrip_max_concurrent_jobs(self, client, restore_runtime_config):
        with (
            patch.object(server.telemetry, "record_event"),
            patch.object(server.runtime_config, "get",
                         return_value=3.0),
        ):
            resp = client.put(
                "/api/config", json={"maxConcurrentJobs": 3}
            )
        assert resp.status_code == 200
        resp2 = client.get("/api/config")
        assert resp2.status_code == 200
        assert resp2.json()["maxConcurrentJobs"] == 3.0
        from server import runtime_config
        assert runtime_config.get("max_concurrent_jobs") == 3.0


    def test_update_intervals(self, client, restore_runtime_config):
        with patch.object(server.telemetry, "record_event"):
            resp = client.put("/api/config", json={"heartbeatIntervalSec": 9.0})
        assert resp.status_code == 200
        assert resp.json()["heartbeatIntervalSec"] == 9.0

    def test_update_scheduler_url(self, client, restore_scheduler_url):
        with (
            patch.object(server.config_module, "persist_env") as mock_persist,
            patch.object(server.telemetry, "record_event"),
        ):
            resp = client.put(
                "/api/config", json={"schedulerUrl": "http://new-host:9000/"}
            )
        assert resp.status_code == 200
        assert resp.json()["schedulerUrl"] == "http://new-host:9000"
        mock_persist.assert_called_once()

    def test_same_scheduler_url_no_persist(self, client, restore_scheduler_url):
        import config as config_module

        current = config_module.get_scheduler_url()
        with patch.object(server.config_module, "persist_env") as mock_persist:
            client.put("/api/config", json={"schedulerUrl": current})
        mock_persist.assert_not_called()


class TestPauseResume:
    def test_pause_and_resume(self, client, reset_telemetry):
        assert client.post("/api/control/pause").json()["paused"] is True
        assert client.post("/api/control/resume").json()["paused"] is False


class TestDump:
    def test_model_dump_preferred(self):
        model = MagicMock()
        model.model_dump.return_value = {"a": 1}
        assert server._dump(model) == {"a": 1}

    def test_dict_fallback(self):
        class Old:
            def dict(self):
                return {"b": 2}

        assert server._dump(Old()) == {"b": 2}


class TestRunHelpers:
    def test_run_in_thread(self):
        with patch.object(server, "run") as mock_run:
            thread = server.run_in_thread("127.0.0.1", 8611)
            thread.join(timeout=5)
        mock_run.assert_called_once_with("127.0.0.1", 8611)
