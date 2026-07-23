
import os
import time
import json
import shutil
import tempfile
import logging
import io
import zipfile
from urllib.parse import quote

import docker
import requests
from dotenv import load_dotenv


# Load environment variables
load_dotenv()  # loads variables from .env

# Load scheduler base URL from .env
SCHEDULER_BASE_URL = os.environ.get("SCHEDULER_API_URL", "http://localhost:8000")
SCHEDULER_UPDATE_URL = SCHEDULER_BASE_URL.rstrip("/") + "/jobs/update_job_to_vram_estimation_pending"

SCHEDULER_QUEUE_URL = SCHEDULER_BASE_URL.rstrip("/") + "/jobs/unbuilt_jobs"

DOCKER_HUB_USERNAME = os.environ["DOCKER_HUB_USERNAME"]
DOCKER_HUB_PASSWORD = os.environ.get("DOCKER_HUB_PASSWORD", "")

OBJECT_STORE_URL = os.environ.get("OBJECT_STORE_URL", "http://localhost:8010").rstrip(
    "/"
)
OBJECT_STORE_BUCKET = os.environ.get("OBJECT_STORE_BUCKET", "uploads")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "10"))
PROCESSED_FILE = "/data/processed_jobs.json"

# --- Debug: optionally persist the assembled build context (source files +
# generated Dockerfile) to a local directory, keyed by job_id, for inspection.
# Off by default so normal runs don't litter the filesystem.
DEBUG_SAVE_LOCAL = os.environ.get("DEBUG_SAVE_LOCAL", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
DEBUG_LOCAL_DIR = os.environ.get("DEBUG_LOCAL_DIR", "./debug_jobs")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("image_builder")


# Job tracking
def load_processed_jobs() -> set:
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_processed_jobs(processed: set):
    os.makedirs(os.path.dirname(PROCESSED_FILE), exist_ok=True)
    with open(PROCESSED_FILE, "w") as f:
        json.dump(list(processed), f)


# Project detection
def find_project_dir(extracted_dir: str) -> str:
    for entry in sorted(os.listdir(extracted_dir)):
        if entry.startswith("__") or entry.startswith("."):
            continue
        candidate = os.path.join(extracted_dir, entry)
        if os.path.isdir(candidate):
            return candidate
    return extracted_dir


# Dockerfile generation
def generate_dockerfile(project_dir: str, command: str, base_image: str) -> str:
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

    if command:
        lines += [
            f"CMD {command}",
        ]
    else:
        lines += [
            'CMD ["python"]',
        ]

    logger.info("Using base image: %s", base_image)
    return "\n".join(lines)


# Docker login
def docker_login(client: docker.DockerClient):
    if DOCKER_HUB_PASSWORD:
        logger.info("Logging in to Docker Hub as %s ...", DOCKER_HUB_USERNAME)
        client.login(username=DOCKER_HUB_USERNAME, password=DOCKER_HUB_PASSWORD)
        logger.info("Docker Hub login successful.")
    else:
        logger.info("No Docker Hub password provided, assuming already logged in.")


# Debug snapshot of the assembled build context
def save_debug_copy(job_id: str, build_dir: str) -> None:
    """Copy the assembled build context (source files + generated Dockerfile)
    into DEBUG_LOCAL_DIR/<job_id> for local inspection. Only called when
    DEBUG_SAVE_LOCAL is enabled. Overwrites any previous copy for the job."""
    debug_dir = os.path.join(DEBUG_LOCAL_DIR, job_id)
    try:
        if os.path.exists(debug_dir):
            shutil.rmtree(debug_dir)
        os.makedirs(os.path.dirname(debug_dir) or ".", exist_ok=True)
        shutil.copytree(build_dir, debug_dir)
        # Ensure permissions allow non-root host users to access debug files
        os.chmod(debug_dir, 0o777)
        for root, dirs, files in os.walk(debug_dir):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o777)
            for f in files:
                os.chmod(os.path.join(root, f), 0o666)
        logger.info("Saved debug copy of job %s to %s", job_id, debug_dir)
    except Exception as e:
        logger.error("Failed to save debug copy for job %s: %s", job_id, e)


# Build and push image
def build_and_push(client: docker.DockerClient, job_id: str, project_dir: str, command: str, base_image: str) -> bool:
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

        dockerfile_content = generate_dockerfile(project_dir, command, base_image)
        with open(os.path.join(build_dir, "Dockerfile"), "w") as f:
            f.write(dockerfile_content)

        if DEBUG_SAVE_LOCAL:
            save_debug_copy(job_id, build_dir)

        logger.info("Building image %s ...", image_tag)
        try:
            _image, build_logs = client.images.build(
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


# Notify scheduler
def notify_scheduler_job_ready(job_id: str) -> bool:
    payload = {"job_id": job_id}

    try:
        response = requests.post(
            SCHEDULER_UPDATE_URL,
            json=payload,
            timeout=10,
        )

        if response.status_code == 200:
            # Check response body for error — scheduler returns 200 even on failures
            body = response.json()
            if "error" in body:
                logger.error(
                    "Scheduler returned error for job %s: %s",
                    job_id,
                    body["error"],
                )
                return False

            logger.info("Scheduler notified successfully for job %s", job_id)
            return True

        logger.error(
            "Scheduler notification failed for job %s: %s %s",
            job_id,
            response.status_code,
            response.text,
        )

    except Exception as e:
        logger.error(
            "Failed to contact scheduler for job %s: %s",
            job_id,
            e,
        )

    return False


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
        command = job.get("command", "")
        base_image = job.get("docker_base_image")

        if not job_id or not object_key or not base_image:
            logger.warning("Skipping malformed job payload: %s", job)
            continue

        if job_id in processed:
            # Image was built before but still showing as unbuilt —
            # scheduler notification must have failed. Retry notification.
            logger.info("Job %s already built but still unbuilt in scheduler, re-notifying...", job_id)
            notify_scheduler_job_ready(job_id)
            continue

        logger.info("=" * 50)
        logger.info("Processing job: %s", job_id)

        extract_dir = None
        success = False

        try:
            archive_bytes = download_job_archive(object_key)
            extract_dir = extract_job_archive(archive_bytes, job_id)
            project_dir = find_project_dir(extract_dir)
            success = build_and_push(client, job_id, project_dir, command, base_image)
        except Exception as e:
            logger.error("Failed while processing job %s: %s", job_id, e, exc_info=True)
        finally:
            if extract_dir:
                shutil.rmtree(extract_dir, ignore_errors=True)

        if success:
            notified = notify_scheduler_job_ready(job_id)
            if notified:
                processed.add(job_id)
                save_processed_jobs(processed)
                logger.info("Job %s completed.", job_id)
            else:
                logger.error("Job %s built but scheduler notification failed, will retry.", job_id)
        else:
            logger.error("Job %s failed, will retry later.", job_id)

    logger.info("Scan complete. Total processed: %d", len(processed))



# Main loop
def main():
    logger.info("Docker Image Builder service starting ...")
    logger.info("Watching scheduler queue: %s", SCHEDULER_QUEUE_URL)
    logger.info("Poll interval: %d seconds", POLL_INTERVAL)
    logger.info("Docker Hub user: %s", DOCKER_HUB_USERNAME)
    if DEBUG_SAVE_LOCAL:
        logger.info("Debug local saving ENABLED -> %s", DEBUG_LOCAL_DIR)
    else:
        logger.info("Debug local saving disabled (set DEBUG_SAVE_LOCAL=true to enable)")

    while True:
        try:
            scan_and_process()
        except Exception as e:
            logger.error("Error during scan cycle: %s", e, exc_info=True)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
