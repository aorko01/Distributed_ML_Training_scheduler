"""Unit tests for Scheduler/app/services/worker_service.py _apply_worker_metrics."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.worker_service import _apply_worker_metrics  # noqa: E402


class TestApplyWorkerMetrics:
    def test_applies_hostname(self, sample_worker):
        _apply_worker_metrics(sample_worker, {"hostname": "new-host"})
        # MagicMock supports setattr, so this should work
        assert getattr(sample_worker, "hostname") == "new-host"

    def test_applies_ip_address(self, sample_worker):
        _apply_worker_metrics(sample_worker, {"ip_address": "10.0.0.1"})
        assert getattr(sample_worker, "ip_address") == "10.0.0.1"

    def test_applies_available_vram(self, sample_worker):
        _apply_worker_metrics(sample_worker, {"available_vram": 500.0})
        assert getattr(sample_worker, "available_vram") == 500.0

    def test_applies_gpus_in_use(self, sample_worker):
        _apply_worker_metrics(sample_worker, {"gpus_in_use": 4})
        assert getattr(sample_worker, "gpus_in_use") == 4

    def test_applies_gpu_load(self, sample_worker):
        _apply_worker_metrics(sample_worker, {"gpu_load": 75.5})
        assert getattr(sample_worker, "gpu_load") == 75.5

    def test_applies_cpu_load(self, sample_worker):
        _apply_worker_metrics(sample_worker, {"cpu_load": 50.0})
        assert getattr(sample_worker, "cpu_load") == 50.0

    def test_applies_mem_usage(self, sample_worker):
        _apply_worker_metrics(sample_worker, {"mem_usage": 80.0})
        assert getattr(sample_worker, "mem_usage") == 80.0

    def test_applies_cpu_cores(self, sample_worker):
        _apply_worker_metrics(sample_worker, {"cpu_cores": 128})
        assert getattr(sample_worker, "cpu_cores") == 128

    def test_applies_total_ram(self, sample_worker):
        _apply_worker_metrics(sample_worker, {"total_ram": 1024.0})
        assert getattr(sample_worker, "total_ram") == 1024.0

    def test_applies_total_disk(self, sample_worker):
        _apply_worker_metrics(sample_worker, {"total_disk": 4000.0})
        assert getattr(sample_worker, "total_disk") == 4000.0

    def test_applies_available_disk(self, sample_worker):
        _apply_worker_metrics(sample_worker, {"available_disk": 3000.0})
        assert getattr(sample_worker, "available_disk") == 3000.0

    def test_ignores_none_values(self, sample_worker):
        original_hostname = getattr(sample_worker, "hostname")
        _apply_worker_metrics(sample_worker, {"hostname": None})
        assert getattr(sample_worker, "hostname") == original_hostname

    def test_ignores_unknown_fields(self, sample_worker):
        _apply_worker_metrics(sample_worker, {"unknown_field": "value"})
        # The function only sets known fields, so unknown_field should not exist
        assert not hasattr(sample_worker, "unknown_field")

    def test_applies_multiple_fields(self, sample_worker):
        _apply_worker_metrics(sample_worker, {
            "hostname": "multi-host",
            "ip_address": "10.0.0.2",
            "available_vram": 400.0,
            "gpu_load": 60.0,
        })
        assert getattr(sample_worker, "hostname") == "multi-host"
        assert getattr(sample_worker, "ip_address") == "10.0.0.2"
        assert getattr(sample_worker, "available_vram") == 400.0
        assert getattr(sample_worker, "gpu_load") == 60.0
