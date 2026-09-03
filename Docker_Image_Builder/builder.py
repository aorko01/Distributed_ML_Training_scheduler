import os
import shutil
import tempfile
import zipfile
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import docker

from config import (
    logger, POLL_INTERVAL, SCHEDULER_QUEUE_URL,
    MAX_CONCURRENT_JOBS, PRUNE_INTERVAL_HOURS,
)
from database import init_db, is_job_processed, mark_job_processed
from api import (
    fetch_unbuilt_jobs, download_job_archive,
    notify_scheduler_job_ready, notify_scheduler_interactive_ready,
    notify_scheduler_job_failed,
)
from docker_ops import (
    docker_login, build_image, push_image, delete_local_image,
    prune_old_base_images, resolve_interactive_base_image, ensure_access_image,
)


def find_project_dir(extracted_dir: str) -> str:
    for entry in sorted(os.listdir(extracted_dir)):
        if entry.startswith("__") or entry.startswith("."):
            continue
        candidate = os.path.join(extracted_dir, entry)
        if os.path.isdir(candidate):
            return candidate
    return extracted_dir


def extract_job_archive(archive_path: str, job_id: str) -> str:
    extract_dir = tempfile.mkdtemp(prefix=f"job_{job_id}_")
    try:
        with zipfile.ZipFile(archive_path, "r") as zip_ref:
            zip_ref.extractall(extract_dir)
    except Exception:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise
    return extract_dir


def process_job(job, client, push_executor, delete_executor):
    """Process a single job: download, build, push (background), notify."""
    job_id = job.get("id")
    object_key = job.get("object_key")
    command = job.get("command", "")
    base_image = job.get("docker_base_image")
    build_type = job.get("build_type", "training")
    base_job_id = job.get("base_job_id")

    if not job_id or (build_type != "interactive" and (not object_key or not base_image)):
        logger.warning("Skipping malformed job payload: %s", job)
        return

    if build_type == "interactive":
        if not base_job_id and not object_key:
            logger.warning("Skipping malformed interactive job payload: %s", job)
            return

    if is_job_processed(job_id):
        logger.info("Job %s already built, re-notifying...", job_id)
        if build_type == "interactive":
            notify_scheduler_interactive_ready(job_id)
        else:
            notify_scheduler_job_ready(job_id)
        return

    logger.info("=" * 50)
    logger.info("Processing job: %s", job_id)

    extract_dir = None
    build_result = None
    archive_path = None

    try:
        if build_type == "interactive":
            if base_job_id:
                if not ensure_access_image(client):
                    build_result = ("system", "Failed to pull access image")
                else:
                    # No build needed for derived interactive — just notify
                    notified = notify_scheduler_interactive_ready(job_id)
                    if notified:
                        mark_job_processed(job_id)
                        logger.info("Job %s completed (derived interactive).", job_id)
                    return
            else:
                env_config = job.get("config") or {}
                base_image_tag = resolve_interactive_base_image(env_config)
                archive_path = download_job_archive(object_key)
                extract_dir = extract_job_archive(archive_path, job_id)
                project_dir = find_project_dir(extract_dir)
                image_tag = build_image(
                    client, job_id, project_dir, command="",
                    base_image=base_image_tag, build_type="interactive",
                    include_project=True,
                )
        else:
            archive_path = download_job_archive(object_key)
            extract_dir = extract_job_archive(archive_path, job_id)
            project_dir = find_project_dir(extract_dir)
            image_tag = build_image(client, job_id, project_dir, command, base_image)
    except Exception as e:
        logger.error("Failed while processing job %s: %s", job_id, e, exc_info=True)
        build_result = ("system", f"Unexpected error while processing job: {e}")
    finally:
        if extract_dir:
            shutil.rmtree(extract_dir, ignore_errors=True)
        if archive_path and os.path.exists(archive_path):
            os.remove(archive_path)

    # Handle build failure
    if build_result is not None:
        failure_type, failure_reason = build_result
        if failure_type == "user":
            notified = notify_scheduler_job_failed(job_id, failure_type, failure_reason)
            if notified:
                logger.error("Job %s failed (user code), reported.", job_id)
        else:
            logger.warning("Job %s failed (system), keeping pending for retry: %s", job_id, failure_reason)
        return

    # image_tag could be a tuple (failure_type, reason) from build_image
    if 'image_tag' in locals() and isinstance(image_tag, tuple):
        failure_type, failure_reason = image_tag
        if failure_type == "user":
            notified = notify_scheduler_job_failed(job_id, failure_type, failure_reason)
            if notified:
                logger.error("Job %s failed (user code), reported.", job_id)
        else:
            logger.warning("Job %s failed (system), keeping pending for retry: %s", job_id, failure_reason)
        return

    # Build succeeded — push in background thread
    image_tag_str = image_tag  # It's a string at this point

    def _push_and_notify():
        """Push image, then notify scheduler and submit deletion."""
        push_start = time.monotonic()
        push_result = push_image(client, job_id, image_tag_str, build_type)
        push_elapsed = time.monotonic() - push_start
        logger.info("Push job %s took %.2fs", job_id, push_elapsed)

        if push_result is not None:
            _, push_fail_reason = push_result
            logger.error("Push failed for job %s: %s", job_id, push_fail_reason)
            return  # Keep pending for retry

        # Push succeeded — submit image deletion in background
        delete_executor.submit(delete_local_image, client, job_id, image_tag_str)

        # Notify scheduler
        if build_type == "interactive":
            notified = notify_scheduler_interactive_ready(job_id)
        else:
            notified = notify_scheduler_job_ready(job_id)
        if notified:
            mark_job_processed(job_id)
            logger.info("Job %s completed.", job_id)
        else:
            logger.error("Job %s built but scheduler notification failed, will retry.", job_id)

    push_executor.submit(_push_and_notify)


