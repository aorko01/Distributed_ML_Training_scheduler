"""Unit tests for executor.py (job lifecycle on the worker)."""
import os
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import executor as executor_module
from executor import JobExecutor, _docker_host_path


class TestDockerHostPath:
    def test_posix_unchanged(self):
        with patch.object(executor_module.os, "name", "posix"):
            assert _docker_host_path("/a/b") == "/a/b"

    def test_windows_converted(self):
        with patch.object(executor_module.os, "name", "nt"):
            assert _docker_host_path("C:\\a\\b") == "C:/a/b"


class TestInit:
    def test_docker_failure_leaves_no_client(self, mock_api):
        # Fixed: docker_client is always defined (None on failure) so later
        # accesses fail with a clear Docker error instead of AttributeError.
        with patch.object(executor_module.docker, "from_env", side_effect=Exception("no daemon")):
            ex = JobExecutor(mock_api)
        assert ex.docker_client is None


class TestRecordJob:
    def test_estimation_vs_training(self, executor):
        with (
            patch.object(executor_module, "record_job") as mock_job,
            patch.object(executor_module, "record_event") as mock_event,
        ):
            JobExecutor._record_job("j1", "img", "vram_estimation", "completed", 0.0)
            assert mock_job.call_args[0][0]["type"] == "estimation"
            JobExecutor._record_job("j2", "img", "training", "failed", 0.0)
            assert mock_job.call_args[0][0]["type"] == "training"
        assert mock_event.call_count == 2


class TestPullImage:
    def test_success_and_failure(self, executor):
        assert executor.pull_docker_image("img") is True
        executor.docker_client.images.pull.side_effect = Exception("denied")
        assert executor.pull_docker_image("img") is False


class TestWorkdir:
    def test_reads_workdir(self, executor):
        executor.docker_client.images.get.return_value = MagicMock(
            attrs={"Config": {"WorkingDir": "/workspace"}}
        )
        assert executor._image_workdir("img") == "/workspace"

    def test_missing_workdir_returns_none(self, executor):
        executor.docker_client.images.get.return_value = MagicMock(
            attrs={"Config": {}}
        )
        assert executor._image_workdir("img") is None

    def test_exception_returns_none(self, executor):
        executor.docker_client.images.get.side_effect = Exception("x")
        assert executor._image_workdir("img") is None

    def test_resolve_falls_back_and_rejects_root(self, executor):
        with patch.object(executor_module.JobExecutor, "_image_workdir", return_value=None):
            assert executor._resolve_mount_target("img") == "/workspace"
        with patch.object(executor_module.JobExecutor, "_image_workdir", return_value="/"):
            assert executor._resolve_mount_target("img") is None


class TestPrepareOutputMount:
    def test_fallback_when_no_workdir(self, executor, tmp_path):
        with patch.object(executor_module.JobExecutor, "_resolve_mount_target", return_value=None):
            target, baseline = executor._prepare_output_mount(str(tmp_path), "img")
        assert target == executor_module.CONTAINER_OUTPUT_MOUNT
        assert baseline == set()

    def test_seeds_and_records_baseline(self, executor, tmp_path):
        out_dir = tmp_path / "job1"
        out_dir.mkdir()
        (out_dir / "existing.txt").write_text("x")
        with (
            patch.object(executor_module.JobExecutor, "_resolve_mount_target", return_value="/workspace"),
            patch.object(executor_module.subprocess, "run") as mock_run,
            patch.object(executor_module, "write_baseline") as mock_baseline,
        ):
            target, baseline = executor._prepare_output_mount(str(out_dir), "img")
        assert target == "/workspace"
        assert mock_run.call_count == 3  # create, cp, rm
        assert any(str(out_dir) in p or "existing" in p for p in baseline)

    def test_seed_failure_falls_back(self, executor, tmp_path):
        with (
            patch.object(executor_module.JobExecutor, "_resolve_mount_target", return_value="/ws"),
            patch.object(
                executor_module.subprocess, "run",
                side_effect=[subprocess.CalledProcessError(1, "docker", stderr="err"),
                             MagicMock()],
            ),
        ):
            target, baseline = executor._prepare_output_mount(str(tmp_path), "img")
        assert target == executor_module.CONTAINER_OUTPUT_MOUNT
        assert baseline == set()


class TestContainerUserArgs:
    def test_root_mode_empty(self):
        with patch.object(executor_module, "CONTAINER_AS_ROOT", True):
            assert JobExecutor._container_user_args() == []

    def test_non_posix_empty(self):
        with patch.object(executor_module, "CONTAINER_AS_ROOT", False), patch.object(
            executor_module.os, "name", "nt"
        ):
            assert JobExecutor._container_user_args() == []

    def test_posix_uid_gid(self):
        with (
            patch.object(executor_module, "CONTAINER_AS_ROOT", False),
            patch.object(executor_module.os, "name", "posix"),
        ):
            if not hasattr(os, "getuid"):
                assert JobExecutor._container_user_args() == []
            else:
                args = JobExecutor._container_user_args()
                assert args[0] == "--user"


class TestParsePythonCommand:
    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("python train.py --epochs 5", ["train.py", "--epochs", "5"]),
            ("python3 train.py", ["train.py"]),
            ("CUDA_VISIBLE_DEVICES=0 python train.py", ["train.py"]),
            ("python -u train.py --lr 0.01", ["train.py", "--lr", "0.01"]),
            ('["python", "train.py", "--epochs", "5"]', ["train.py", "--epochs", "5"]),
            ("echo hello", None),
            ("", None),
            ("python", None),
            ("python -u", None),
            ("[[[", None),
        ],
    )
    def test_cases(self, command, expected):
        assert JobExecutor._parse_python_command(command) == expected


