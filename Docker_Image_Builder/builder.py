import os
import io
import time
import shutil
import zipfile
import tempfile
import docker

from config import logger, POLL_INTERVAL, SCHEDULER_QUEUE_URL, DOCKER_HUB_USERNAME
from database import init_db, is_job_processed, mark_job_processed
from api import fetch_unbuilt_jobs, download_job_archive, notify_scheduler_job_ready, notify_scheduler_interactive_ready, notify_scheduler_job_failed
from docker_ops import docker_login, build_push_and_clean, prune_old_base_images, resolve_interactive_base_image, ensure_access_image

def find_project_dir(extracted_dir: str) -> str:
    for entry in sorted(os.listdir(extracted_dir)):
        if entry.startswith("__") or entry.startswith("."):
            continue
        candidate = os.path.join(extracted_dir, entry)
        if os.path.isdir(candidate):
            return candidate
    return extracted_dir

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
    client = docker.from_env()
    docker_login(client)

    # Perform routine cleanup of old base images
    prune_old_base_images(client)

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
        build_type = job.get("build_type", "training")
        base_job_id = job.get("base_job_id")

        if not job_id or (build_type != "interactive" and (not object_key or not base_image)):
            logger.warning("Skipping malformed job payload: %s", job)
            continue

        if build_type == "interactive":
            # Interactive builds either derive from an existing training job
            # (base_job_id) or are built directly from an uploaded archive
            # (object_key + env spec in config).
            if not base_job_id and not object_key:
                logger.warning("Skipping malformed interactive job payload (needs base_job_id or object_key): %s", job)
                continue

        if is_job_processed(job_id):
            logger.info("Job %s already built but still unbuilt in scheduler, re-notifying...", job_id)
            if build_type == "interactive":
                notify_scheduler_interactive_ready(job_id)
            else:
                notify_scheduler_job_ready(job_id)
            continue

        logger.info("=" * 50)
        logger.info("Processing job: %s", job_id)

        extract_dir = None
        result = None

        try:
            if build_type == "interactive":
                if base_job_id:
                    # Derived interactive build: reuse the base training image
                    # ({user}/{base_job_id}:latest) as the env image — no build
                    # needed. Just ensure the shared access image is available
                    # and notify the scheduler.
                    if not ensure_access_image(client):
                        result = ("system", "Failed to pull access image aorko123/access-sshd:latest")
                    else:
                        result = None
                else:
                    # Direct interactive build: resolve the environment from
                    # the job's config spec and build a standalone env image
                    # with the user's code copied into it. The shared access
                    # image is ensured inside build_push_and_clean.
                    env_config = job.get("config") or {}
                    base_image_tag = resolve_interactive_base_image(env_config)
                    logger.info(
                        "Direct interactive build for %s: env=%s -> base=%s",
                        job_id, env_config, base_image_tag,
                    )
                    archive_bytes = download_job_archive(object_key)
                    extract_dir = extract_job_archive(archive_bytes, job_id)
                    project_dir = find_project_dir(extract_dir)
                    result = build_push_and_clean(
                        client, job_id, project_dir, command="",
                        base_image=base_image_tag, build_type="interactive",
                        include_project=True,
                    )
            else:
                archive_bytes = download_job_archive(object_key)
                extract_dir = extract_job_archive(archive_bytes, job_id)
                project_dir = find_project_dir(extract_dir)
                result = build_push_and_clean(client, job_id, project_dir, command, base_image)
        except Exception as e:
            logger.error("Failed while processing job %s: %s", job_id, e, exc_info=True)
            result = ("system", f"Unexpected error while processing job: {e}")
        finally:
            if extract_dir:
                shutil.rmtree(extract_dir, ignore_errors=True)

        if result is None:
            if build_type == "interactive":
                notified = notify_scheduler_interactive_ready(job_id)
            else:
                notified = notify_scheduler_job_ready(job_id)
            if notified:
                mark_job_processed(job_id)
                logger.info("Job %s completed.", job_id)
            else:
                logger.error("Job %s built but scheduler notification failed, will retry.", job_id)
        else:
            failure_type, failure_reason = result
            if failure_type == "user":
                notified = notify_scheduler_job_failed(job_id, failure_type, failure_reason)
                if notified:
                    logger.error("Job %s failed (user code), reported to scheduler.", job_id)
                else:
                    logger.error("Job %s failed (user code), scheduler not notified, will retry.", job_id)
            else:
                logger.warning(
                    "Job %s failed (builder/system issue), keeping pending for retry: %s",
                    job_id, failure_reason,
                )

def main():
    logger.info("Docker Image Builder service starting ...")
    logger.info("Watching scheduler queue: %s", SCHEDULER_QUEUE_URL)
    
    init_db()

    while True:
        try:
            scan_and_process()
        except Exception as e:
            logger.error("Error during scan cycle: %s", e, exc_info=True)
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()