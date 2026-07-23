import os
import time
import uuid
import logging
import subprocess
import requests
import GPUtil
import docker
from dotenv import load_dotenv

load_dotenv()

# Configuration
BASE_URL = os.getenv("SCHEDULER_URL")
if not BASE_URL:
    raise ValueError("SCHEDULER_URL not set in environment")
BASE_URL = BASE_URL.rstrip("/")

REGISTER_URL = f"{BASE_URL}/workers/register"
HEARTBEAT_URL = f"{BASE_URL}/workers/heartbeat"
PULL_JOB_URL = f"{BASE_URL}/jobs/pull_job"
UPLOAD_OUTPUT_URL = f"{BASE_URL}/jobs/upload_output"

HEARTBEAT_INTERVAL = 5
JOB_POLL_INTERVAL = 10
WORKER_ID_FILE = "worker_id.txt"
DOCKER_HUB_USERNAME = os.getenv("DOCKER_HUB_USERNAME", "aorko123")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Logger setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("worker")

# Worker ID (persistent)
if os.path.exists(WORKER_ID_FILE):
    with open(WORKER_ID_FILE, "r") as f:
        worker_id = f.read().strip()
else:
    worker_id = str(uuid.uuid4())
    with open(WORKER_ID_FILE, "w") as f:
        f.write(worker_id)


def get_gpu_info():
    """Retrieve primary GPU specs and VRAM availability."""
    gpus = GPUtil.getGPUs()
    if not gpus:
        return "Unknown", 0.0, 0.0, 0
    gpu = gpus[0]
    return gpu.name, round(gpu.memoryTotal / 1024, 2), round(gpu.memoryFree / 1024, 2), len(gpus)


def register_worker():
    """Register worker hardware capabilities with the scheduler."""
    gpu_type, total_vram, _, num_gpus = get_gpu_info()
    payload = {
        "worker_id": worker_id,
        "gpu_type": gpu_type,
        "num_gpus": num_gpus,
        "total_vram": total_vram,
    }
    try:
        resp = requests.post(REGISTER_URL, json=payload, timeout=10)
        logger.info("Registered with scheduler: %s", resp.json())
    except Exception as e:
        logger.error("Failed to register worker: %s", e)


def send_heartbeat():
    """Send periodic heartbeat with updated VRAM stats."""
    gpu_type, _, available_vram, _ = get_gpu_info()
    payload = {
        "worker_id": worker_id,
        "gpu_type": gpu_type,
        "available_vram": available_vram,
    }
    try:
        resp = requests.post(HEARTBEAT_URL, json=payload, timeout=10)
        logger.info("Heartbeat sent: %s", resp.json())
    except Exception as e:
        logger.error("Heartbeat failed: %s", e)


def pull_job() -> dict | None:
    """Pull next available job assignment from scheduler."""
    gpu_type, _, free_vram, _ = get_gpu_info()
    payload = {
        "worker_id": worker_id,
        "gpu_type": gpu_type,
        "free_vram": free_vram,
    }
    try:
        resp = requests.post(PULL_JOB_URL, json=payload, timeout=10)
        data = resp.json()

        if "message" in data:
            logger.info("No runnable jobs: %s", data["message"])
            return None
        if "error" in data:
            logger.error("Error from scheduler: %s", data["error"])
            return None

        return data
    except Exception as e:
        logger.error("Failed to pull job: %s", e)
        return None


def pull_docker_image(image_name: str) -> bool:
    """Pull the job Docker image from registry."""
    logger.info("Pulling Docker image: %s", image_name)
    try:
        client = docker.from_env()
        client.images.pull(image_name)
        logger.info("Successfully pulled image: %s", image_name)
        return True
    except Exception as e:
        logger.error("Failed to pull image %s: %s", image_name, e)
        return False


def upload_output_file(file_path: str, job_id: str):
    """Upload completed job output log to scheduler."""
    if not os.path.exists(file_path):
        logger.error("Output file not found: %s", file_path)
        return

    try:
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f)}
            resp = requests.post(UPLOAD_OUTPUT_URL, files=files, timeout=30)
            logger.info("Uploaded output for job %s: %s", job_id, resp.text)
    except Exception as e:
        logger.error("Failed to upload output file for job %s: %s", job_id, e)


def handle_vram_estimation(job_id: str):
    """Handle VRAM estimation job."""
    logger.info("VRAM estimation job received for job %s.", job_id)


def handle_training(job_id: str, image_name: str):
    """Handle training job."""
    logger.info("Training job received for job %s.", job_id)

    cmd = ["docker", "run", "--rm", "--gpus", "all", image_name]
    logger.info("Running training container with command: %s", " ".join(cmd))

    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        log_file = os.path.join(OUTPUT_DIR, f"{job_id}.txt")

        with open(log_file, "w", encoding="utf-8") as f:
            f.write(res.stdout + "\n" + res.stderr)

        if res.returncode == 0:
            logger.info("Job %s completed successfully.", job_id)
        else:
            logger.error("Job %s failed with exit code %d.", job_id, res.returncode)

        upload_output_file(log_file, job_id)

    except Exception as e:
        logger.error("Execution error for job %s: %s", job_id, e)


def handle_retry(job_id: str):
    """Handle retry job."""
    logger.info("Retry job received for job %s.", job_id)


def process_job():
    """Fetch and dispatch job based on type."""
    job = pull_job()
    if not job:
        return

    job_id = job.get("job_id") or job.get("id")
    flag = job.get("flag", "training")
    image_name = f"{DOCKER_HUB_USERNAME}/{job_id}:latest"

    if not pull_docker_image(image_name):
        logger.error("Aborting job %s: image pull failed.", job_id)
        return

    if flag == "vram_estimation":
        handle_vram_estimation(job_id)
    elif flag == "training":
        handle_training(job_id, image_name)
    elif flag == "retry":
        handle_retry(job_id)
    else:
        logger.warning("Unknown job flag '%s' for job %s.", flag, job_id)


if __name__ == "__main__":
    logger.info("Worker starting. ID: %s", worker_id)
    register_worker()

    last_heartbeat = 0
    while True:
        now = time.time()
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            try:
                send_heartbeat()
            except Exception as e:
                logger.error("Heartbeat error: %s", e)
            last_heartbeat = now

        try:
            process_job()
        except Exception as e:
            logger.error("Error processing job: %s", e)

        time.sleep(JOB_POLL_INTERVAL)