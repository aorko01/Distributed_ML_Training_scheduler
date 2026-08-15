import threading
from config import HEARTBEAT_INTERVAL, JOB_POLL_INTERVAL, LOG_PUSH_INTERVAL, LOG_UPLOAD_INTERVAL

_lock = threading.Lock()
_values = {
    "heartbeat_interval": float(HEARTBEAT_INTERVAL),
    "job_poll_interval": float(JOB_POLL_INTERVAL),
    "log_push_interval": float(LOG_PUSH_INTERVAL),
    "log_upload_interval": float(LOG_UPLOAD_INTERVAL),
}


def get(key: str) -> float:
    with _lock:
        return _values.get(key, 0.0)


def set_many(pairs: dict):
    with _lock:
        for key, value in pairs.items():
            if key in _values:
                try:
                    _values[key] = max(1.0, float(value))
                except (TypeError, ValueError):
                    pass


def all() -> dict:
    with _lock:
        return dict(_values)
