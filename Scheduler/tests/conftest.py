"""Shared pytest fixtures for Scheduler unit tests."""

import sys
import os

# Set a dummy DATABASE_URL before any app imports so the db module doesn't
# try to connect to a real database during import.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

# Ensure the ``app`` package is importable when running from ``Scheduler/``
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402


@pytest.fixture
def sample_job():
    """Create a sample Job instance for testing."""
    job = MagicMock()
    job.id = "test-job-123"
    job.user_id = "user-456"
    job.object_key = "user-456/test.zip"
    job.name = "test-training-job"
    job.command = "python train.py"
    job.resume_command = "python train.py --resume"
    job.docker_base_image = "pytorch/pytorch:2.0.0-cuda11.7-cudnn8-runtime"
    job.config = {"lr": 0.001, "epochs": 10}
    job.status = MagicMock()
    job.status.value = "IN_PROGRESS"
    job.priority = MagicMock()
    job.priority.value = "NORMAL"
    job.reason_for_priority = None
    job.vram_required = 8.0
    job.ram_required = 16.0
    job.step_time = 0.5
    job.gpu_hour = 2.5
    job.started_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    job.created_at = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    job.updated_at = datetime(2024, 1, 1, 14, 0, 0, tzinfo=timezone.utc)
    job.device = "NVIDIA A100"
    job.failure_reason = None
    job.build_type = "training"
    job.base_job_id = None
    return job


@pytest.fixture
def sample_worker():
    """Create a sample Worker instance for testing."""
    # Create a simple class instead of MagicMock to support setattr properly
    class MockWorker:
        def __init__(self):
            self.worker_id = "worker-789"
            self.gpu_type = "NVIDIA A100"
            self.num_gpus = 8
            self.total_vram = 640.0
            self.gpus_in_use = 2
            self.available_vram = 480.0
            self.hostname = "gpu-node-01"
            self.ip_address = "192.168.1.100"
            self.gpu_load = 45.5
            self.cpu_load = 30.2
            self.mem_usage = 65.8
            self.cpu_cores = 64
            self.total_ram = 512.0
            self.total_disk = 2000.0
            self.available_disk = 1500.0
            self.first_seen = datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
            self.last_registered = datetime(2024, 1, 1, 14, 0, 0, tzinfo=timezone.utc)

    return MockWorker()


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return MagicMock()
