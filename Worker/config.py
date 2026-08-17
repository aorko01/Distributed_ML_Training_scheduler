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
# Files at or above this size bypass the proxied upload endpoint and go straight
# to MinIO via a presigned URL (the Cloudflare proxy rejects payloads >100MB).
OBJECT_STORE_LARGE_FILE_THRESHOLD = int(
    os.getenv("OBJECT_STORE_LARGE_FILE_THRESHOLD", str(50 * 1024 * 1024))
)

# Intervals and Auth
HEARTBEAT_INTERVAL = 5
JOB_POLL_INTERVAL = 10
DOCKER_HUB_USERNAME = os.getenv("DOCKER_HUB_USERNAME", "aorko123")

# Container execution.
# The executor normally mounts the job output dir onto the container's WORKDIR
# so everything the container writes relative to its working directory is
# captured. CONTAINER_OUTPUT_MOUNT is the fallback mount path used when the
# WORKDIR can't be prepared (e.g. image WORKDIR is "/" or the seed fails).
CONTAINER_OUTPUT_MOUNT = os.getenv("CONTAINER_OUTPUT_MOUNT", "/output")
LOG_UPLOAD_INTERVAL = int(os.getenv("LOG_UPLOAD_INTERVAL", "60"))
LOG_PUSH_INTERVAL = float(os.getenv("LOG_PUSH_INTERVAL", "1.0"))

# Run training/estimation containers as the worker's own UID/GID so files
# written into the output mount are owned by the worker: readable for upload
# and deletable on cleanup. Set CONTAINER_AS_ROOT=1 only if a job needs root.
CONTAINER_AS_ROOT = os.getenv("CONTAINER_AS_ROOT", "0").lower() in ("1", "true", "yes")

# File Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKER_ID_FILE = os.path.join(BASE_DIR, "worker_id.txt")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
VRAM_ESTIMATION_SCRIPT = os.path.join(BASE_DIR, "vram_estimation.py")

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Central Logger Configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)