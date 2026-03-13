import requests
import uuid
import GPUtil
import os
import time
import docker
import logging
from dotenv import load_dotenv

load_dotenv()

# -------------------------
# CONFIG
# -------------------------
BASE_URL = os.getenv("SCHEDULER_URL")

if not BASE_URL:
    raise ValueError("SCHEDULER_URL not set in environment")

BASE_URL = BASE_URL.rstrip("/")

REGISTER_URL = f"{BASE_URL}/workers/register"
HEARTBEAT_URL = f"{BASE_URL}/workers/heartbeat"
PULL_JOB_URL = f"{BASE_URL}/jobs/pull_job"

HEARTBEAT_INTERVAL = 5
JOB_POLL_INTERVAL = 10
WORKER_ID_FILE = "worker_id.txt"
DOCKER_HUB_USERNAME = "aorko123"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

# -------------------------
# ENSURE OUTPUT DIR EXISTS
# -------------------------
os.makedirs(OUTPUT_DIR, exist_ok=True)


# -------------------------
# LOGGER SETUP
# -------------------------
def get_logger(name: str, log_file: str = None) -> logging.Logger:
    """
    Returns a logger that always writes to stdout.
    If log_file is provided, also writes to that file.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Avoid adding duplicate handlers if logger already exists
    if logger.handlers:
        return logger

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # Console handler (always on)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (per-job)
    if log_file:
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


# Base logger for general worker activity (no job-specific file)
base_logger = get_logger("worker")


# -------------------------
# WORKER ID (persistent)
# -------------------------
if os.path.exists(WORKER_ID_FILE):
    with open(WORKER_ID_FILE, "r") as f:
        worker_id = f.read().strip()
else:
    worker_id = str(uuid.uuid4())
    with open(WORKER_ID_FILE, "w") as f:
        f.write(worker_id)


# -------------------------
# GPU INFO
# -------------------------
def get_gpu_info():
    gpus = GPUtil.getGPUs()
    gpu = gpus[0]
    gpu_type = gpu.name
    total_vram = round(gpu.memoryTotal / 1024, 2)
    available_vram = round(gpu.memoryFree / 1024, 2)
    num_gpus = len(gpus)
    return gpu_type, total_vram, available_vram, num_gpus


# -------------------------
# MAC ADDRESS
# -------------------------
mac = ':'.join(f'{(uuid.getnode() >> ele) & 0xff:02x}'
               for ele in range(0, 8*6, 8)[::-1])


# -------------------------
# REGISTER WORKER
# -------------------------
def register_worker():
    gpu_type, total_vram, _, num_gpus = get_gpu_info()

    worker_info = {
        "worker_id": worker_id,
        "mac_address": mac,
        "gpu_type": gpu_type,
        "num_gpus": num_gpus,
        "total_vram": total_vram
    }

    resp = requests.post(REGISTER_URL, json=worker_info)
    base_logger.info("REGISTER RESPONSE: %s", resp.json())


# -------------------------
# SEND HEARTBEAT
# -------------------------
def send_heartbeat():
    gpu_type, _, available_vram, _ = get_gpu_info()

    heartbeat_payload = {
        "worker_id": worker_id,
        "gpu_type": gpu_type,
        "available_vram": available_vram
    }

    resp = requests.post(HEARTBEAT_URL, json=heartbeat_payload)
    base_logger.info("HEARTBEAT SENT: %s", heartbeat_payload)
    base_logger.info("SERVER RESPONSE: %s", resp.json())


# -------------------------
# PULL JOB FROM SCHEDULER
# -------------------------
def pull_job():
    gpu_type, _, free_vram, _ = get_gpu_info()

    params = {
        "worker_id": worker_id,
        "gpu_type": gpu_type,
        "free_vram": free_vram
    }

    resp = requests.get(PULL_JOB_URL, params=params, timeout=10)
    data = resp.json()

    if "message" in data:
        base_logger.info("No pending jobs: %s", data["message"])
        return None

    if "error" in data:
        base_logger.error("Error from scheduler: %s", data["error"])
        return None

    return data


# -------------------------
# PULL DOCKER IMAGE
# -------------------------
def pull_docker_image(client: docker.DockerClient, job_id: str, logger: logging.Logger) -> bool:
    image_name = f"{DOCKER_HUB_USERNAME}/{job_id}:latest"
    logger.info("Pulling Docker image: %s", image_name)

    try:
        for line in client.api.pull(image_name, stream=True, decode=True):
            status = line.get("status", "")
            progress = line.get("progress", "")
            if status:
                logger.info("  [pull] %s %s", status, progress)
            if "error" in line:
                logger.error("  [pull error] %s", line["error"])
                return False

        logger.info("Successfully pulled image: %s", image_name)
        return True

    except docker.errors.APIError as e:
        logger.error("Failed to pull image %s: %s", image_name, e)
        return False


# -------------------------
# RUN SCRIPT INSIDE DOCKER
# -------------------------
def run_job_in_docker(client: docker.DockerClient, job_id: str, script_path: str, logger: logging.Logger):
    image_name = f"{DOCKER_HUB_USERNAME}/{job_id}:latest"

    logger.info("Running script '%s' in container from image: %s", script_path, image_name)

    try:
        container = client.containers.run(
            image=image_name,
            command=["python", script_path],
            detach=True,
            runtime="nvidia",
            device_requests=[
                docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])
            ],
            stdout=True,
            stderr=True,
        )

        logger.info("Container started: %s", container.short_id)

        for log_line in container.logs(stream=True, follow=True):
            logger.info("  [container] %s", log_line.decode("utf-8", errors="replace").strip())

        result = container.wait()
        exit_code = result.get("StatusCode", -1)

        if exit_code == 0:
            logger.info("Job %s completed successfully.", job_id)
        else:
            logger.error("Job %s exited with code %d.", job_id, exit_code)

        container.remove()

    except docker.errors.ContainerError as e:
        logger.error("Container error for job %s: %s", job_id, e)
    except docker.errors.ImageNotFound:
        logger.error("Image not found locally for job %s. Pull may have failed.", job_id)
    except docker.errors.APIError as e:
        logger.error("Docker API error for job %s: %s", job_id, e)


# -------------------------
# PROCESS ONE JOB
# -------------------------
def process_job():
    job = pull_job()
    if job is None:
        return

    job_id = job.get("job_id")
    script_path = job.get("script_path")

    if not job_id or not script_path:
        base_logger.error("Invalid job payload received: %s", job)
        return

    # Create a dedicated logger that writes to output/<job_id>.txt
    log_file = os.path.join(OUTPUT_DIR, f"{job_id}.txt")
    job_logger = get_logger(f"job_{job_id}", log_file=log_file)

    job_logger.info("=" * 60)
    job_logger.info("Job started — ID: %s | Script: %s", job_id, script_path)
    job_logger.info("Log file: %s", log_file)

    client = docker.from_env()

    success = pull_docker_image(client, job_id, job_logger)
    if not success:
        job_logger.error("Aborting job %s: image pull failed.", job_id)
        return

    run_job_in_docker(client, job_id, script_path, job_logger)

    job_logger.info("Job %s finished. Log saved to: %s", job_id, log_file)


# -------------------------
# MAIN LOOP
# -------------------------
if __name__ == "__main__":
    base_logger.info("Worker starting up. Output directory: %s", OUTPUT_DIR)
    register_worker()

    base_logger.info("Starting worker loop...")
    last_heartbeat = 0

    while True:
        now = time.time()

        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            try:
                send_heartbeat()
            except Exception as e:
                base_logger.error("Heartbeat failed: %s", e)
            last_heartbeat = time.time()

        try:
            process_job()
        except Exception as e:
            base_logger.error("Job processing failed: %s", e)

        time.sleep(JOB_POLL_INTERVAL)