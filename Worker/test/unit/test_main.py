"""Unit tests for main.py (heartbeat + job polling loops)."""
import threading
from unittest.mock import MagicMock, patch

import pytest

import main


def _stop_after_first_call(stop_event):
    """Side effect helper: set the stop event the first time it is called."""
    def _inner(*args, **kwargs):
        stop_event.set()

    return _inner


class TestHeartbeatLoop:
    def test_sends_heartbeat_then_stops(self):
        api = MagicMock()
        stop_event = threading.Event()
        api.send_heartbeat.side_effect = _stop_after_first_call(stop_event)
        with (
            patch.object(main, "get_gpu_info",
                         return_value=("A100", 80.0, 40.0, 2, 10.0)),
            patch.object(main, "collect_node_info", return_value={"hostname": "h"}),
            patch.object(main, "count_gpus_in_use", return_value=1),
            patch.object(main, "record_heartbeat") as mock_record,
            patch.object(main, "is_paused", return_value=False),
            patch.object(main.runtime_config, "get", return_value=5),
        ):
            main.heartbeat_loop(api, stop_event)
        api.send_heartbeat.assert_called_once()
        payload = api.send_heartbeat.call_args[0][2]
        assert payload["gpus_in_use"] == 1
        mock_record.assert_called_once_with(True)

    def test_paused_skips_heartbeat(self):
        api = MagicMock()
        stop_event = MagicMock()
        stop_event.is_set.side_effect = [False, True]
        with (
            patch.object(main, "is_paused", return_value=True),
            patch.object(main, "record_heartbeat") as mock_record,
        ):
            main.heartbeat_loop(api, stop_event)
        api.send_heartbeat.assert_not_called()
        mock_record.assert_not_called()
        stop_event.wait.assert_called_with(1.0)

    def test_exception_records_failed_heartbeat(self):
        api = MagicMock()
        api.send_heartbeat.side_effect = Exception("net down")
        stop_event = MagicMock()
        stop_event.is_set.side_effect = [False, True]
        with (
            patch.object(main, "get_gpu_info",
                         return_value=("A100", 80.0, 40.0, 2, 10.0)),
            patch.object(main, "collect_node_info", return_value={}),
            patch.object(main, "count_gpus_in_use", return_value=0),
            patch.object(main, "record_heartbeat") as mock_record,
            patch.object(main, "is_paused", return_value=False),
            patch.object(main.runtime_config, "get", return_value=5),
        ):
            main.heartbeat_loop(api, stop_event)
        mock_record.assert_called_once_with(False, "net down")


class TestJobLoop:
    def test_pulls_and_processes(self):
        executor = MagicMock()
        api = MagicMock()
        executor.resume_persisted_job_if_any.return_value = False
        api.pull_job.return_value = {"id": "j1"}
        stop_event = MagicMock()
        stop_event.is_set.side_effect = [False, True]
        with (
            patch.object(main, "get_gpu_info",
                         return_value=("A100", 80.0, 40.0, 2, 10.0)),
            patch.object(main, "is_paused", return_value=False),
            patch.object(main.runtime_config, "get", return_value=5),
        ):
            main.job_loop(executor, api, stop_event)
        executor.process_job.assert_called_once_with({"id": "j1"})

    def test_no_job_no_process(self):
        executor = MagicMock()
        api = MagicMock()
        executor.resume_persisted_job_if_any.return_value = False
        api.pull_job.return_value = None
        stop_event = MagicMock()
        stop_event.is_set.side_effect = [False, True]
        with (
            patch.object(main, "get_gpu_info",
                         return_value=("A100", 80.0, 40.0, 2, 10.0)),
            patch.object(main, "is_paused", return_value=False),
            patch.object(main.runtime_config, "get", return_value=5),
        ):
            main.job_loop(executor, api, stop_event)
        executor.process_job.assert_not_called()

    def test_paused_skips_polling(self):
        executor = MagicMock()
        api = MagicMock()
        stop_event = MagicMock()
        stop_event.is_set.side_effect = [False, True]
        with patch.object(main, "is_paused", return_value=True):
            main.job_loop(executor, api, stop_event)
        api.pull_job.assert_not_called()


class TestMain:
    def test_main_registers_and_starts_threads(self):
        fake_thread = MagicMock()
        with (
            patch.object(main, "get_or_create_worker_id", return_value="w1"),
            patch.object(main, "SchedulerAPI") as mock_api_cls,
            patch.object(main, "JobExecutor") as mock_exec_cls,
            patch.object(main, "get_gpu_info",
                         return_value=("A100", 80.0, 40.0, 2, 10.0)),
            patch.object(main, "collect_node_info", return_value={}),
            patch.object(main, "count_gpus_in_use", return_value=0),
            patch.object(main.threading, "Thread", return_value=fake_thread),
            patch.object(main.server, "run_in_thread"),
            patch.object(main.time, "sleep", side_effect=KeyboardInterrupt),
            patch.object(main.os, "getenv", side_effect=lambda k, d=None: d),
        ):
            main.main()
        mock_api_cls.return_value.register_worker.assert_called_once()
        assert fake_thread.start.call_count == 2
