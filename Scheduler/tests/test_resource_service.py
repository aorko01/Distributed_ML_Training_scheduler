"""Unit tests for Scheduler/app/services/resource_service.py pure functions."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.resource_service import _int_or_float, _compare, _config_matches  # noqa: E402
from app.schemas.resource_schema import ResourceConfig  # noqa: E402


class TestIntOrFloat:
    def test_none_returns_none(self):
        assert _int_or_float(None) is None

    def test_integer_returns_float(self):
        assert _int_or_float(5) == 5.0
        assert isinstance(_int_or_float(5), float)

    def test_float_returns_float(self):
        assert _int_or_float(5.5) == 5.5

    def test_string_number_returns_float(self):
        assert _int_or_float("3.14") == 3.14

    def test_non_numeric_string_returns_none(self):
        assert _int_or_float("abc") is None

    def test_integer_float_preserves_integer_value(self):
        assert _int_or_float(10.0) == 10.0

    def test_negative_number(self):
        assert _int_or_float(-5) == -5.0

    def test_zero(self):
        assert _int_or_float(0) == 0.0


class TestCompare:
    def test_ge_operator_greater(self):
        assert _compare(10.0, 5.0, "ge") is True

    def test_ge_operator_equal(self):
        assert _compare(5.0, 5.0, "ge") is True

    def test_ge_operator_less(self):
        assert _compare(3.0, 5.0, "ge") is False

    def test_eq_operator_equal(self):
        assert _compare(5.0, 5.0, "eq") is True

    def test_eq_operator_not_equal(self):
        assert _compare(5.0, 3.0, "eq") is False

    def test_eq_operator_close_values(self):
        assert _compare(5.0000000001, 5.0, "eq") is True

    def test_requested_none_always_true(self):
        assert _compare(10.0, None, "ge") is True
        assert _compare(10.0, None, "eq") is True

    def test_actual_none_when_requested_not_none(self):
        assert _compare(None, 5.0, "ge") is False
        assert _compare(None, 5.0, "eq") is False

    def test_both_none(self):
        assert _compare(None, None, "ge") is True


class TestConfigMatches:
    def test_exact_match(self):
        config = ResourceConfig(
            gpu_type="NVIDIA A100",
            gpu_vram=80.0,
            cpu_ram=256.0,
            cpu_cores=32,
            disk=1000.0,
            op="ge",
        )
        assert _config_matches("NVIDIA A100", 80.0, 256.0, 32, 1000.0, config) is True

    def test_vram_mismatch(self):
        config = ResourceConfig(gpu_vram=80.0, op="ge")
        assert _config_matches("NVIDIA A100", 40.0, 256.0, 32, 1000.0, config) is False

    def test_ram_mismatch(self):
        config = ResourceConfig(cpu_ram=256.0, op="ge")
        assert _config_matches("NVIDIA A100", 80.0, 128.0, 32, 1000.0, config) is False

    def test_cpu_mismatch(self):
        config = ResourceConfig(cpu_cores=32, op="ge")
        assert _config_matches("NVIDIA A100", 80.0, 256.0, 16, 1000.0, config) is False

    def test_disk_mismatch(self):
        config = ResourceConfig(disk=1000.0, op="ge")
        assert _config_matches("NVIDIA A100", 80.0, 256.0, 32, 500.0, config) is False

    def test_gpu_type_mismatch(self):
        config = ResourceConfig(gpu_type="NVIDIA V100", op="ge")
        assert _config_matches("NVIDIA A100", 80.0, 256.0, 32, 1000.0, config) is False

    def test_no_gpu_type_filter(self):
        config = ResourceConfig(gpu_type=None, gpu_vram=80.0, op="ge")
        assert _config_matches("NVIDIA A100", 80.0, 256.0, 32, 1000.0, config) is True

    def test_eq_operator(self):
        config = ResourceConfig(gpu_vram=80.0, op="eq")
        assert _config_matches("NVIDIA A100", 80.0, 256.0, 32, 1000.0, config) is True

    def test_eq_operator_close_enough(self):
        config = ResourceConfig(gpu_vram=80.0, op="eq")
        # The tolerance is 1e-9, so 80.000000001 should be close enough
        assert _config_matches("NVIDIA A100", 80.0000000001, 256.0, 32, 1000.0, config) is True

    def test_all_none_values(self):
        config = ResourceConfig(gpu_vram=None, cpu_ram=None, cpu_cores=None, disk=None, op="ge")
        assert _config_matches("NVIDIA A100", None, None, None, None, config) is True
