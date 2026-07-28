import os
import time
import uuid
import json
import shlex
import shutil
import logging
import zipfile
import threading
import subprocess
import tempfile
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
SAVE_VRAM_ESTIMATION_URL = f"{BASE_URL}/jobs/save_vram_estimation"

HEARTBEAT_INTERVAL = 5
JOB_POLL_INTERVAL = 10
WORKER_ID_FILE = "worker_id.txt"
DOCKER_HUB_USERNAME = os.getenv("DOCKER_HUB_USERNAME", "aorko123")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
VRAM_ESTIMATION_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vram_estimation.py")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Object store (checkpoint persistence)
OBJECT_STORE_URL = os.getenv("OBJECT_STORE_URL", "http://localhost:8010").rstrip("/")
CHECKPOINT_BUCKET = os.getenv("OBJECT_STORE_CHECKPOINT_BUCKET", "checkpoints")
CHECKPOINT_UPLOAD_URL = f"{OBJECT_STORE_URL}/objects/upload"
CHECKPOINT_CONTAINER_PATH = "/checkpoints"  # contract: user code checkpoints here
CHECKPOINT_SYNC_INTERVAL = int(os.getenv("CHECKPOINT_SYNC_INTERVAL", "30"))  # seconds

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


def get_python_command(command: str) -> list[str] | None:
    try:
        command_args = json.loads(command) if command.lstrip().startswith("[") else shlex.split(command)
    except (json.JSONDecodeError, ValueError):
        return None

    for index, value in enumerate(command_args):
        if os.path.basename(value).startswith("python"):
            command_args = command_args[index + 1:]
            while command_args and command_args[0].startswith("-"):
                command_args.pop(0)
            return command_args or None
    return None


