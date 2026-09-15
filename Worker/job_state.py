import json
import os
import time
import logging
import threading

from config import BASE_DIR

logger = logging.getLogger("job_state")

# Marker file recording the jobs this worker is currently executing. The scheduler
# only marks a job RETRY_NEEDED after a worker has missed heartbeats for
# STALL_TIMEOUT_SECONDS (3 minutes), so if the worker dies and comes back before
# then the job is still IN_PROGRESS on the scheduler even though nothing is
# running it. Persisting running jobs lets a restarted worker pick them back up
# (resume from checkpoints) instead of leaving jobs stuck IN_PROGRESS.
STATE_FILE = os.path.join(BASE_DIR, "running_job.json")
_lock = threading.Lock()


def load_running_job() -> dict | None:
    """Return the persisted in-progress job ({job_id, saved_at, jobs}) or None."""
    with _lock:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not data.get("job_id"):
                return None
            return data
        except (OSError, ValueError, TypeError):
            return None


def load_running_jobs() -> list[dict]:
    """Return all persisted in-progress jobs as a list of dicts ({job_id, saved_at})."""
    with _lock:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return []
            if isinstance(data.get("jobs"), dict):
                result = [
                    j for j in data["jobs"].values()
                    if isinstance(j, dict) and j.get("job_id")
                ]
                if result:
                    return result
            if data.get("job_id"):
                return [{"job_id": data["job_id"], "saved_at": data.get("saved_at", time.time())}]
            return []
        except (OSError, ValueError, TypeError):
            return []


def save_running_job(job_id: str):
    """Atomically persist a job the worker is about to start running."""
    with _lock:
        now = time.time()
        jobs: dict[str, dict] = {}
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    if isinstance(data.get("jobs"), dict):
                        jobs = {
                            k: v for k, v in data["jobs"].items()
                            if isinstance(v, dict) and v.get("job_id")
                        }
                    elif data.get("job_id"):
                        jobs[data["job_id"]] = {
                            "job_id": data["job_id"],
                            "saved_at": data.get("saved_at", now),
                        }
        except Exception:
            jobs = {}

        jobs[job_id] = {"job_id": job_id, "saved_at": now}
        payload = {
            "job_id": job_id,
            "saved_at": now,
            "jobs": jobs,
        }
        tmp_path = STATE_FILE + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp_path, STATE_FILE)
        except OSError as e:
            logger.warning("Failed to persist running job state: %s", e)


def clear_running_job(job_id: str | None = None):
    """Remove the in-progress job marker once a job reaches a terminal state.

    If job_id is provided, removes that job from persisted jobs. If no jobs
    remain (or job_id is None), removes the state file entirely.
    """
    with _lock:
        try:
            if not os.path.exists(STATE_FILE):
                return
            if job_id is None:
                os.remove(STATE_FILE)
                return

            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                if os.path.exists(STATE_FILE):
                    os.remove(STATE_FILE)
                return

            if not isinstance(data, dict):
                os.remove(STATE_FILE)
                return

            jobs: dict[str, dict] = {}
            if isinstance(data.get("jobs"), dict):
                jobs = {
                    k: v for k, v in data["jobs"].items()
                    if isinstance(v, dict) and v.get("job_id")
                }
            elif data.get("job_id"):
                jobs[data["job_id"]] = {
                    "job_id": data["job_id"],
                    "saved_at": data.get("saved_at", time.time()),
                }

            jobs.pop(job_id, None)
            if not jobs:
                if os.path.exists(STATE_FILE):
                    os.remove(STATE_FILE)
                return

            next_job = next(iter(jobs.values()))
            payload = {
                "job_id": next_job["job_id"],
                "saved_at": next_job["saved_at"],
                "jobs": jobs,
            }
            tmp_path = STATE_FILE + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp_path, STATE_FILE)
        except OSError as e:
            logger.warning("Failed to clear running job state: %s", e)