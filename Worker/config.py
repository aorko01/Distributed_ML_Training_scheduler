import os
import logging
from dotenv import load_dotenv

load_dotenv()

# Scheduler URLs
BASE_URL = os.getenv("SCHEDULER_URL")
if not BASE_URL:
    raise ValueError("SCHEDULER_URL not set in environment")
BASE_URL = BASE_URL.rstrip("/")

_scheduler_url = BASE_URL


def get_scheduler_url() -> str:
    return _scheduler_url


def set_scheduler_url(url: str):
    global _scheduler_url
    _scheduler_url = url.strip().rstrip("/") or _scheduler_url


def persist_env(pairs: dict):
    """Overwrite env values in the .env file (keeps existing keys)."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        lines = []
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                lines = f.readlines()
        keys = set(pairs.keys())
        kept = [ln for ln in lines if not ln.split("=", 1)[0].strip() in keys]
        with open(env_path, "w") as f:
            f.writelines(kept)
            for key, value in pairs.items():
                f.write(f"{key}={value}\n")
    except Exception as e:
        logging.getLogger("config").error("Failed to persist .env: %s", e)

REGISTER_URL = f"{BASE_URL}/workers/register"
HEARTBEAT_URL = f"{BASE_URL}/workers/heartbeat"
PULL_JOB_URL = f"{BASE_URL}/jobs/pull_job"
SAVE_VRAM_ESTIMATION_URL = f"{BASE_URL}/jobs/save_vram_estimation"
MARK_COMPLETED_URL = f"{BASE_URL}/jobs/mark_completed"
SEND_LOG_URL = f"{BASE_URL}/jobs/logs"

# Object Store
OBJECT_STORE_URL = os.getenv("OBJECT_STORE_URL", "http://localhost:8010").rstrip("/")
OBJECT_OUTPUT_BUCKET = os.getenv("OBJECT_OUTPUT_BUCKET", "outputs")

# Intervals and Auth
HEARTBEAT_INTERVAL = 5
JOB_POLL_INTERVAL = 10
DOCKER_HUB_USERNAME = os.getenv("DOCKER_HUB_USERNAME", "aorko123")

# Container execution
CONTAINER_OUTPUT_MOUNT = os.getenv("CONTAINER_OUTPUT_MOUNT", "/output")
LOG_UPLOAD_INTERVAL = int(os.getenv("LOG_UPLOAD_INTERVAL", "60"))
LOG_PUSH_INTERVAL = float(os.getenv("LOG_PUSH_INTERVAL", "1.0"))

# Checkpoint / restore (best-effort fallback layer, not primary fault recovery)
# CHECKPOINT_ENABLED: master switch. When disabled, training containers are run
# exactly as before (docker run --rm) and checkpointing endpoints are no-ops.
# CHECKPOINT_INTERVAL: seconds between automatic snapshots of running training
# jobs. 0 disables periodic checkpointing (manual endpoints still work). This
# value is tunable at runtime via /api/config (checkpointIntervalSec).
# CHECKPOINT_MODE: how a manual pause stores the snapshot:
#   "stop_restart"   — checkpoint leaves the container stopped (frozen);
#                      restore uses `docker start --checkpoint`.
#   "leave_running"  — checkpoint keeps the container running with CUDA
#                      suspended; restore stops it and starts from the dump.
CHECKPOINT_ENABLED = os.getenv("CHECKPOINT_ENABLED", "1").lower() in ("1", "true", "yes")
CHECKPOINT_INTERVAL = float(os.getenv("CHECKPOINT_INTERVAL", "0"))
CHECKPOINT_MODE = os.getenv("CHECKPOINT_MODE", "stop_restart")
CUDA_CHECKPOINT_BIN = os.getenv("CUDA_CHECKPOINT_BIN", "cuda-checkpoint")

# File Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKER_ID_FILE = os.path.join(BASE_DIR, "worker_id.txt")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
VRAM_ESTIMATION_SCRIPT = os.path.join(BASE_DIR, "vram_estimation.py")
CHECKPOINT_DIR = os.path.join(BASE_DIR, "checkpoints")

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(CHECKPOINT_DIR, exist_ok=True)

# Central Logger Configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)