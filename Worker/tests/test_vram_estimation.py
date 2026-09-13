"""Unit tests for vram_estimation.py (CUDA probe run inside the container).

``torch`` is intentionally *not* installed in the worker environment under
test, so a minimal fake ``torch`` module is injected into ``sys.modules``
before ``vram_estimation`` is imported.
"""
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _install_fake_torch(peak_gb=2.0):
    fake_torch = types.ModuleType("torch")

    class _Cuda:
        def is_available(self):
            return True

        def synchronize(self):
            pass

        def max_memory_reserved(self):
            return peak_gb * 1e9

        def reset_peak_memory_stats(self):
            pass

    class _Optimizer:
        def __init__(self, params=None):
            self.params = params

        def step(self, *args, **kwargs):
            return True

    fake_torch.cuda = _Cuda()
    fake_torch.optim = SimpleNamespace(Optimizer=_Optimizer)
    sys.modules["torch"] = fake_torch
    return fake_torch


_fake_torch = _install_fake_torch()

import vram_estimation  # noqa: E402
from vram_estimation import ProbeDone, estimate  # noqa: E402


def _run_steps(n):
    def _fake_run_path(target, run_name=None):
        optimizer = _fake_torch.optim.Optimizer()
        for _ in range(n):
            optimizer.step()

    return _fake_run_path


class TestEstimate:
    def test_cuda_unavailable_raises(self):
        with (
            patch.object(_fake_torch.cuda, "is_available", return_value=False),
            patch.object(vram_estimation.runpy, "run_path"),
        ):
            with pytest.raises(RuntimeError, match="CUDA is not available"):
                estimate("target.py", [])

    def test_early_stop_reports_metrics(self):
        with patch.object(
            vram_estimation.runpy, "run_path", side_effect=_run_steps(50)
        ):
            report = estimate("target.py", ["--epochs", "5"])
        assert report["peak_reserved_memory"] == pytest.approx(2.0)
        assert report["peak_ram_memory"] > 0
        assert report["step_wall_time"] is not None

    def test_no_steps_gives_none_step_time(self):
        with patch.object(
            vram_estimation.runpy, "run_path", side_effect=_run_steps(0)
        ):
            report = estimate("target.py", [])
        assert report["step_wall_time"] is None
        assert report["peak_reserved_memory"] == pytest.approx(2.0)

    def test_system_exit_swallowed(self):
        def _exit(target, run_name=None):
            raise SystemExit(0)

        with patch.object(vram_estimation.runpy, "run_path", side_effect=_exit):
            report = estimate("target.py", [])
        assert report["step_wall_time"] is None

    def test_optimizer_patched_and_restored(self):
        original_init = _fake_torch.optim.Optimizer.__init__
        with patch.object(
            vram_estimation.runpy, "run_path", side_effect=_run_steps(3)
        ):
            estimate("target.py", [])
        assert _fake_torch.optim.Optimizer.__init__ is original_init

    def test_target_args_forwarded(self):
        seen = {}

        def _capture(target, run_name=None):
            seen["argv"] = list(vram_estimation.sys.argv)

        with patch.object(vram_estimation.runpy, "run_path", side_effect=_capture):
            estimate("my_train.py", ["--lr", "0.01"])
        assert seen["argv"][0] == "my_train.py"
        assert "--lr" in seen["argv"]


class TestMain:
    def test_main_writes_report(self, tmp_path):
        out = tmp_path / "report.json"
        report = {"peak_reserved_memory": 1.0, "peak_ram_memory": 2.0,
                  "step_wall_time": 0.5}
        argv = ["prog", "--output", str(out), "train.py", "--epochs", "3"]
        with (
            patch.object(vram_estimation.sys, "argv", argv),
            patch.object(vram_estimation, "estimate", return_value=report) as mock_est,
        ):
            vram_estimation.main()
        mock_est.assert_called_once()
        assert json.loads(out.read_text()) == report
