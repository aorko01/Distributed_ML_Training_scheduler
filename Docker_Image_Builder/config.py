import os
import logging
from dotenv import load_dotenv

load_dotenv()

# API and URLs
SCHEDULER_BASE_URL = os.environ.get("SCHEDULER_API_URL", "http://localhost:8000")
SCHEDULER_UPDATE_URL = SCHEDULER_BASE_URL.rstrip("/") + "/jobs/update_job_to_vram_estimation_pending"
SCHEDULER_INTERACTIVE_UPDATE_URL = SCHEDULER_BASE_URL.rstrip("/") + "/jobs/mark_interactive_ready"
SCHEDULER_FAILURE_URL = SCHEDULER_BASE_URL.rstrip("/") + "/jobs/mark_failed"
SCHEDULER_QUEUE_URL = SCHEDULER_BASE_URL.rstrip("/") + "/jobs/unbuilt_jobs"
SCHEDULER_LOG_URL = SCHEDULER_BASE_URL.rstrip("/") + "/jobs/logs"

OBJECT_STORE_URL = os.environ.get("OBJECT_STORE_URL", "http://localhost:8010").rstrip("/")
OBJECT_STORE_BUCKET = os.environ.get("OBJECT_STORE_BUCKET", "uploads")
OBJECT_OUTPUT_BUCKET = os.environ.get("OBJECT_OUTPUT_BUCKET", "outputs")

# Credentials
DOCKER_HUB_USERNAME = os.environ["DOCKER_HUB_USERNAME"]
DOCKER_HUB_PASSWORD = os.environ.get("DOCKER_HUB_PASSWORD", "")

# App Settings
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "10"))
DB_PATH = os.environ.get("DB_PATH", "/data/builder.db")

# Debugging
DEBUG_SAVE_LOCAL = os.environ.get("DEBUG_SAVE_LOCAL", "false").strip().lower() in ("1", "true", "yes", "on")
DEBUG_LOCAL_DIR = os.environ.get("DEBUG_LOCAL_DIR", "./debug_jobs")

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("image_builder")