"""Unit tests for main.py (heartbeat + job polling loops)."""
import threading
import time
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
        executor = MagicMock()
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
            main.heartbeat_loop(api, executor, stop_event)
        api.send_heartbeat.assert_called_once()
        payload = api.send_heartbeat.call_args[0][2]
        assert payload["gpus_in_use"] == 1
        mock_record.assert_called_once_with(True)

    def test_paused_skips_heartbeat(self):
        api = MagicMock()
        executor = MagicMock()
        stop_event = MagicMock()
        stop_event.is_set.side_effect = [False, True]
        with (
            patch.object(main, "is_paused", return_value=True),
            patch.object(main, "record_heartbeat") as mock_record,
        ):
            main.heartbeat_loop(api, executor, stop_event)
        api.send_heartbeat.assert_not_called()
        mock_record.assert_not_called()
        stop_event.wait.assert_called_with(1.0)

    def test_exception_records_failed_heartbeat(self):
        api = MagicMock()
        api.send_heartbeat.side_effect = Exception("net down")
        executor = MagicMock()
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
            main.heartbeat_loop(api, executor, stop_event)
        mock_record.assert_called_once_with(False, "net down")


class TestJobLoop:
    def test_pulls_and_processes(self):
        executor = MagicMock()
        executor.active_jobs_count = 0
        api = MagicMock()
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
        executor.active_jobs_count = 0
        api = MagicMock()
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
        executor.active_jobs_count = 0
        api = MagicMock()
        stop_event = MagicMock()
        stop_event.is_set.side_effect = [False, True]
        with patch.object(main, "is_paused", return_value=True):
            main.job_loop(executor, api, stop_event)
        api.pull_job.assert_not_called()

    def test_pulls_multiple_jobs_concurrently(self):
        """job_loop should pull a new job immediately when capacity allows,
        rather than waiting for the previous job to finish."""
        executor = MagicMock()
        executor.active_jobs_count = 0
        executor.has_unresumed_job.return_value = False
        api = MagicMock()
        stop_event = MagicMock()
        stop_event.is_set.side_effect = [False, False, True]
        api.pull_job.side_effect = [{"id": "j1"}, {"id": "j2"}, None]
        with (
            patch.object(main, "get_gpu_info",
                         return_value=("A100", 80.0, 40.0, 2, 10.0)),
            patch.object(main, "is_paused", return_value=False),
            patch.object(main.runtime_config, "get", return_value=5),
            patch.object(main.threading, "Thread") as mock_thread_cls,
        ):
            main.job_loop(executor, api, stop_event)
        assert api.pull_job.call_count == 2
        thread_calls = sum(1 for c in mock_thread_cls.call_args_list
                           if c.kwargs.get("target") is not executor.resume_persisted_job_if_any)
        assert thread_calls == 2
        resume_calls = sum(1 for c in mock_thread_cls.call_args_list
                           if c.kwargs.get("target") is executor.resume_persisted_job_if_any)
        assert resume_calls == 0

    def test_respects_max_concurrent_jobs(self):
        """job_loop should not pull a job when at max concurrent capacity."""
        executor = MagicMock()
        executor.active_jobs_count = 5
        api = MagicMock()
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
        api.pull_job.assert_not_called()

    def test_effective_vram_passed_to_pull(self):
        executor = MagicMock()
        executor.active_jobs_count = 0
        executor.get_effective_free_vram.return_value = 7.0
        api = MagicMock()
        api.pull_job.return_value = None
        stop_event = MagicMock()
        stop_event.is_set.side_effect = [False, True]
        with (
            patch.object(main, "get_gpu_info",
                         return_value=("A100", 12.0, 10.0, 1, 0)),
            patch.object(main, "is_paused", return_value=False),
            patch.object(main.runtime_config, "get", return_value=5),
        ):
            main.job_loop(executor, api, stop_event)
        api.pull_job.assert_called_once_with("A100", 7.0)

    def test_resume_in_loop(self):
        executor = MagicMock()
        executor.active_jobs_count = 0
        executor.has_unresumed_job.return_value = True
        api = MagicMock()
        api.pull_job.return_value = None
        stop_event = MagicMock()
        stop_event.is_set.side_effect = [False, True]
        with (
            patch.object(main, "get_gpu_info",
                         return_value=("A100", 80.0, 40.0, 2, 10.0)),
            patch.object(main, "is_paused", return_value=False),
            patch.object(main.runtime_config, "get", return_value=5),
            patch.object(main.threading, "Thread") as mock_thread_cls,
        ):
            main.job_loop(executor, api, stop_event)
        executor.has_unresumed_job.assert_called()
        resume_threads = [
            c for c in mock_thread_cls.call_args_list
            if c.kwargs.get("target") is executor.resume_persisted_job_if_any
        ]
        assert len(resume_threads) >= 1

    @pytest.mark.xfail(
        strict=True,
        reason="R1: job_loop does not reserve a capacity slot before starting the "
        "worker thread, so a job whose thread has not called _register_job yet is "
        "not counted and job_loop over-pulls beyond max_concurrent_jobs. Remove this "
        "marker once job_loop reserves synchronously before t.start().",
    )
    def test_reserves_capacity_before_worker_thread_registers(self):
        """job_loop must count a pulled job immediately, not only once the
        background thread reaches _register_job."""
        import executor as executor_module
        from executor import JobExecutor

        class _SlowRegisterExecutor(JobExecutor):
            def __init__(self, exec_api):
                with patch.object(executor_module.docker, "from_env",
                                  return_value=MagicMock()):
                    super().__init__(exec_api)
                self.gate = threading.Event()
                self.started = []

            def process_job(self, job):
                # Model the window between Thread.start() and _register_job:
                # the loop must not treat this job as free capacity meanwhile.
                self.started.append(job.get("id"))
                self.gate.wait(timeout=0.3)
                self._register_job(job.get("id"), job.get("vram_required"))

        api = MagicMock()
        api.pull_job.return_value = {"id": "j", "vram_required": 1.0}
        ex = _SlowRegisterExecutor(api)
        stop_event = MagicMock()
        stop_event.is_set.side_effect = [False] * 8 + [True]

        def _cfg(key, default=0.0):
            return 2.0 if key == "max_concurrent_jobs" else 5.0

        with (
            patch.object(main, "get_gpu_info",
                         return_value=("A100", 80.0, 40.0, 2, 10.0)),
            patch.object(main, "is_paused", return_value=False),
            patch.object(main.runtime_config, "get", side_effect=_cfg),
            patch.object(ex, "has_unresumed_job", return_value=False),
        ):
            main.job_loop(ex, api, stop_event)
        ex.gate.set()
        assert api.pull_job.call_count <= 2

    @pytest.mark.xfail(
        strict=True,
        reason="R2: while a persisted job is being resumed (resume thread has not "
        "registered yet) job_loop spawns a new resume thread on every poll, so the "
        "same job can be resumed concurrently. Remove this marker once resume is "
        "reserved/deduplicated in job_loop.",
    )
    def test_resume_spawned_once_per_persisted_job(self):
        """A persisted job must not get multiple concurrent resume attempts."""
        import executor as executor_module
        from executor import JobExecutor

        class _SlowResumeExecutor(JobExecutor):
            def __init__(self, exec_api):
                with patch.object(executor_module.docker, "from_env",
                                  return_value=MagicMock()):
                    super().__init__(exec_api)
                self.gate = threading.Event()
                self.calls = 0
                self._lk = threading.Lock()

            def resume_persisted_job_if_any(self):
                with self._lk:
                    self.calls += 1
                # Model a slow resume that only registers once it is underway.
                self.gate.wait(timeout=0.3)
                self._register_job("j1", 1.0)
                return True

        api = MagicMock()
        api.pull_job.return_value = None
        ex = _SlowResumeExecutor(api)
        stop_event = MagicMock()
        stop_event.is_set.side_effect = [False] * 20 + [True]

        def _cfg(key, default=0.0):
            return 2.0 if key == "max_concurrent_jobs" else 5.0

        with (
            patch.object(main, "get_gpu_info",
                         return_value=("A100", 80.0, 40.0, 2, 10.0)),
            patch.object(main, "is_paused", return_value=False),
            patch.object(main.runtime_config, "get", side_effect=_cfg),
            patch.object(executor_module, "load_running_jobs",
                         return_value=[{"job_id": "j1", "saved_at": 0.0}]),
        ):
            main.job_loop(ex, api, stop_event)
        ex.gate.set()
        assert ex.calls == 1


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

    def test_main_start_server_without_blocking_resume(self):
        """main() must not block on resume before starting server."""
        executor = MagicMock()
        executor.active_jobs_count = 0
        with (
            patch.object(main, "get_or_create_worker_id", return_value="w1"),
            patch.object(main, "SchedulerAPI") as mock_api_cls,
            patch.object(main, "JobExecutor", return_value=executor),
            patch.object(main, "get_gpu_info",
                         return_value=("A100", 80.0, 40.0, 2, 10.0)),
            patch.object(main, "collect_node_info", return_value={}),
            patch.object(main, "count_gpus_in_use", return_value=0),
            patch.object(main.threading, "Thread") as mock_thread_cls,
            patch.object(main.server, "run_in_thread") as mock_server,
            patch.object(main.time, "sleep", side_effect=KeyboardInterrupt),
            patch.object(main.os, "getenv", side_effect=lambda k, d=None: d),
            patch.object(executor, "resume_persisted_job_if_any") as mock_resume,
        ):
            main.main()
        mock_server.assert_called_once()
        assert not mock_resume.called
