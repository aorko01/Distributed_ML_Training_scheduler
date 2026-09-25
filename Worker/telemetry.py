import logging
import threading
import time

_lock = threading.Lock()
_job_history: list[dict] = []
_events: list[dict] = []
_worker_logs: list[dict] = []
_last_heartbeat_success: float | None = None
_last_heartbeat_error: str | None = None
_paused: bool = False
_logging_capture_installed = False
MAX_JOB_HISTORY = 50
MAX_EVENTS = 100
MAX_WORKER_LOGS = 500


class _WorkerLogHandler(logging.Handler):
    """Keep a small UI-facing copy of Worker logs.

    This handler is attached to Python's root logger, so it captures the
    Worker service itself but never reads Docker/container stdout or logs.
    """

    def emit(self, record: logging.LogRecord):
        try:
            message = record.getMessage()
            # JobExecutor mirrors training-container stdout through this exact
            # logger format for Scheduler delivery. The desktop console is an
            # operator view, not a container-log viewer, so discard it here.
            if record.name == "executor" and message.startswith("[job "):
                return
            item = {
                "timestamp": time.strftime(
                    "%Y-%m-%dT%H:%M:%S%z", time.localtime(record.created)
                ),
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": message,
            }
            with _lock:
                _worker_logs.append(item)
                del _worker_logs[:-MAX_WORKER_LOGS]
        except Exception:
            # Logging must never be able to interrupt scheduling/execution.
            self.handleError(record)


def install_logging_capture():
    global _logging_capture_installed
    with _lock:
        if _logging_capture_installed:
            return
        _logging_capture_installed = True
    handler = _WorkerLogHandler(level=logging.INFO)
    logging.getLogger().addHandler(handler)


def _append_event_locked(level: str, message: str):
    global _events
    _events.append({
        "time": time.strftime("%H:%M:%S", time.localtime()),
        "level": level,
        "message": message,
    })
    _events = _events[-MAX_EVENTS:]


def record_job(job: dict):
    global _job_history
    with _lock:
        _job_history.append(job)
        _job_history = _job_history[-MAX_JOB_HISTORY:]
        _append_event_locked(
            "success" if job.get("status") == "completed" else "error",
            f"Job {job.get('id', '?')} {job.get('status', '?')}",
        )


def record_event(level: str, message: str):
    with _lock:
        _append_event_locked(level, message)


def record_heartbeat(ok: bool, error: str | None = None):
    global _last_heartbeat_success, _last_heartbeat_error
    with _lock:
        _last_heartbeat_success = time.time() if ok else _last_heartbeat_success
        _last_heartbeat_error = error if not ok else None
        _append_event_locked(
            "success" if ok else "error",
            "Heartbeat sent" if ok else f"Heartbeat failed: {error}" if error else "Heartbeat failed",
        )


def set_paused(value: bool):
    global _paused
    with _lock:
        _paused = bool(value)


def is_paused() -> bool:
    with _lock:
        return _paused


def get_jobs() -> list[dict]:
    with _lock:
        return list(_job_history)


def get_events() -> list[dict]:
    with _lock:
        return list(_events)


def get_worker_logs(limit: int = 200) -> list[dict]:
    with _lock:
        safe_limit = max(1, min(int(limit), MAX_WORKER_LOGS))
        return list(_worker_logs[-safe_limit:])


def get_heartbeat() -> dict:
    with _lock:
        return {
            "last_success_at": _last_heartbeat_success,
            "last_error": _last_heartbeat_error,
            "paused": _paused,
        }
