import os
import logging
from dotenv import load_dotenv

load_dotenv()

# Scheduler URLs
BASE_URL = os.getenv("SCHEDULER_URL")
if not BASE_URL:
    raise ValueError("SCHEDULER_URL not set in environment")
BASE_URL = BASE_URL.rstrip("/")

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