def scan_and_process(client, job_executor, push_executor, delete_executor):
    cycle_start = time.monotonic()
    try:
        jobs = fetch_unbuilt_jobs()
    except Exception as e:
        logger.error("Failed to fetch unbuilt jobs: %s", e)
        return

    submitted = 0
    for job in jobs:
        job_executor.submit(process_job, job, client, push_executor, delete_executor)
        submitted += 1

    cycle_elapsed = time.monotonic() - cycle_start
    logger.info("Cycle: submitted %d jobs in %.2fs", submitted, cycle_elapsed)


def main():
    logger.info("Docker Image Builder service starting ...")
    logger.info("Watching scheduler queue: %s", SCHEDULER_QUEUE_URL)

    init_db()

    # Graceful shutdown
    shutdown_event = threading.Event()

    def _handle_signal(signum, frame):
        logger.info("Received signal %s, shutting down gracefully...", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Shared Docker client
    # Note: docker-py DockerClient is thread-safe for independent HTTP calls.
    client = docker.from_env()
    docker_login(client)

    # Thread pools
    job_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS, thread_name_prefix="job")
    push_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS, thread_name_prefix="push")
    delete_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS, thread_name_prefix="delete")

    # Prune scheduling
    last_prune = 0.0  # Run immediately at startup

    try:
        while not shutdown_event.is_set():
            # Check if it's time to prune
            now = time.monotonic()
            if now - last_prune >= PRUNE_INTERVAL_HOURS * 3600:
                try:
                    prune_old_base_images(client)
                    last_prune = time.monotonic()
                except Exception as e:
                    logger.error("Error during base image pruning: %s", e)

            try:
                scan_and_process(client, job_executor, push_executor, delete_executor)
            except Exception as e:
                logger.error("Error during scan cycle: %s", e, exc_info=True)

            # Sleep in small slices so we can respond to shutdown signals
            for _ in range(POLL_INTERVAL):
                if shutdown_event.is_set():
                    break
                time.sleep(1)
    finally:
        logger.info("Shutting down executors...")
        job_executor.shutdown(wait=True)
        push_executor.shutdown(wait=True)
        delete_executor.shutdown(wait=True)
        logger.info("All executors shut down. Exiting.")


if __name__ == "__main__":
    main()
