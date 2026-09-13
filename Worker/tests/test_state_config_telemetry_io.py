"""Unit tests for job_state.py, runtime_config.py, telemetry.py, io_monitor.py."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# job_state
# ---------------------------------------------------------------------------
class TestJobState:
    @pytest.fixture()
    def state_file(self, tmp_path, monkeypatch):
        import job_state

        path = str(tmp_path / "running_job.json")
        monkeypatch.setattr(job_state, "STATE_FILE", path)
        return path

    def test_save_and_load_roundtrip(self, state_file):
        import job_state

        job_state.save_running_job("job-1")
        data = job_state.load_running_job()
        assert data["job_id"] == "job-1"
        assert "saved_at" in data

    def test_load_missing_returns_none(self, state_file):
        import job_state

        assert job_state.load_running_job() is None

    def test_load_invalid_json_returns_none(self, state_file):
        import job_state

        with open(state_file, "w") as f:
            f.write("{bad json")
        assert job_state.load_running_job() is None

    def test_load_without_job_id_returns_none(self, state_file):
        import job_state

        with open(state_file, "w") as f:
            json.dump({"foo": 1}, f)
        assert job_state.load_running_job() is None

    def test_load_non_dict_returns_none(self, state_file):
        import job_state

        with open(state_file, "w") as f:
            json.dump(["job-1"], f)
        assert job_state.load_running_job() is None

    def test_clear_removes_file(self, state_file):
        import os

        import job_state

        job_state.save_running_job("job-1")
        job_state.clear_running_job()
        assert not os.path.exists(state_file)

    def test_clear_missing_is_noop(self, state_file):
        import job_state

        job_state.clear_running_job()  # no raise


# ---------------------------------------------------------------------------
# runtime_config
# ---------------------------------------------------------------------------
class TestRuntimeConfig:
    def test_get_known_and_unknown(self, restore_runtime_config):
        import runtime_config

        assert runtime_config.get("heartbeat_interval") > 0
        assert runtime_config.get("nope") == 0.0

    def test_set_many_updates_and_clamps(self, restore_runtime_config):
        import runtime_config

        runtime_config.set_many({"heartbeat_interval": 10.0})
        assert runtime_config.get("heartbeat_interval") == 10.0
        runtime_config.set_many({"heartbeat_interval": 0.1})
        assert runtime_config.get("heartbeat_interval") == 1.0

    def test_set_many_ignores_unknown_and_invalid(self, restore_runtime_config):
        import runtime_config

        before = runtime_config.all()
        runtime_config.set_many({"unknown": 5.0, "heartbeat_interval": "bad"})
        assert runtime_config.all() == before

    def test_all_returns_copy(self, restore_runtime_config):
        import runtime_config

        snapshot = runtime_config.all()
        snapshot["heartbeat_interval"] = 9999.0
        assert runtime_config.get("heartbeat_interval") != 9999.0


# ---------------------------------------------------------------------------
# telemetry
# ---------------------------------------------------------------------------
class TestTelemetry:
    def test_record_job_trims_and_events(self, reset_telemetry):
        import telemetry

        for i in range(telemetry.MAX_JOB_HISTORY + 5):
            telemetry.record_job({"id": f"j{i}", "status": "completed"})
        assert len(telemetry.get_jobs()) == telemetry.MAX_JOB_HISTORY
        assert telemetry.get_jobs()[-1]["id"] == f"j{telemetry.MAX_JOB_HISTORY + 4}"

    def test_record_job_failed_level(self, reset_telemetry):
        import telemetry

        telemetry.record_job({"id": "j1", "status": "failed"})
        assert telemetry.get_events()[-1]["level"] == "error"

    def test_record_event(self, reset_telemetry):
        import telemetry

        telemetry.record_event("info", "hello")
        assert telemetry.get_events()[-1]["message"] == "hello"

    def test_events_trimmed(self, reset_telemetry):
        import telemetry

        for i in range(telemetry.MAX_EVENTS + 10):
            telemetry.record_event("info", f"m{i}")
        assert len(telemetry.get_events()) == telemetry.MAX_EVENTS

    def test_record_heartbeat_success_and_failure(self, reset_telemetry):
        import telemetry

        telemetry.record_heartbeat(True)
        hb = telemetry.get_heartbeat()
        assert hb["last_success_at"] is not None
        assert hb["last_error"] is None
        telemetry.record_heartbeat(False, "timeout")
        hb = telemetry.get_heartbeat()
        assert hb["last_error"] == "timeout"

    def test_pause_flag(self, reset_telemetry):
        import telemetry

        assert telemetry.is_paused() is False
        telemetry.set_paused(True)
        assert telemetry.is_paused() is True
        telemetry.set_paused(False)
        assert telemetry.is_paused() is False

    def test_getters_return_copies(self, reset_telemetry):
        import telemetry

        before = len(telemetry.get_events())
        telemetry.record_event("info", "x")
        events = telemetry.get_events()
        assert len(events) == before + 1
        events.clear()
        assert len(telemetry.get_events()) == before + 1


# ---------------------------------------------------------------------------
# io_monitor
# ---------------------------------------------------------------------------
class TestIORateMonitor:
    def test_first_sample_zeros(self):
        from io_monitor import IORateMonitor

        monitor = IORateMonitor()
        disk = SimpleNamespace(read_bytes=1000, write_bytes=2000)
        net = SimpleNamespace(bytes_recv=3000, bytes_sent=4000)
        with (
            patch("io_monitor.psutil.disk_io_counters", return_value=disk),
            patch("io_monitor.psutil.net_io_counters", return_value=net),
        ):
            out = monitor.sample()
        assert out == {
            "diskReadBytesPerS": 0.0, "diskWriteBytesPerS": 0.0,
            "netRecvBytesPerS": 0.0, "netSentBytesPerS": 0.0,
        }

    def test_second_sample_computes_rates(self):
        from io_monitor import IORateMonitor

        monitor = IORateMonitor()
        disk1 = SimpleNamespace(read_bytes=1000, write_bytes=2000)
        net1 = SimpleNamespace(bytes_recv=3000, bytes_sent=4000)
        disk2 = SimpleNamespace(read_bytes=3000, write_bytes=6000)
        net2 = SimpleNamespace(bytes_recv=9000, bytes_sent=8000)
        with (
            patch("io_monitor.psutil.disk_io_counters", side_effect=[disk1, disk2]),
            patch("io_monitor.psutil.net_io_counters", side_effect=[net1, net2]),
            patch("io_monitor.time.time", side_effect=[100.0, 102.0]),
        ):
            monitor.sample()
            out = monitor.sample()
        assert out["diskReadBytesPerS"] == 1000.0
        assert out["diskWriteBytesPerS"] == 2000.0
        assert out["netRecvBytesPerS"] == 3000.0
        assert out["netSentBytesPerS"] == 2000.0

    def test_counter_reset_clamped_to_zero(self):
        from io_monitor import IORateMonitor

        monitor = IORateMonitor()
        disk1 = SimpleNamespace(read_bytes=5000, write_bytes=5000)
        net1 = SimpleNamespace(bytes_recv=5000, bytes_sent=5000)
        disk2 = SimpleNamespace(read_bytes=100, write_bytes=100)
        net2 = SimpleNamespace(bytes_recv=100, bytes_sent=100)
        with (
            patch("io_monitor.psutil.disk_io_counters", side_effect=[disk1, disk2]),
            patch("io_monitor.psutil.net_io_counters", side_effect=[net1, net2]),
            patch("io_monitor.time.time", side_effect=[100.0, 101.0]),
        ):
            monitor.sample()
            out = monitor.sample()
        assert all(v == 0.0 for v in out.values())

    def test_same_timestamp_returns_zeros(self):
        from io_monitor import IORateMonitor

        monitor = IORateMonitor()
        counters = SimpleNamespace(
            read_bytes=1, write_bytes=1, bytes_recv=1, bytes_sent=1
        )
        with (
            patch("io_monitor.psutil.disk_io_counters", return_value=counters),
            patch("io_monitor.psutil.net_io_counters", return_value=counters),
            patch("io_monitor.time.time", return_value=50.0),
        ):
            monitor.sample()
            assert all(v == 0.0 for v in monitor.sample().values())

    def test_psutil_exception_returns_zeros(self):
        from io_monitor import IORateMonitor

        with patch(
            "io_monitor.psutil.disk_io_counters", side_effect=Exception("x")
        ):
            out = IORateMonitor().sample()
        assert all(v == 0.0 for v in out.values())
