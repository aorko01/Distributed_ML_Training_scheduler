import os
import time
import json
import shutil
import tempfile
import logging
import docker
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("image_builder")

# Docker Hub credentials
DOCKER_HUB_USERNAME = os.environ["DOCKER_HUB_USERNAME"]
DOCKER_HUB_PASSWORD = os.environ.get("DOCKER_HUB_PASSWORD", "")  # Optional if already logged in

# Directory to watch for new job uploads
UPLOADS_DIR = os.environ.get("UPLOADS_DIR", "/uploads")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "10"))
PROCESSED_FILE = "/data/processed_jobs.json"

# Default base image and mapping for cached common images
BASE_IMAGE_DEFAULT = "pytorch/pytorch:2.1.0-cuda11.8-cudnn8-runtime"

BASE_IMAGE_MAP = {
    # Transformers / NLP
    "transformers": f"{DOCKER_HUB_USERNAME}/ml-base-transformers:latest",
    "datasets": f"{DOCKER_HUB_USERNAME}/ml-base-transformers:latest",
    "accelerate": f"{DOCKER_HUB_USERNAME}/ml-base-transformers:latest",
    
    # Vision
    "opencv-python": f"{DOCKER_HUB_USERNAME}/ml-base-vision:latest",
    "albumentations": f"{DOCKER_HUB_USERNAME}/ml-base-vision:latest",
    "Pillow": f"{DOCKER_HUB_USERNAME}/ml-base-vision:latest",

    # Training / logging
    "wandb": f"{DOCKER_HUB_USERNAME}/ml-base-training:latest",
    "tensorboard": f"{DOCKER_HUB_USERNAME}/ml-base-training:latest",
    "hydra-core": f"{DOCKER_HUB_USERNAME}/ml-base-training:latest",
}

# ----------------------
# Job tracking functions
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
    """Find actual project directory inside extracted folder."""
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
    """Login to Docker Hub if credentials are provided."""
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

    # Temporary build directory
    build_dir = tempfile.mkdtemp(prefix=f"build_{job_id}_")
    try:
        # Copy project files
        for item in os.listdir(project_dir):
            src = os.path.join(project_dir, item)
            dst = os.path.join(build_dir, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

        # Write Dockerfile
        dockerfile_content = generate_dockerfile(project_dir)
        with open(os.path.join(build_dir, "Dockerfile"), "w") as f:
            f.write(dockerfile_content)

        logger.info("Building image %s from %s ...", image_tag, build_dir)
        try:
            image, build_logs = client.images.build(
                path=build_dir,
                tag=image_tag,
                rm=True,
                forcerm=True,
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
    logger.info("Pushing image %s to Docker Hub ...", image_tag)
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
# Scan uploads directory
# ----------------------
def scan_and_process():
    processed = load_processed_jobs()
    client = docker.from_env()

    docker_login(client)

    if not os.path.isdir(UPLOADS_DIR):
        logger.warning("Uploads directory %s does not exist yet.", UPLOADS_DIR)
        return

    for entry in os.listdir(UPLOADS_DIR):
        job_dir = os.path.join(UPLOADS_DIR, entry)
        if not os.path.isdir(job_dir):
            continue

        job_id = entry
        if job_id in processed:
            continue

        extracted_dir = os.path.join(job_dir, "extracted")
        if not os.path.isdir(extracted_dir):
            logger.info("Job %s has no extracted/ directory yet, skipping.", job_id)
            continue

        project_dir = find_project_dir(extracted_dir)
        if project_dir is None:
            logger.warning("Job %s: could not find project directory, skipping.", job_id)
            continue

        logger.info("=" * 60)
        logger.info("Processing new job: %s", job_id)
        logger.info("Project directory: %s", project_dir)

        success = build_and_push(client, job_id, project_dir)
        if success:
            processed.add(job_id)
            save_processed_jobs(processed)
            logger.info("Job %s marked as processed.", job_id)
        else:
            logger.error("Job %s failed, will retry next cycle.", job_id)

    logger.info("Scan complete. %d jobs processed so far.", len(processed))

# ----------------------
# Main loop
# ----------------------
def main():
    logger.info("Docker Image Builder service starting ...")
    logger.info("Watching directory: %s", UPLOADS_DIR)
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