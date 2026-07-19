
import os
import time
import json
import shutil
import tempfile
import logging
import io
import zipfile
from pathlib import Path
from urllib.parse import quote

import docker
import requests
from dotenv import load_dotenv

# ----------------------
# Load environment variables
# ----------------------
load_dotenv()  # loads variables from .env

# Load scheduler base URL from .env
SCHEDULER_BASE_URL = os.environ.get("SCHEDULER_API_URL", "http://localhost:8000")
SCHEDULER_UPDATE_URL = SCHEDULER_BASE_URL.rstrip("/") + "/jobs/update_job_to_pending"
SCHEDULER_QUEUE_URL = SCHEDULER_BASE_URL.rstrip("/") + "/jobs/unbuilt_jobs"

DOCKER_HUB_USERNAME = os.environ["DOCKER_HUB_USERNAME"]
DOCKER_HUB_PASSWORD = os.environ.get("DOCKER_HUB_PASSWORD", "")

OBJECT_STORE_URL = os.environ.get("OBJECT_STORE_URL", "http://localhost:8010").rstrip(
    "/"
)
OBJECT_STORE_BUCKET = os.environ.get("OBJECT_STORE_BUCKET", "uploads")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "10"))
PROCESSED_FILE = "/data/processed_jobs.json"

BASE_IMAGE_DEFAULT = "pytorch/pytorch:2.1.0-cuda11.8-cudnn8-runtime"

BASE_IMAGE_MAP = {
    "transformers": f"{DOCKER_HUB_USERNAME}/ml-base-transformers:latest",
    "datasets": f"{DOCKER_HUB_USERNAME}/ml-base-transformers:latest",
    "accelerate": f"{DOCKER_HUB_USERNAME}/ml-base-transformers:latest",
    "opencv-python": f"{DOCKER_HUB_USERNAME}/ml-base-vision:latest",
    "albumentations": f"{DOCKER_HUB_USERNAME}/ml-base-vision:latest",
    "Pillow": f"{DOCKER_HUB_USERNAME}/ml-base-vision:latest",
    "wandb": f"{DOCKER_HUB_USERNAME}/ml-base-training:latest",
    "tensorboard": f"{DOCKER_HUB_USERNAME}/ml-base-training:latest",
    "hydra-core": f"{DOCKER_HUB_USERNAME}/ml-base-training:latest",
}

# ----------------------
# Logging
# ----------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("image_builder")


# ----------------------
# Job tracking
# ----------------------
def load_processed_jobs() -> set:
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_processed_jobs(processed: set):
    os.makedirs(os.path.dirname(PROCESSED_FILE), exist_ok=True)
    with open(PROCESSED_FILE, "w") as f:
        json.dump(list(processed), f)


# ----------------------
# Project detection
# ----------------------
def find_project_dir(extracted_dir: str) -> str | None:
    for entry in sorted(os.listdir(extracted_dir)):
        if entry.startswith("__") or entry.startswith("."):
            continue
        candidate = os.path.join(extracted_dir, entry)
        if os.path.isdir(candidate):
            return candidate
    return extracted_dir


