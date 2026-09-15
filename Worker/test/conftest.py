"""Shared fixtures for Worker unit tests.

- Sets ``SCHEDULER_URL`` *before* any local module is imported
  (``config.py`` raises ``ValueError`` at import time when it is missing).
- Puts the ``Worker/`` directory on ``sys.path`` so the flat
  ``import config`` / ``import api`` style imports work from anywhere.
- Provides fixtures that snapshot/restore mutable global state
  (``runtime_config._values``, ``telemetry`` globals, scheduler URL).
"""
import os
import sys

import pytest

os.environ.setdefault("SCHEDULER_URL", "http://localhost:8000")
os.environ.setdefault("OBJECT_STORE_URL", "http://localhost:8010")

WORKER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKER_DIR not in sys.path:
    sys.path.insert(0, WORKER_DIR)


@pytest.fixture()
def restore_scheduler_url():
    import config

    original = config.get_scheduler_url()
    yield
    config.set_scheduler_url(original)


@pytest.fixture()
def restore_runtime_config():
    import runtime_config

    original = runtime_config.all()
    yield
    with runtime_config._lock:
        runtime_config._values.clear()
        runtime_config._values.update(original)


@pytest.fixture()
def reset_telemetry():
    import telemetry

    with telemetry._lock:
        old_jobs = list(telemetry._job_history)
        old_events = list(telemetry._events)
        old_success = telemetry._last_heartbeat_success
        old_error = telemetry._last_heartbeat_error
        old_paused = telemetry._paused
    yield
    with telemetry._lock:
        telemetry._job_history = old_jobs
        telemetry._events = old_events
        telemetry._last_heartbeat_success = old_success
        telemetry._last_heartbeat_error = old_error
        telemetry._paused = old_paused


@pytest.fixture()
def mock_api():
    from unittest.mock import MagicMock

    return MagicMock()


@pytest.fixture()
def executor(mock_api):
    """JobExecutor with docker.from_env mocked out."""
    from unittest.mock import MagicMock, patch

    with patch("executor.docker.from_env", return_value=MagicMock()):
        from executor import JobExecutor

        ex = JobExecutor(mock_api)
    return ex
