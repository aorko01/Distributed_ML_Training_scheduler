import json
import os
import time
import logging

from config import BASE_DIR

logger = logging.getLogger("job_state")

# Marker file recording the job this worker is currently executing. The scheduler
# only marks a job RETRY_NEEDED after a worker has missed heartbeats for
# STALL_TIMEOUT_SECONDS (3 minutes), so if the worker dies and comes back before
# then the job is still IN_PROGRESS on the scheduler even though nothing is
# running it. Persisting the running job lets a restarted worker pick it back up
# (resume from checkpoints) instead of leaving the job stuck IN_PROGRESS.
STATE_FILE = os.path.join(BASE_DIR, "running_job.json")


def load_running_job() -> dict | None:
    """Return the persisted in-progress job ({job_id, saved_at}) or None."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data.get("job_id"):
            return None
        return data
    except (OSError, ValueError, TypeError):
        return None


def save_running_job(job_id: str):
    """Atomically persist the job the worker is about to start running."""
    payload = {"job_id": job_id, "saved_at": time.time()}
    tmp_path = STATE_FILE + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, STATE_FILE)
    except OSError as e:
        logger.warning("Failed to persist running job state: %s", e)


def clear_running_job():
    """Remove the in-progress job marker once the job reaches a terminal state."""
    try:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
    except OSError as e:
        logger.warning("Failed to clear running job state: %s", e)