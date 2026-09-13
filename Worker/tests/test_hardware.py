"""Unit tests for hardware.py (host/GPU introspection)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

import pytest

import hardware


class _GPU:
    def __init__(self, name="NVIDIA A100", total=81920, free=40960, load=0.5,
                 used=40960, temp=65.0):
        self.name = name
        self.memoryTotal = total
        self.memoryFree = free
        self.load = load
        self.memoryUsed = used
        self.temperature = temp


class TestWorkerId:
    def test_reads_existing(self, tmp_path):
        f = tmp_path / "worker_id.txt"
        f.write_text("  existing-id\n")
        with patch.object(hardware, "WORKER_ID_FILE", str(f)):
            assert hardware.get_or_create_worker_id() == "existing-id"

    def test_creates_and_persists(self, tmp_path):
        f = tmp_path / "worker_id.txt"
        with patch.object(hardware, "WORKER_ID_FILE", str(f)):
            first = hardware.get_or_create_worker_id()
            assert hardware.get_or_create_worker_id() == first
            assert f.read_text() == first


class TestHostnameAndIp:
    def test_hostname(self):
        with patch.object(hardware.socket, "gethostname", return_value="host1"):
            assert hardware.get_hostname() == "host1"

    def test_ip_success(self):
        sock = MagicMock()
        sock.getsockname.return_value = ("10.0.0.5", 1234)
        with patch.object(hardware.socket, "socket", return_value=sock):
            assert hardware.get_ip_address() == "10.0.0.5"
        sock.close.assert_called_once()

    def test_ip_failure_returns_zeros(self):
        with patch.object(hardware.socket, "socket", side_effect=Exception("no net")):
            assert hardware.get_ip_address() == "0.0.0.0"


class TestCpu:
    def test_windows_uses_psutil(self):
        with (
            patch.object(hardware.os, "name", "nt"),
            patch.object(hardware.psutil, "cpu_percent", return_value=150.0),
        ):
            assert hardware.get_cpu_load() == 100.0

    def test_posix_uses_loadavg(self):
        with (
            patch.object(hardware.os, "name", "posix"),
            patch.object(hardware.os, "getloadavg", return_value=(4.0, 0, 0)),
            patch.object(hardware.os, "cpu_count", return_value=8),
        ):
            assert hardware.get_cpu_load() == 50.0

    def test_cpu_load_exception_returns_zero(self):
        with patch.object(hardware.os, "getloadavg", side_effect=Exception("x")):
            assert hardware.get_cpu_load() == 0.0

    def test_cpu_cores(self):
        with patch.object(hardware.os, "cpu_count", return_value=16):
            assert hardware.get_cpu_cores() == 16
        with patch.object(hardware.os, "cpu_count", side_effect=Exception("x")):
            assert hardware.get_cpu_cores() == 0


class TestMemUsage:
    def test_psutil_path(self):
        mem = SimpleNamespace(percent=42.5)
        with patch.object(hardware.psutil, "virtual_memory", return_value=mem):
            assert hardware.get_mem_usage() == 42.5

    def test_psutil_clamped(self):
        mem = SimpleNamespace(percent=999.0)
        with patch.object(hardware.psutil, "virtual_memory", return_value=mem):
            assert hardware.get_mem_usage() == 100.0

    def test_proc_meminfo_fallback(self):
        content = "MemTotal:       1000 kB\nMemAvailable:    400 kB\n"
        with (
            patch.object(hardware.psutil, "virtual_memory", side_effect=Exception("x")),
            patch.object(hardware.os, "name", "posix"),
            patch.object(hardware.os.path, "exists", return_value=True),
            patch("builtins.open", mock_open(read_data=content)),
        ):
            assert hardware.get_mem_usage() == 60.0

    def test_no_meminfo_returns_zero(self):
        with (
            patch.object(hardware.psutil, "virtual_memory", side_effect=Exception("x")),
            patch.object(hardware.os, "name", "nt"),
        ):
            assert hardware.get_mem_usage() == 0.0


class TestGpuInfo:
    def test_no_gpus(self):
        with patch.object(hardware.GPUtil, "getGPUs", return_value=[]):
            assert hardware.get_gpu_info() == ("Unknown", 0.0, 0.0, 0, 0.0)

    def test_single_gpu(self):
        with patch.object(hardware.GPUtil, "getGPUs", return_value=[_GPU()]):
            name, total, free, num, load = hardware.get_gpu_info()
        assert name == "NVIDIA A100"
        assert total == 80.0
        assert free == 40.0
        assert num == 1
        assert load == 50.0

    def test_picks_largest_total(self):
        gpus = [_GPU(total=40960, free=1000, load=0.2), _GPU(total=81920, free=80000, load=0.8)]
        with patch.object(hardware.GPUtil, "getGPUs", return_value=gpus):
            _, total, _, num, load = hardware.get_gpu_info()
        assert total == 80.0
        assert num == 2
        assert load == 50.0


class TestGpusInUse:
    def test_thresholds(self):
        gpus = [
            _GPU(load=0.5, used=100),      # busy (load)
            _GPU(load=0.0, used=128),      # busy (memory)
            _GPU(load=0.0, used=10),       # idle
        ]
        with patch.object(hardware.GPUtil, "getGPUs", return_value=gpus):
            assert hardware.count_gpus_in_use() == 2

    def test_exception_returns_zero(self):
        with patch.object(hardware.GPUtil, "getGPUs", side_effect=Exception("x")):
            assert hardware.count_gpus_in_use() == 0


class TestGpusInfoList:
    def test_per_gpu_dict(self):
        with patch.object(hardware.GPUtil, "getGPUs", return_value=[_GPU()]):
            out = hardware.get_gpus_info()
        assert len(out) == 1
        gpu = out[0]
        assert gpu["index"] == 0
        assert gpu["vramTotalGb"] == 80.0
        assert gpu["vramFreeGb"] == 40.0
        assert gpu["vramUsedGb"] == 40.0
        assert gpu["temperatureC"] == 65.0
        assert gpu["inUse"] is True

    def test_exception_returns_empty(self):
        with patch.object(hardware.GPUtil, "getGPUs", side_effect=Exception("x")):
            assert hardware.get_gpus_info() == []


class TestTemperature:
    def test_average(self):
        with patch.object(
            hardware.GPUtil, "getGPUs", return_value=[_GPU(temp=60.0), _GPU(temp=80.0)]
        ):
            assert hardware.get_gpu_temperature() == 70.0

    def test_no_temps_returns_zero(self):
        with patch.object(hardware.GPUtil, "getGPUs", return_value=[_GPU(temp=0)]):
            assert hardware.get_gpu_temperature() == 0.0
        with patch.object(hardware.GPUtil, "getGPUs", return_value=[]):
            assert hardware.get_gpu_temperature() == 0.0


class TestDiskMemOs:
    def test_mem_total(self):
        mem = SimpleNamespace(total=8 * 1024**3)
        with patch.object(hardware.psutil, "virtual_memory", return_value=mem):
            assert hardware.get_mem_total_gb() == 8.0

    def test_mem_total_exception(self):
        with patch.object(hardware.psutil, "virtual_memory", side_effect=Exception()):
            assert hardware.get_mem_total_gb() == 0.0

    def test_disk_info(self):
        usage = SimpleNamespace(total=100 * 1024**3, free=40 * 1024**3)
        with patch.object(hardware.shutil, "disk_usage", return_value=usage):
            assert hardware.get_disk_info() == (100.0, 40.0)

    def test_disk_info_exception(self):
        with patch.object(hardware.shutil, "disk_usage", side_effect=Exception()):
            assert hardware.get_disk_info() == (0.0, 0.0)

    def test_docker_available(self):
        with patch.object(hardware.shutil, "which", return_value="/usr/bin/docker"):
            assert hardware.docker_available() is True
        with patch.object(hardware.shutil, "which", return_value=None):
            assert hardware.docker_available() is False

    def test_cuda_available_via_nvidia_smi(self):
        with patch.object(hardware.shutil, "which", return_value="/usr/bin/nvidia-smi"):
            assert hardware.cuda_available() is True

    def test_cuda_available_via_gpus(self):
        with (
            patch.object(hardware.shutil, "which", return_value=None),
            patch.object(hardware.GPUtil, "getGPUs", return_value=[_GPU()]),
        ):
            assert hardware.cuda_available() is True

    def test_cuda_unavailable(self):
        with (
            patch.object(hardware.shutil, "which", return_value=None),
            patch.object(hardware.GPUtil, "getGPUs", return_value=[]),
        ):
            assert hardware.cuda_available() is False

    def test_os_info_linux(self):
        with (
            patch.object(hardware.platform, "system", return_value="Linux"),
            patch.object(hardware.platform, "release", return_value="6.1"),
        ):
            assert hardware.get_os_info() == "Linux 6.1"

    def test_os_info_macos(self):
        with (
            patch.object(hardware.platform, "system", return_value="Darwin"),
            patch.object(hardware.platform, "mac_ver", return_value=("14.0", "", "")),
        ):
            assert hardware.get_os_info() == "macOS 14.0"


class TestCollectNodeInfo:
    def test_keys_present(self):
        with (
            patch.object(hardware, "get_hostname", return_value="h"),
            patch.object(hardware, "get_ip_address", return_value="1.2.3.4"),
            patch.object(hardware, "get_cpu_load", return_value=10.0),
            patch.object(hardware, "get_mem_usage", return_value=20.0),
            patch.object(hardware, "get_cpu_cores", return_value=8),
            patch.object(hardware, "get_mem_total_gb", return_value=32.0),
            patch.object(hardware, "get_disk_info", return_value=(500.0, 250.0)),
        ):
            info = hardware.collect_node_info()
        assert info == {
            "hostname": "h", "ip_address": "1.2.3.4", "cpu_load": 10.0,
            "mem_usage": 20.0, "cpu_cores": 8, "total_ram": 32.0,
            "total_disk": 500.0, "available_disk": 250.0,
        }