# ----------------------
# Requirements parsing
# ----------------------
def read_requirements(project_dir: str) -> list[str]:
    req_file = os.path.join(project_dir, "requirements.txt")
    if not os.path.exists(req_file):
        return []

    packages = []
    with open(req_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pkg = line.split("==")[0].strip()
            packages.append(pkg)
    return packages


def select_base_image(project_dir: str) -> str:
    packages = read_requirements(project_dir)
    for pkg in packages:
        if pkg in BASE_IMAGE_MAP:
            return BASE_IMAGE_MAP[pkg]
    return BASE_IMAGE_DEFAULT


# ----------------------
# Dockerfile generation
# ----------------------
def generate_dockerfile(project_dir: str) -> str:
    base_image = select_base_image(project_dir)
    has_requirements = os.path.exists(os.path.join(project_dir, "requirements.txt"))

    lines = [
        f"FROM {base_image}",
        "",
        "WORKDIR /workspace",
        "",
        "COPY . /workspace/",
        "",
    ]

    if has_requirements:
        lines += [
            "RUN pip install --no-cache-dir -r requirements.txt",
            "",
        ]

    lines += [
        'CMD ["python"]',
    ]

    logger.info("Selected base image: %s", base_image)
    return "\n".join(lines)


# ----------------------
# Docker login
# ----------------------
def docker_login(client: docker.DockerClient):
    if DOCKER_HUB_PASSWORD:
        logger.info("Logging in to Docker Hub as %s ...", DOCKER_HUB_USERNAME)
        client.login(username=DOCKER_HUB_USERNAME, password=DOCKER_HUB_PASSWORD)
        logger.info("Docker Hub login successful.")
    else:
        logger.info("No Docker Hub password provided, assuming already logged in.")


# ----------------------
# Build and push image
# ----------------------
def build_and_push(client: docker.DockerClient, job_id: str, project_dir: str) -> bool:
    image_tag = f"{DOCKER_HUB_USERNAME}/{job_id}:latest"

    build_dir = tempfile.mkdtemp(prefix=f"build_{job_id}_")
    try:
        for item in os.listdir(project_dir):
            src = os.path.join(project_dir, item)
            dst = os.path.join(build_dir, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

        dockerfile_content = generate_dockerfile(project_dir)
        with open(os.path.join(build_dir, "Dockerfile"), "w") as f:
            f.write(dockerfile_content)

        logger.info("Building image %s ...", image_tag)
        try:
            image, build_logs = client.images.build(
                path=build_dir, tag=image_tag, rm=True, forcerm=True
            )
            for chunk in build_logs:
                if "stream" in chunk:
                    line = chunk["stream"].strip()
                    if line:
                        logger.info("  [build] %s", line)
        except docker.errors.BuildError as e:
            logger.error("Build failed for job %s: %s", job_id, e)
            return False
        except Exception as e:
            logger.error("Unexpected build error for job %s: %s", job_id, e)
            return False
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)

    # Push image
    logger.info("Pushing image %s ...", image_tag)
    try:
        push_output = client.images.push(
            repository=f"{DOCKER_HUB_USERNAME}/{job_id}",
            tag="latest",
            stream=True,
            decode=True,
        )
        for chunk in push_output:
            if "status" in chunk:
                detail = chunk.get("progress", "")
                logger.info("  [push] %s %s", chunk["status"], detail)
            if "error" in chunk:
                logger.error("Push error for job %s: %s", job_id, chunk["error"])
                return False
    except Exception as e:
        logger.error("Push failed for job %s: %s", job_id, e)
        return False

    logger.info("Successfully built and pushed %s", image_tag)
    return True


# ----------------------
# Notify scheduler
# ----------------------
def notify_scheduler_job_ready(job_id: str):
    payload = {"job_id": job_id}

    try:
        response = requests.post(SCHEDULER_UPDATE_URL, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info("Scheduler notified successfully for job %s", job_id)
        else:
            logger.error(
                "Scheduler notification failed for job %s: %s %s",
                job_id,
                response.status_code,
                response.text,
            )
    except Exception as e:
        logger.error("Failed to contact scheduler for job %s: %s", job_id, e)


def fetch_unbuilt_jobs() -> list[dict]:
    response = requests.get(SCHEDULER_QUEUE_URL, timeout=10)
    response.raise_for_status()
    payload = response.json()
    return payload.get("jobs", [])


def download_job_archive(object_key: str) -> bytes:
    download_url = f"{OBJECT_STORE_URL}/objects/{OBJECT_STORE_BUCKET}/{quote(object_key, safe='/')}"
    response = requests.get(download_url, timeout=30)
    response.raise_for_status()
    return response.content


def extract_job_archive(archive_bytes: bytes, job_id: str) -> str:
    extract_dir = tempfile.mkdtemp(prefix=f"job_{job_id}_")

    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as zip_ref:
            zip_ref.extractall(extract_dir)
    except Exception:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise

    return extract_dir


def scan_and_process():
    processed = load_processed_jobs()
    client = docker.from_env()
    docker_login(client)

    try:
        jobs = fetch_unbuilt_jobs()
    except Exception as e:
        logger.error("Failed to fetch unbuilt jobs: %s", e)
        return

    for job in jobs:
        job_id = job.get("id")
        object_key = job.get("object_key")

        if not job_id or not object_key:
            logger.warning("Skipping malformed job payload: %s", job)
            continue

        if job_id in processed:
            continue

        logger.info("=" * 50)
        logger.info("Processing job: %s", job_id)

        extract_dir = None
        success = False

        try:
            archive_bytes = download_job_archive(object_key)
            extract_dir = extract_job_archive(archive_bytes, job_id)
            project_dir = find_project_dir(extract_dir)
            success = build_and_push(client, job_id, project_dir)
        except Exception as e:
            logger.error("Failed while processing job %s: %s", job_id, e, exc_info=True)
        finally:
            if extract_dir:
                shutil.rmtree(extract_dir, ignore_errors=True)

        if success:
            processed.add(job_id)
            save_processed_jobs(processed)
            notify_scheduler_job_ready(job_id)
            logger.info("Job %s completed.", job_id)
        else:
            logger.error("Job %s failed, will retry later.", job_id)

    logger.info("Scan complete. Total processed: %d", len(processed))


# ----------------------
# Main loop
# ----------------------
def main():
    logger.info("Docker Image Builder service starting ...")
    logger.info("Watching scheduler queue: %s", SCHEDULER_QUEUE_URL)
    logger.info("Poll interval: %d seconds", POLL_INTERVAL)
    logger.info("Docker Hub user: %s", DOCKER_HUB_USERNAME)

    while True:
        try:
            scan_and_process()
        except Exception as e:
            logger.error("Error during scan cycle: %s", e, exc_info=True)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