class TestHandleVramEstimation:
    def test_non_python_command_fails_user(self, executor):
        executor.api.mark_job_failed = MagicMock()
        with (
            patch.object(JobExecutor, "_parse_python_command", return_value=None),
            patch.object(JobExecutor, "_record_job") as mock_record,
        ):
            executor.handle_vram_estimation("j1", "img", "echo hi")
        executor.api.mark_job_failed.assert_called_once_with(
            "j1", "user", "Job needs a Python command for VRAM estimation"
        )
        mock_record.assert_called_once()

    def test_container_launch_failure_is_system(self, executor):
        executor.api.mark_job_failed = MagicMock()
        with (
            patch.object(JobExecutor, "_parse_python_command", return_value=["train.py"]),
            patch.object(executor_module.subprocess, "run", side_effect=Exception("no docker")),
            patch.object(JobExecutor, "_record_job"),
        ):
            executor.handle_vram_estimation("j1", "img", "python train.py")
        assert executor.api.mark_job_failed.call_args[0][1] == "system"

    def test_nonzero_exit_is_user_failure(self, executor):
        result = SimpleNamespace(returncode=1, stderr="traceback here", stdout="")
        executor.api.mark_job_failed = MagicMock()
        with (
            patch.object(JobExecutor, "_parse_python_command", return_value=["train.py"]),
            patch.object(executor_module.subprocess, "run", return_value=result),
            patch.object(JobExecutor, "_record_job"),
        ):
            executor.handle_vram_estimation("j1", "img", "python train.py")
        assert executor.api.mark_job_failed.call_args[0][1] == "user"

    def test_missing_report_is_user_failure(self, executor, tmp_path):
        result = SimpleNamespace(returncode=0, stderr="", stdout="")
        executor.api.mark_job_failed = MagicMock()
        import contextlib

        @contextlib.contextmanager
        def _fake_tmpdir(*a, **k):
            yield str(tmp_path)

        with (
            patch.object(JobExecutor, "_parse_python_command", return_value=["train.py"]),
            patch.object(executor_module.subprocess, "run", return_value=result),
            patch.object(executor_module.tempfile, "TemporaryDirectory", _fake_tmpdir),
            patch.object(JobExecutor, "_record_job"),
        ):
            executor.handle_vram_estimation("j1", "img", "python train.py")
        assert "Invalid VRAM estimation report" in executor.api.mark_job_failed.call_args[0][2]

    def test_success_saves_report(self, executor, tmp_path):
        import contextlib
        import json

        result = SimpleNamespace(returncode=0, stderr="", stdout="")

        @contextlib.contextmanager
        def _fake_tmpdir(*a, **k):
            yield str(tmp_path)

        def _run_and_write_report(cmd, **kwargs):
            with open(tmp_path / "report.json", "w") as f:
                json.dump(
                    {"peak_reserved_memory": 4.5, "peak_ram_memory": 8.0,
                     "step_wall_time": 1.25},
                    f,
                )
            return result

        executor.api.save_vram_estimation = MagicMock()
        with (
            patch.object(JobExecutor, "_parse_python_command", return_value=["train.py"]),
            patch.object(executor_module.subprocess, "run", side_effect=_run_and_write_report),
            patch.object(executor_module.tempfile, "TemporaryDirectory", _fake_tmpdir),
            patch.object(JobExecutor, "_record_job"),
        ):
            executor.handle_vram_estimation("j1", "img", "python train.py")
        executor.api.save_vram_estimation.assert_called_once()

    def test_report_dir_avoids_systemd_private_tmp(self, executor, tmp_path):
        """The report dir must not live under /tmp: with PrivateTmp=true the
        worker's /tmp is a private namespace invisible to the Docker daemon,
        so the bind-mount lands empty and report.json is never seen (rc=0,
        dir_listing=[])."""
        import contextlib

        result = SimpleNamespace(returncode=0, stderr="", stdout="")
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        @contextlib.contextmanager
        def _fake_tmpdir(*a, **k):
            # Must be pinned under WORKER_STATE_DIR (daemon-visible), never
            # the tempfile default (/tmp → hidden by PrivateTmp). Note: pytest
            # tmp_path itself lives under /tmp, so only assert the state-dir
            # pinning here, not a blanket /tmp prefix ban.
            assert k.get("dir", "").startswith(str(state_dir)), (
                f"report tmpdir must be under WORKER_STATE_DIR, got dir={k.get('dir')!r}"
            )
            yield str(tmp_path)

        with (
            patch.dict(executor_module.os.environ, {"WORKER_STATE_DIR": str(state_dir)}),
            patch.object(JobExecutor, "_parse_python_command", return_value=["train.py"]),
            patch.object(executor_module.subprocess, "run", return_value=result),
            patch.object(executor_module.tempfile, "TemporaryDirectory", _fake_tmpdir),
            patch.object(JobExecutor, "_record_job"),
        ):
            executor.api.mark_job_failed = MagicMock()
            executor.handle_vram_estimation("j1", "img", "python train.py")

    def test_report_base_dir_falls_back_to_output_dir(self, executor, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        with (
            patch.dict(executor_module.os.environ, {}, clear=False),
            patch.object(executor_module.os.environ, "get", return_value=None),
            patch.object(executor_module, "OUTPUT_DIR", str(out)),
        ):
            base = JobExecutor._vram_report_base_dir()
        assert base == str(out / "vram-reports")


class TestHandleTraining:
    def test_starts_monitor_and_runs(self, executor, tmp_path):
        with (
            patch.object(executor_module, "OUTPUT_DIR", str(tmp_path)),
            patch.object(JobExecutor, "_remove_output_dir", return_value=True),
            patch.object(executor_module.os, "makedirs"),
            patch.object(JobExecutor, "_prepare_output_mount", return_value=("/ws", set())),
            patch.object(executor_module, "ObjectStore") as mock_store_cls,
            patch.object(executor_module, "OutputFileMonitor") as mock_mon_cls,
            patch.object(JobExecutor, "_run_container") as mock_run,
        ):
            executor.handle_training("j1", "img")
        mock_run.assert_called_once()
        mock_mon_cls.return_value.start.assert_called_once()

    def test_entry_command_is_applied_at_run_time(self, executor, tmp_path):
        """Images are built without a command; the submitted one runs here."""
        with (
            patch.object(executor_module, "OUTPUT_DIR", str(tmp_path)),
            patch.object(JobExecutor, "_remove_output_dir", return_value=True),
            patch.object(executor_module.os, "makedirs"),
            patch.object(JobExecutor, "_prepare_output_mount", return_value=("/ws", set())),
            patch.object(executor_module, "ObjectStore"),
            patch.object(executor_module, "OutputFileMonitor"),
            patch.object(JobExecutor, "_run_container") as mock_run,
        ):
            executor.handle_training("j1", "img", "python train.py --epochs 3")
        assert mock_run.call_args.kwargs["command_args"] == [
            "sh", "-c", "python train.py --epochs 3"
        ]

    def test_missing_command_falls_back_to_image_default(self, executor, tmp_path):
        with (
            patch.object(executor_module, "OUTPUT_DIR", str(tmp_path)),
            patch.object(JobExecutor, "_remove_output_dir", return_value=True),
            patch.object(executor_module.os, "makedirs"),
            patch.object(JobExecutor, "_prepare_output_mount", return_value=("/ws", set())),
            patch.object(executor_module, "ObjectStore"),
            patch.object(executor_module, "OutputFileMonitor"),
            patch.object(JobExecutor, "_run_container") as mock_run,
        ):
            executor.handle_training("j1", "img", None)
        assert mock_run.call_args.kwargs["command_args"] is None


class TestHandleRetry:
    def test_resume_success_returns_early(self, executor):
        with (
            patch.object(JobExecutor, "_resume_attempt", return_value=None),
            patch.object(JobExecutor, "handle_training") as mock_train,
        ):
            executor.handle_retry("j", "img", "python resume.py", "python train.py")
        mock_train.assert_not_called()

    def test_system_resume_failure_marks_failed(self, executor):
        executor.api.mark_job_failed = MagicMock()
        with (
            patch.object(JobExecutor, "_resume_attempt", return_value=("system", "no gpu")),
            patch.object(JobExecutor, "_record_job"),
            patch.object(executor_module, "clear_running_job") as mock_clear,
            patch.object(JobExecutor, "handle_training") as mock_train,
        ):
            executor.handle_retry("j", "img", "python resume.py", "python train.py")
        executor.api.mark_job_failed.assert_called_once_with("j", "system", "no gpu")
        mock_clear.assert_called_once()
        mock_train.assert_not_called()

    def test_user_resume_failure_falls_back_to_fresh(self, executor):
        with (
            patch.object(JobExecutor, "_resume_attempt", return_value=("user", "bad ckpt")),
            patch.object(JobExecutor, "handle_training") as mock_train,
        ):
            executor.handle_retry("j", "img", "python resume.py", "python train.py")
        mock_train.assert_called_once()

    def test_no_original_command_marks_failed(self, executor):
        executor.api.mark_job_failed = MagicMock()
        with (
            patch.object(JobExecutor, "_resume_attempt", return_value=("user", "bad")),
            patch.object(JobExecutor, "_record_job"),
            patch.object(executor_module, "clear_running_job"),
        ):
            executor.handle_retry("j", "img", "python resume.py", None)
        executor.api.mark_job_failed.assert_called_once_with(
            "j", "system", "Retry job has no original command"
        )


class TestResumeAttempt:
    def test_local_baseline_reused(self, executor, tmp_path):
        store = MagicMock()
        with (
            patch.object(executor_module.os.path, "isfile", return_value=True),
            patch.object(JobExecutor, "_resolve_mount_target", return_value="/ws"),
            patch.object(executor_module, "load_baseline", return_value=set()),
            patch.object(executor_module, "OutputFileMonitor") as mock_mon,
            patch.object(JobExecutor, "_run_container", return_value=(True, "", "")),
            patch.object(JobExecutor, "_finalize_job") as mock_final,
        ):
            out = executor._resume_attempt(
                "j", "img", str(tmp_path), store, "python resume.py", 0.0
            )
        assert out is None
        mock_final.assert_called_once()

    def test_no_local_state_restores_from_store(self, executor, tmp_path):
        store = MagicMock()
        with (
            patch.object(executor_module.os.path, "isfile", return_value=False),
            patch.object(JobExecutor, "_remove_output_dir", return_value=True),
            patch.object(executor_module.os, "makedirs"),
            patch.object(JobExecutor, "_prepare_output_mount", return_value=("/ws", set())),
            patch.object(JobExecutor, "_restore_job_output") as mock_restore,
            patch.object(executor_module, "OutputFileMonitor") as mock_mon,
            patch.object(JobExecutor, "_run_container", return_value=(True, "", "")),
            patch.object(JobExecutor, "_finalize_job"),
        ):
            out = executor._resume_attempt(
                "j", "img", str(tmp_path), store, "python resume.py", 0.0
            )
        assert out is None
        mock_restore.assert_called_once()

    def test_failed_resume_returns_tuple(self, executor, tmp_path):
        store = MagicMock()
        with (
            patch.object(executor_module.os.path, "isfile", return_value=True),
            patch.object(JobExecutor, "_resolve_mount_target", return_value="/ws"),
            patch.object(executor_module, "load_baseline", return_value=set()),
            patch.object(executor_module, "OutputFileMonitor"),
            patch.object(
                JobExecutor, "_run_container", return_value=(False, "user", "bad")
            ),
            patch.object(JobExecutor, "_remove_output_dir", return_value=True),
        ):
            out = executor._resume_attempt(
                "j", "img", str(tmp_path), store, "python resume.py", 0.0
            )
        assert out == ("user", "bad")


class TestRestoreJobOutput:
    def test_restores_and_skips_build_log(self, executor, tmp_path):
        store = MagicMock()
        store.list_objects.return_value = [
            {"key": "j1/ckpt.pt", "size": 10},
            {"key": "j1/build.log", "size": 5},
            {"key": "j1/", "size": 0},
        ]
        store.download_to.return_value = True
        executor._restore_job_output("j1", str(tmp_path), store)
        store.download_to.assert_called_once()  # only ckpt.pt

    def test_no_objects_logs_info(self, executor, tmp_path):
        store = MagicMock()
        store.list_objects.return_value = []
        executor._restore_job_output("j1", str(tmp_path), store)  # no raise


class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    def readline(self):
        return self._lines.pop(0) if self._lines else ""


class TestRunContainer:
    def _popen(self, lines, returncode=0):
        proc = MagicMock()
        proc.stdout = _FakeStdout(lines)
        proc.returncode = returncode
        return proc

    def test_success_finalizes(self, executor):
        store = MagicMock()
        store.download.return_value = None
        monitor = MagicMock()
        executor.api.send_logs.return_value = True
        with (
            patch.object(executor_module.subprocess, "Popen",
                         return_value=self._popen(["epoch 1\n"], 0)),
            patch.object(JobExecutor, "_finalize_job") as mock_final,
        ):
            executor._run_container("j", "img", "/tmp/out", "/ws", store,
                                    monitor, 0.0)
        mock_final.assert_called_once()
        assert mock_final.call_args[0][5] is True
        executor.api.send_logs.assert_called_once_with("j", ["epoch 1"])

    def test_nonzero_exit_is_user_failure(self, executor):
        store = MagicMock()
        store.download.return_value = None
        monitor = MagicMock()
        with (
            patch.object(executor_module.subprocess, "Popen",
                         return_value=self._popen([], 3)),
            patch.object(JobExecutor, "_finalize_job") as mock_final,
        ):
            executor._run_container("j", "img", "/tmp/out", "/ws", store,
                                    monitor, 0.0)
        assert mock_final.call_args[0][5] is False
        assert mock_final.call_args[0][6] == "user"

    def test_popen_exception_is_system_failure(self, executor):
        store = MagicMock()
        store.download.return_value = None
        monitor = MagicMock()
        with (
            patch.object(executor_module.subprocess, "Popen", side_effect=Exception("boom")),
            patch.object(JobExecutor, "_finalize_job") as mock_final,
        ):
            executor._run_container("j", "img", "/tmp/out", "/ws", store,
                                    monitor, 0.0)
        assert mock_final.call_args[0][6] == "system"

    def test_no_finalize_returns_tuple(self, executor):
        store = MagicMock()
        store.download.return_value = None
        monitor = MagicMock()
        with patch.object(executor_module.subprocess, "Popen",
                          return_value=self._popen(["hi\n"], 0)):
            out = executor._run_container(
                "j", "img", "/tmp/out", "/ws", store, monitor, 0.0,
                command_args=["sh", "-c", "x"], finalize=False,
            )
        assert out == (True, "system", "Training container failed to start") or out[0] is True


class TestRemoveOutputDir:
    def test_gone_returns_true(self, executor, tmp_path):
        assert executor._remove_output_dir(str(tmp_path / "nope")) is True

    def test_rmtree_success(self, executor, tmp_path):
        d = tmp_path / "job"
        d.mkdir()
        (d / "f.txt").write_text("x")
        assert executor._remove_output_dir(str(d)) is True

    def test_docker_fallback(self, executor):
        with (
            patch.object(executor_module.shutil, "rmtree"),
            patch.object(executor_module.os.path, "exists", side_effect=[True, False]),
            patch.object(executor_module.subprocess, "run") as mock_run,
        ):
            assert executor._remove_output_dir("/out") is True
        mock_run.assert_called_once()

    def test_fallback_failure_returns_false(self, executor):
        with (
            patch.object(executor_module.shutil, "rmtree"),
            patch.object(executor_module.os.path, "exists", return_value=True),
            patch.object(executor_module.subprocess, "run", side_effect=Exception("x")),
        ):
            assert executor._remove_output_dir("/out") is False


class TestFinalizeJob:
    def test_success_clears_and_completes(self, executor):
        monitor = MagicMock()
        monitor.pending_uploads.return_value = []
        executor.api.mark_job_completed = MagicMock()
        with (
            patch.object(JobExecutor, "_remove_output_dir", return_value=True),
            patch.object(JobExecutor, "_record_job"),
            patch.object(executor_module, "clear_running_job") as mock_clear,
        ):
            executor._finalize_job("j", "img", "/out", monitor, 0.0, True)
        executor.api.mark_job_completed.assert_called_once_with("j")
        mock_clear.assert_called_once_with("j")

    def test_pending_uploads_keep_dir(self, executor):
        monitor = MagicMock()
        monitor.pending_uploads.return_value = ["/out/a"]
        executor.api.mark_job_completed = MagicMock()
        with (
            patch.object(JobExecutor, "_remove_output_dir") as mock_rm,
            patch.object(JobExecutor, "_record_job"),
            patch.object(executor_module, "clear_running_job") as mock_clear,
        ):
            executor._finalize_job("j", "img", "/out", monitor, 0.0, True)
        mock_rm.assert_not_called()
        mock_clear.assert_called_once_with("j")

    def test_failure_marks_failed(self, executor):
        monitor = MagicMock()
        monitor.pending_uploads.return_value = []
        executor.api.mark_job_failed = MagicMock()
        with (
            patch.object(JobExecutor, "_remove_output_dir", return_value=True),
            patch.object(JobExecutor, "_record_job"),
            patch.object(executor_module, "clear_running_job") as mock_clear,
        ):
            executor._finalize_job("j", "img", "/out", monitor, 0.0, False,
                                   "user", "bad code")
        executor.api.mark_job_failed.assert_called_once_with("j", "user", "bad code")
        mock_clear.assert_called_once_with("j")


class TestFlushAndAppendLogs:
    def test_flush_empty_noop(self, executor):
        executor.api.send_logs = MagicMock()
        executor._flush_log_push("j")
        executor.api.send_logs.assert_not_called()

    def test_flush_throttled(self, executor):
        executor.api.send_logs = MagicMock()
        state = executor._get_job_log_state("j")
        state.log_push_buffer = ["a"]
        state.last_log_push = 99.0
        with (
            patch.object(executor_module.time, "monotonic", return_value=100.0),
            patch.object(executor_module.runtime_config, "get", return_value=60.0),
        ):
            executor._flush_log_push("j")
            executor.api.send_logs.assert_not_called()
            executor._flush_log_push("j", force=True)
            executor.api.send_logs.assert_called_once()

    def test_failed_push_keeps_lines_for_retry(self, executor):
        state = executor._get_job_log_state("j")
        state.log_push_buffer = ["a", "b"]
        executor.api.send_logs.return_value = False

        executor._flush_log_push("j", force=True)

        assert state.log_push_buffer == ["a", "b"]

    def test_append_empty_noop(self, executor):
        store = MagicMock()
        executor._append_build_log("j", store, [])
        store.upload_bytes.assert_not_called()

    def test_append_uploads_with_base(self, executor):
        store = MagicMock()
        store.download.return_value = "base-line".encode()
        store.upload_bytes.return_value = True
        with patch.object(executor_module.runtime_config, "get", return_value=60.0):
            state = executor._get_job_log_state("j")
            state.build_log_base = None
            executor._append_build_log("j", store, ["l1", "l2"], force=True)
        assert store.upload_bytes.call_args[0][0] == "j/training.log"
        content = store.upload_bytes.call_args[0][1].decode()
        assert "base-line" in content and "l1" in content

    def test_append_seeds_from_training_log_first(self, executor):
        store = MagicMock()
        store.download.side_effect = lambda key: "train-base".encode() if key == "j/training.log" else None
        store.upload_bytes.return_value = True
        with patch.object(executor_module.runtime_config, "get", return_value=60.0):
            state = executor._get_job_log_state("j")
            state.build_log_base = None
            executor._append_build_log("j", store, ["l1"], force=True)
        assert store.download.call_args_list[0][0][0] == "j/training.log"
        assert "train-base" in store.upload_bytes.call_args[0][1].decode()

    def test_append_failure_keeps_last_upload_none(self, executor):
        store = MagicMock()
        store.download.return_value = None
        store.upload_bytes.return_value = False
        with patch.object(executor_module.runtime_config, "get", return_value=60.0):
            state = executor._get_job_log_state("j")
            state.build_log_base = None
            state.last_log_upload = None
            executor._append_build_log("j", store, ["l1"], force=True)
        assert state.last_log_upload is None


class TestProcessJob:
    def test_uses_attempt_specific_image_tag_from_scheduler(self, executor):
        with (
            patch.object(JobExecutor, "pull_docker_image", return_value=True) as pull,
            patch.object(executor_module, "save_running_job"),
            patch.object(JobExecutor, "handle_training"),
        ):
            executor.process_job({
                "id": "j1",
                "flag": "training",
                "image_tag": "repo/j1:build-attempt-1",
            })
        pull.assert_called_once_with("repo/j1:build-attempt-1")

    def test_pull_failure_marks_failed(self, executor):
        executor.api.mark_job_failed = MagicMock()
        with patch.object(JobExecutor, "pull_docker_image", return_value=False):
            executor.process_job({"id": "j1", "flag": "training"})
        executor.api.mark_job_failed.assert_called_once()

    def test_training_flag(self, executor):
        with (
            patch.object(JobExecutor, "pull_docker_image", return_value=True),
            patch.object(executor_module, "save_running_job"),
            patch.object(JobExecutor, "handle_training") as mock_train,
        ):
            executor.process_job({"id": "j1", "flag": "training"})
        mock_train.assert_called_once()

    def test_training_flag_forwards_entry_command(self, executor):
        with (
            patch.object(JobExecutor, "pull_docker_image", return_value=True),
            patch.object(executor_module, "save_running_job"),
            patch.object(JobExecutor, "handle_training") as mock_train,
        ):
            executor.process_job({
                "id": "j1",
                "flag": "training",
                "image_tag": "repo/j1:build-attempt-1",
                "command": "python train.py",
            })
        mock_train.assert_called_once_with(
            "j1", "repo/j1:build-attempt-1", "python train.py"
        )

    def test_vram_flag(self, executor):
        with (
            patch.object(JobExecutor, "pull_docker_image", return_value=True),
            patch.object(JobExecutor, "handle_vram_estimation") as mock_est,
        ):
            executor.process_job(
                {"id": "j1", "flag": "vram_estimation", "command": "python t.py"}
            )
        mock_est.assert_called_once()

    def test_retry_flag(self, executor):
        with (
            patch.object(JobExecutor, "pull_docker_image", return_value=True),
            patch.object(executor_module, "save_running_job"),
            patch.object(JobExecutor, "handle_retry") as mock_retry,
        ):
            executor.process_job({"id": "j1", "flag": "retry"})
        mock_retry.assert_called_once()

    def test_unknown_flag_warns(self, executor):
        with (
            patch.object(JobExecutor, "pull_docker_image", return_value=True),
            patch.object(JobExecutor, "handle_training") as mock_train,
        ):
            executor.process_job({"id": "j1", "flag": "bogus"})
        mock_train.assert_not_called()

    def test_unsafe_job_id_releases_reserved_capacity(self, executor):
        assert executor.try_begin_job("../bad", 1.0) is True
        executor.process_job({"id": "../bad", "flag": "training"})
        assert executor.active_jobs_count == 0


class TestResumePersisted:
    def test_no_state_returns_false(self, executor):
        with patch.object(executor_module, "load_running_job", return_value=None):
            assert executor.resume_persisted_job_if_any() is False

    def test_missing_job_id_clears(self, executor):
        with (
            patch.object(executor_module, "load_running_jobs", return_value=[]),
            patch.object(executor_module, "load_running_job", return_value={"x": 1}),
            patch.object(executor_module, "clear_running_job") as mock_clear,
        ):
            assert executor.resume_persisted_job_if_any() is False
        mock_clear.assert_called_once()

    def test_scheduler_error_keeps_state(self, executor):
        executor.api.resume_job = MagicMock(side_effect=Exception("down"))
        with (
            patch.object(executor_module, "load_running_job", return_value={"job_id": "j1"}),
            patch.object(executor_module, "get_gpu_info", return_value=("A100", 1, 1, 1, 1)),
        ):
            assert executor.resume_persisted_job_if_any() is False

    def test_gone_job_clears_state(self, executor):
        executor.api.resume_job = MagicMock(return_value=None)
        with (
            patch.object(executor_module, "load_running_job", return_value={"job_id": "j1"}),
            patch.object(executor_module, "get_gpu_info", return_value=("A100", 1, 1, 1, 1)),
            patch.object(executor_module, "clear_running_job") as mock_clear,
        ):
            assert executor.resume_persisted_job_if_any() is False
        mock_clear.assert_called_once()

    def test_pull_failure_clears_and_false(self, executor):
        executor.api.resume_job = MagicMock(return_value={"resume_command": "r"})
        with (
            patch.object(executor_module, "load_running_job", return_value={"job_id": "j1"}),
            patch.object(executor_module, "get_gpu_info", return_value=("A100", 1, 1, 1, 1)),
            patch.object(JobExecutor, "pull_docker_image", return_value=False),
            patch.object(executor_module, "clear_running_job") as mock_clear,
        ):
            assert executor.resume_persisted_job_if_any() is False
        mock_clear.assert_called_once()
        executor.api.mark_job_failed.assert_called_once()

    def test_success_resumes(self, executor):
        executor.api.resume_job = MagicMock(
            return_value={"resume_command": "r", "command": "c"}
        )
        with (
            patch.object(executor_module, "load_running_job", return_value={"job_id": "j1"}),
            patch.object(executor_module, "get_gpu_info", return_value=("A100", 1, 1, 1, 1)),
            patch.object(JobExecutor, "pull_docker_image", return_value=True),
            patch.object(JobExecutor, "handle_retry") as mock_retry,
        ):
            assert executor.resume_persisted_job_if_any() is True
        mock_retry.assert_called_once()

    def test_malformed_entry_does_not_wipe_all_state(self, executor):
        executor.api.resume_job = MagicMock(return_value=None)
        with (
            patch.object(executor_module, "load_running_jobs",
                         return_value=[{"saved_at": 0.0},
                                       {"job_id": "j2", "saved_at": 0.0}]),
            patch.object(executor_module, "get_gpu_info",
                         return_value=("A100", 1, 1, 1, 1)),
            patch.object(executor_module, "clear_running_job") as mock_clear,
        ):
            executor.resume_persisted_job_if_any()
        # The malformed entry must never trigger a clear-all.
        assert not any(
            call.args and call.args[0] is None
            for call in mock_clear.call_args_list
        )
        assert ("j2",) in [call.args for call in mock_clear.call_args_list]


class TestConcurrentExecution:
    def test_active_jobs_count_starts_zero(self, executor):
        assert executor.active_jobs_count == 0

    def test_register_and_unregister_job(self, executor):
        executor._register_job("j1", 2.0)
        assert executor.active_jobs_count == 1
        assert executor.is_job_active("j1")
        executor._unregister_job("j1")
        assert executor.active_jobs_count == 0
        assert not executor.is_job_active("j1")

    def test_multiple_active_jobs(self, executor):
        executor._register_job("j1", 2.0)
        executor._register_job("j2", 4.0)
        assert executor.active_jobs_count == 2
        assert executor.is_job_active("j1")
        assert executor.is_job_active("j2")
        executor._unregister_job("j1")
        assert executor.active_jobs_count == 1
        assert not executor.is_job_active("j1")
        assert executor.is_job_active("j2")
        executor._unregister_job("j2")
        assert executor.active_jobs_count == 0

    def test_get_effective_free_vram(self, executor):
        """Effective free VRAM should subtract active jobs' requirements
        when GPUtil hasn't yet reflected their allocations."""
        executor._register_job("j1", 2.0)
        executor._register_job("j2", 3.0)
        # total=5.1, free=10.0 → current_used=5.1-10.0<0 → max(0, -4.9)=0
        # Need active > current_used for an adjustment.
        # total=12.0, free=10.0 → current_used=2.0, active=5.0
        # unobserved = 5.0-2.0 = 3.0, effective = 10.0-3.0 = 7.0
        effective = executor.get_effective_free_vram(10.0, 12.0)
        assert effective == 7.0

    def test_get_effective_free_vram_no_active_jobs(self, executor):
        effective = executor.get_effective_free_vram(10.0, 80.0)
        assert effective == 10.0

    def test_get_effective_free_vram_no_total(self, executor):
        executor._register_job("j1", 2.0)
        effective = executor.get_effective_free_vram(10.0, 0.0)
        assert effective == 8.0

    def test_per_job_log_state_isolation(self, executor):
        """Each job should have independent log state."""
        s1 = executor._get_job_log_state("j1")
        s2 = executor._get_job_log_state("j2")
        assert s1 is not None
        assert s2 is not None
        assert s1 is not s2
        s1.log_push_buffer.append("line from j1")
        s2.log_push_buffer.append("line from j2")
        assert s1.log_push_buffer == ["line from j1"]
        assert s2.log_push_buffer == ["line from j2"]

    def test_drop_job_log_state(self, executor):
        executor._get_job_log_state("j1")
        assert "j1" in executor._job_logs
        executor._drop_job_log_state("j1")
        assert "j1" not in executor._job_logs

    def test_reset_log_state_per_job(self, executor):
        state = executor._get_job_log_state("j1")
        state.log_push_buffer = ["a"]
        state.last_log_push = 100.0
        executor._reset_log_state("j1")
        assert state.log_push_buffer == []
        assert state.last_log_push is None

    def test_has_unresumed_job_true(self, executor):
        with patch.object(executor_module, "load_running_jobs",
                          return_value=[{"job_id": "j1", "saved_at": 0.0}]):
            assert executor.has_unresumed_job() is True

    def test_has_unresumed_job_false_when_active(self, executor):
        executor._register_job("j1", 2.0)
        with patch.object(executor_module, "load_running_jobs",
                          return_value=[{"job_id": "j1", "saved_at": 0.0}]):
            assert executor.has_unresumed_job() is False

    def test_has_unresumed_job_false_when_none(self, executor):
        with (
            patch.object(executor_module, "load_running_jobs", return_value=[]),
            patch.object(executor_module, "load_running_job", return_value=None),
        ):
            assert executor.has_unresumed_job() is False

    def test_process_job_tracks_active(self, executor):
        """process_job should register and unregister job in active_jobs."""
        executor.pull_docker_image = MagicMock(return_value=True)
        executor.handle_training = MagicMock()
        executor.api.mark_job_failed = MagicMock()

        job = {"id": "j1", "flag": "training", "vram_required": 2.0}
        with patch.object(executor_module, "save_running_job"):
            executor.process_job(job)

        assert not executor.is_job_active("j1")
        executor.handle_training.assert_called_once()

    def test_process_job_unregisters_on_failure(self, executor):
        """process_job should unregister job even if pull fails."""
        executor.pull_docker_image = MagicMock(return_value=False)
        executor.api.mark_job_failed = MagicMock()

        job = {"id": "j1", "flag": "training"}
        with patch.object(executor_module, "save_running_job"):
            executor.process_job(job)

        assert not executor.is_job_active("j1")
        executor.api.mark_job_failed.assert_called_once()

    def test_resume_scan_blocks_new_capacity_claims(self, executor):
        assert executor.try_begin_resume_scan() is True
        assert executor.try_begin_resume_scan() is False
        assert executor.try_begin_job("new", 1.0) is False
        executor.end_resume_scan()
        assert executor.try_begin_job("new", 1.0) is True

    def test_unregistered_resume_reservation_temporarily_blocks_polling(self, executor):
        assert executor.begin_resume("restored") is True
        assert executor.active_jobs_count == int(
            executor_module.runtime_config.get("max_concurrent_jobs")
        )
        executor._register_job("restored", 2.0)
        assert executor.active_jobs_count == 1

    def test_success_resumes_releases_capacity(self, executor):
        """successful resume must release the capacity slot."""
        executor.api.resume_job = MagicMock(
            return_value={"resume_command": "r", "command": "c"}
        )
        with (
            patch.object(executor_module, "load_running_job",
                          return_value={"job_id": "j1"}),
            patch.object(executor_module, "get_gpu_info",
                         return_value=("A100", 1, 1, 1, 1)),
            patch.object(JobExecutor, "pull_docker_image", return_value=True),
            patch.object(JobExecutor, "handle_retry"),
        ):
            result = executor.resume_persisted_job_if_any()
        assert result is True
        assert executor.active_jobs_count == 0
        assert executor.get_effective_free_vram(10.0, 80.0) == 10.0

    def test_pull_failure_releases_capacity(self, executor):
        executor.api.resume_job = MagicMock(
            return_value={"resume_command": "r", "command": "c"}
        )
        with (
            patch.object(executor_module, "load_running_job",
                          return_value={"job_id": "j1"}),
            patch.object(executor_module, "get_gpu_info",
                         return_value=("A100", 1, 1, 1, 1)),
            patch.object(JobExecutor, "pull_docker_image", return_value=False),
        ):
            result = executor.resume_persisted_job_if_any()
        assert result is False
        assert executor.active_jobs_count == 0

    def test_exception_releases_capacity(self, executor):
        executor.api.resume_job = MagicMock(
            return_value={"resume_command": "r", "command": "c"}
        )
        with (
            patch.object(executor_module, "load_running_job",
                          return_value={"job_id": "j1"}),
            patch.object(executor_module, "get_gpu_info",
                         return_value=("A100", 1, 1, 1, 1)),
            patch.object(JobExecutor, "pull_docker_image",
                         side_effect=Exception("boom")),
        ):
            result = executor.resume_persisted_job_if_any()
        assert result is False
        assert executor.active_jobs_count == 0

    def test_capacity_freed_after_unregister(self, executor):
        """Unregistering a job must restore effective free VRAM."""
        executor._register_job("j1", 2.0)
        executor._register_job("j2", 3.0)
        assert executor.get_effective_free_vram(10.0, 12.0) == 7.0
        executor._unregister_job("j1")
        executor._unregister_job("j2")
        assert executor.get_effective_free_vram(10.0, 12.0) == 10.0

    def test_multi_job_resume_respects_capacity(self, executor):
        """With 1 occupied slot, only 1 of 2 persisted jobs resumes."""
        executor._register_job("running", 2.0)
        with patch.object(executor_module, "load_running_jobs",
                           return_value=[
                               {"job_id": "j1", "saved_at": 0.0},
                               {"job_id": "j2", "saved_at": 0.0},
                           ]):
            executor.api.resume_job = MagicMock(
                return_value={"resume_command": "r", "command": "c"}
            )
            with (
                patch.object(executor_module, "get_gpu_info",
                             return_value=("A100", 1, 1, 1, 1)),
                patch.object(JobExecutor, "pull_docker_image", return_value=True),
                patch.object(JobExecutor, "handle_retry"),
            ):
                result = executor.resume_persisted_job_if_any()
        assert result is True
        assert executor.active_jobs_count == 1

    def test_multi_job_resume_all_within_capacity(self, executor):
        """With 2 free slots, both persisted jobs resume."""
        with patch.object(executor_module, "load_running_jobs",
                           return_value=[
                               {"job_id": "j1", "saved_at": 0.0},
                               {"job_id": "j2", "saved_at": 0.0},
                           ]):
            executor.api.resume_job = MagicMock(
                side_effect=[
                    {"resume_command": "r1", "command": "c1"},
                    {"resume_command": "r2", "command": "c2"},
                ]
            )
            with (
                patch.object(executor_module, "get_gpu_info",
                             return_value=("A100", 1, 1, 1, 1)),
                patch.object(JobExecutor, "pull_docker_image", return_value=True),
                patch.object(JobExecutor, "handle_retry") as mock_retry,
            ):
                result = executor.resume_persisted_job_if_any()
        assert result is True
        assert mock_retry.call_count == 2

    def test_persisted_jobs_resume_concurrently(self, executor):
        barrier = threading.Barrier(2)

        def wait_for_other_resume(*_args, **_kwargs):
            barrier.wait(timeout=1.0)

        with patch.object(executor_module, "load_running_jobs",
                          return_value=[
                              {"job_id": "j1", "saved_at": 0.0},
                              {"job_id": "j2", "saved_at": 0.0},
                          ]):
            executor.api.resume_job = MagicMock(
                side_effect=[
                    {"resume_command": "r1", "command": "c1"},
                    {"resume_command": "r2", "command": "c2"},
                ]
            )
            with (
                patch.object(executor_module, "get_gpu_info",
                             return_value=("A100", 1, 1, 1, 1)),
                patch.object(JobExecutor, "pull_docker_image", return_value=True),
                patch.object(JobExecutor, "handle_retry",
                             side_effect=wait_for_other_resume),
            ):
                result = executor.resume_persisted_job_if_any()

        assert result is True
        assert barrier.n_waiting == 0

    def test_has_unresumed_job_true_after_partial_resume(self, executor):
        executor._register_job("j1", 2.0)
        with patch.object(executor_module, "load_running_jobs",
                          return_value=[
                              {"job_id": "j1", "saved_at": 0.0},
                              {"job_id": "j2", "saved_at": 0.0},
                          ]):
            assert executor.has_unresumed_job() is True

    def test_max_concurrent_jobs_default(self):
        """max_concurrent_jobs should be available via runtime_config."""
        import runtime_config
        max_jobs = runtime_config.get("max_concurrent_jobs")
        assert max_jobs >= 1.0
        assert isinstance(max_jobs, float)