def save_vram_estimation(job_id: str, report: dict):
    payload = {
        "job_id": job_id,
        "vram_required": report["peak_reserved_memory"],
        "step_time": report["step_wall_time"],
    }
    try:
        response = requests.post(SAVE_VRAM_ESTIMATION_URL, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise RuntimeError(data["error"])
        logger.info("Saved VRAM estimation for job %s: %s", job_id, data)
    except Exception as e:
        logger.error("Failed to save VRAM estimation for job %s: %s", job_id, e)


def handle_vram_estimation(job_id: str, image_name: str, command: str):
    target_command = get_python_command(command)
    if not target_command:
        logger.error("Job %s needs a Python command for VRAM estimation.", job_id)
        return

    with tempfile.TemporaryDirectory(prefix=f"vram_{job_id}_") as report_dir:
        report_path = os.path.join(report_dir, "report.json")
        cmd = [
            "docker", "run", "--rm", "--gpus", "all",
            "-v", f"{VRAM_ESTIMATION_SCRIPT}:/vram_estimation.py:ro",
            "-v", f"{report_dir}:/report",
            "--entrypoint", "python", image_name,
            "/vram_estimation.py", "--output", "/report/report.json", *target_command,
        ]
        logger.info("Running VRAM estimation for job %s.", job_id)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("VRAM estimation failed for job %s: %s", job_id, result.stderr.strip())
            return
        try:
            with open(report_path, encoding="utf-8") as report_file:
                report = json.load(report_file)
            if report.get("step_wall_time") is None:
                raise ValueError("no optimizer steps were observed")
        except (OSError, ValueError, json.JSONDecodeError) as e:
            logger.error("Invalid VRAM estimation report for job %s: %s", job_id, e)
            return

    save_vram_estimation(job_id, report)


def _checkpoint_object_key(job_id: str) -> str:
    return f"{job_id}/checkpoint.zip"


def _zip_dir(src_dir: str, zip_path: str):
    """Zip the contents of src_dir (relative paths, no top-level folder) into zip_path."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(src_dir):
            for name in files:
                file_path = os.path.join(root, name)
                arcname = os.path.relpath(file_path, src_dir)
                zf.write(file_path, arcname)


def upload_checkpoint(job_id: str, checkpoint_dir: str) -> bool:
    """Zip the host-side checkpoint dir and push it to the object store, overwriting
    any previous checkpoint for this job (object key is stable per job_id)."""
    if not os.path.isdir(checkpoint_dir) or not os.listdir(checkpoint_dir):
        return False

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        zip_path = tmp.name

    try:
        _zip_dir(checkpoint_dir, zip_path)
        object_key = _checkpoint_object_key(job_id)
        with open(zip_path, "rb") as f:
            files = {"file": ("checkpoint.zip", f, "application/zip")}
            data = {"bucket": CHECKPOINT_BUCKET, "object_key": object_key}
            resp = requests.post(CHECKPOINT_UPLOAD_URL, data=data, files=files, timeout=60)
        if resp.status_code >= 400:
            logger.error(
                "Checkpoint upload failed for job %s: %s %s", job_id, resp.status_code, resp.text
            )
            return False
        logger.info("Checkpoint synced for job %s.", job_id)
        return True
    except Exception as e:
        logger.error("Checkpoint upload error for job %s: %s", job_id, e)
        return False
    finally:
        try:
            os.remove(zip_path)
        except OSError:
            pass


def download_checkpoint(job_id: str, dest_dir: str) -> bool:
    """Fetch the latest checkpoint zip for job_id from the object store and extract
    it into dest_dir. Returns False (no error) if no checkpoint exists yet."""
    object_key = _checkpoint_object_key(job_id)
    url = f"{OBJECT_STORE_URL}/objects/{CHECKPOINT_BUCKET}/{object_key}"
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code == 404:
            logger.warning("No existing checkpoint found for job %s.", job_id)
            return False
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to download checkpoint for job %s: %s", job_id, e)
        return False

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(resp.content)
        zip_path = tmp.name

    try:
        os.makedirs(dest_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(dest_dir)
        logger.info("Checkpoint restored for job %s into %s.", job_id, dest_dir)
        return True
    except Exception as e:
        logger.error("Failed to extract checkpoint for job %s: %s", job_id, e)
        return False
    finally:
        try:
            os.remove(zip_path)
        except OSError:
            pass


class _CheckpointSyncer:
    """Background thread that periodically uploads a host checkpoint dir while a
    training container is running. Started once training begins, stopped (with one
    final sync) once the container exits, so we never lose more than one interval
    of progress if the worker dies mid-run."""

    def __init__(self, job_id: str, checkpoint_dir: str, interval: int = CHECKPOINT_SYNC_INTERVAL):
        self.job_id = job_id
        self.checkpoint_dir = checkpoint_dir
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop_event.wait(self.interval):
            upload_checkpoint(self.job_id, self.checkpoint_dir)

    def start(self):
        self._thread.start()

    def stop_and_final_sync(self):
        self._stop_event.set()
        self._thread.join()
        upload_checkpoint(self.job_id, self.checkpoint_dir)


def _run_training_container(job_id: str, image_name: str, resume: bool):
    """Shared execution path for fresh training runs and retries. Mounts a host
    checkpoint dir into the container at CHECKPOINT_CONTAINER_PATH, optionally
    pre-populating it from the last known checkpoint, and periodically syncs it
    back to the object store while the container runs so a mid-training worker
    failure loses at most CHECKPOINT_SYNC_INTERVAL seconds of progress.
    """
    with tempfile.TemporaryDirectory(prefix=f"ckpt_{job_id}_") as checkpoint_dir:
        if resume:
            download_checkpoint(job_id, checkpoint_dir)

        cmd = [
            "docker", "run", "--rm", "--gpus", "all",
            "-v", f"{checkpoint_dir}:{CHECKPOINT_CONTAINER_PATH}",
            image_name,
        ]
        logger.info(
            "Running %s training container for job %s: %s",
            "resumed" if resume else "fresh", job_id, " ".join(cmd),
        )

        syncer = _CheckpointSyncer(job_id, checkpoint_dir)
        syncer.start()

        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
        except Exception as e:
            logger.error("Execution error for job %s: %s", job_id, e)
            syncer.stop_and_final_sync()
            return
        finally:
            if syncer._thread.is_alive():
                syncer.stop_and_final_sync()

        log_file = os.path.join(OUTPUT_DIR, f"{job_id}.txt")
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(res.stdout + "\n" + res.stderr)

        if res.returncode == 0:
            logger.info("Job %s completed successfully.", job_id)
        else:
            logger.error("Job %s failed with exit code %d.", job_id, res.returncode)

        upload_output_file(log_file, job_id)


def handle_training(job_id: str, image_name: str):
    """Handle a fresh (first-attempt) training job."""
    logger.info("Training job received for job %s.", job_id)
    _run_training_container(job_id, image_name, resume=False)


def handle_retry(job_id: str, image_name: str):
    """Handle a retry job: a previous worker died mid-training (missed heartbeats),
    and the scheduler has re-assigned this job to us. Resume from the last
    checkpoint synced to the object store instead of starting from scratch.
    """
    logger.info("Retry job received for job %s.", job_id)
    _run_training_container(job_id, image_name, resume=True)


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
        handle_vram_estimation(job_id, image_name, job.get("command", ""))
    elif flag == "training":
        handle_training(job_id, image_name)
    elif flag == "retry":
        handle_retry(job_id, image_name)
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