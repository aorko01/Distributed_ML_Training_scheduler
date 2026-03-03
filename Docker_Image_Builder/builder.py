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

DOCKER_HUB_USERNAME = os.environ["DOCKER_HUB_USERNAME"]
DOCKER_HUB_PASSWORD = os.environ["DOCKER_HUB_PASSWORD"]
UPLOADS_DIR = os.environ.get("UPLOADS_DIR", "/uploads")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "10"))
PROCESSED_FILE = "/data/processed_jobs.json"


def load_processed_jobs() -> set:
    """Load the set of already-processed job IDs from disk."""
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_processed_jobs(processed: set):
    """Persist the set of processed job IDs to disk."""
    os.makedirs(os.path.dirname(PROCESSED_FILE), exist_ok=True)
    with open(PROCESSED_FILE, "w") as f:
        json.dump(list(processed), f)


def find_project_dir(extracted_dir: str) -> str | None:
    """
    Find the actual project directory inside the extracted folder.
    Skips __MACOSX and other hidden/meta directories.
    Returns the first valid project directory that contains files.
    """
    for entry in sorted(os.listdir(extracted_dir)):
        if entry.startswith("__") or entry.startswith("."):
            continue
        candidate = os.path.join(extracted_dir, entry)
        if os.path.isdir(candidate):
            return candidate
    # If no subdirectory found, the extracted dir itself is the project
    return extracted_dir


def generate_dockerfile(project_dir: str) -> str:
    """
    Generate a Dockerfile string for the given project directory.
    Looks for requirements.txt to install dependencies.
    """
    has_requirements = os.path.exists(os.path.join(project_dir, "requirements.txt"))

    lines = [
        "FROM pytorch/pytorch:2.1.0-cuda11.8-cudnn8-runtime",
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

    return "\n".join(lines)


def docker_login(client: docker.DockerClient):
    """Log in to Docker Hub."""
    logger.info("Logging in to Docker Hub as %s ...", DOCKER_HUB_USERNAME)
    client.login(username=DOCKER_HUB_USERNAME, password=DOCKER_HUB_PASSWORD)
    logger.info("Docker Hub login successful.")


def build_and_push(client: docker.DockerClient, job_id: str, project_dir: str) -> bool:
    """
    Build a Docker image from the project directory and push it to Docker Hub.
    Returns True on success, False on failure.
    """
    image_tag = f"{DOCKER_HUB_USERNAME}/{job_id}:latest"

    # Copy project files to a temporary writable directory for the build
    build_dir = tempfile.mkdtemp(prefix=f"build_{job_id}_")
    try:
        # Copy all project files into the build directory
        for item in os.listdir(project_dir):
            src = os.path.join(project_dir, item)
            dst = os.path.join(build_dir, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

        # Write a Dockerfile into the build directory
        dockerfile_content = generate_dockerfile(project_dir)
        dockerfile_path = os.path.join(build_dir, "Dockerfile")
        with open(dockerfile_path, "w") as f:
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
        # Clean up the temporary build directory
        shutil.rmtree(build_dir, ignore_errors=True)

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


def scan_and_process():
    """
    Scan the uploads directory for new job folders.
    Build and push an image for each unprocessed job.
    """
    processed = load_processed_jobs()
    client = docker.from_env()

    # Ensure we are logged in
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

        # Check that extraction has been done
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
