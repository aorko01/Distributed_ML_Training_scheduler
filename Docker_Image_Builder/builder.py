import os
import io
import re
import time
import shutil
import zipfile
import tempfile
import docker
from concurrent.futures import ThreadPoolExecutor

from config import logger, POLL_INTERVAL, MAX_CONCURRENT_BUILDS, SCHEDULER_QUEUE_URL
from database import init_db
from api import (
    download_job_archive, notify_scheduler_job_ready, notify_scheduler_job_failed,
    claim_job_for_building, release_job_to_not_runnable,
)
from docker_ops import docker_login, build_push_and_clean, prune_old_base_images

def _sanitize_job_id(job_id: str) -> str:
    """Make a job id safe for use in temp-dir prefixes, tags and paths."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", str(job_id or "job"))
    return safe[:64] or "job"


def find_project_dir(extracted_dir: str) -> str:
    # Prefer a directory (or the root) that actually looks like a project:
    # contains requirements.txt or at least one .py file. Otherwise fall back
    # to the previous behaviour (alphabetically first subdir, else root) so
    # existing callers/tests keep working.
    try:
        entries = sorted(os.listdir(extracted_dir))
    except OSError:
        return extracted_dir
    if os.path.isfile(os.path.join(extracted_dir, "requirements.txt")):
        return extracted_dir
    for entry in entries:
        if entry.startswith("__") or entry.startswith("."):
            continue
        candidate = os.path.join(extracted_dir, entry)
        if os.path.isfile(candidate) and entry.endswith(".py"):
            return extracted_dir
    for entry in entries:
        if entry.startswith("__") or entry.startswith("."):
            continue
        candidate = os.path.join(extracted_dir, entry)
        if os.path.isdir(candidate):
            if os.path.isfile(os.path.join(candidate, "requirements.txt")):
                return candidate
    for entry in entries:
        if entry.startswith("__") or entry.startswith("."):
            continue
        candidate = os.path.join(extracted_dir, entry)
        if os.path.isdir(candidate):
            return candidate
    return extracted_dir

def extract_job_archive(archive_bytes: bytes, job_id: str) -> str:
    safe_job_id = _sanitize_job_id(job_id)
    extract_dir = tempfile.mkdtemp(prefix=f"job_{safe_job_id}_")
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as zip_ref:
            # Safe extraction (ZipSlip protection): reject absolute paths
            # and entries that would escape extract_dir via `..`.
            base = os.path.realpath(extract_dir)
            for member in zip_ref.infolist():
                name = member.filename
                if not name or name.endswith("/"):
                    continue
                if os.path.isabs(name) or re.match(r"^[a-zA-Z]:", name):
                    raise ValueError(f"Unsafe zip entry (absolute path): {name!r}")
                dest = os.path.realpath(os.path.join(base, name))
                if dest != base and not dest.startswith(base + os.sep):
                    raise ValueError(f"Unsafe zip entry (path traversal): {name!r}")
                if member.is_dir():
                    os.makedirs(dest, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(dest) or base, exist_ok=True)
                    with zip_ref.open(member, "r") as src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out)
    except Exception:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise
    return extract_dir

def worker_loop(client: docker.DockerClient):
    """Each builder thread runs this indefinitely.

    Atomically claims one NOT_RUNNABLE job at a time via the scheduler, builds
    its image, and reports the outcome. On system-level failures the job is
    released back to NOT_RUNNABLE so another thread/instance can retry it.
    """
    while True:
        try:
            job = claim_job_for_building()

            if job is None:
                time.sleep(POLL_INTERVAL)
                continue

            job_id = job.get("id")
            object_key = job.get("object_key")
            command = job.get("command", "")
            base_image = job.get("docker_base_image")

            if not job_id or not object_key or not base_image:
                logger.warning("Skipping malformed job payload: %s", job)
                if job_id:
                    release_job_to_not_runnable(job_id)
                continue

            logger.info("=" * 50)
            logger.info("Processing job: %s", job_id)

            extract_dir = None
            result = None

            try:
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
                notified = notify_scheduler_job_ready(job_id)
                if notified:
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
                        "Job %s failed (builder/system issue), releasing for retry: %s",
                        job_id, failure_reason,
                    )
                    release_job_to_not_runnable(job_id)

        except Exception as e:
            logger.error("Unexpected error in worker loop: %s", e, exc_info=True)
            time.sleep(POLL_INTERVAL)


def main():
    logger.info("Docker Image Builder service starting ...")
    logger.info("Watching scheduler queue: %s", SCHEDULER_QUEUE_URL)

    init_db()
    client = docker.from_env()
    docker_login(client)
    try:
        prune_old_base_images(client)
    except Exception as e:
        logger.error("Error during initial base image pruning: %s", e)

    logger.info("Starting %d concurrent builder threads", MAX_CONCURRENT_BUILDS)

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_BUILDS) as executor:
        futures = [executor.submit(worker_loop, client) for _ in range(MAX_CONCURRENT_BUILDS)]
        # Main thread stays alive to handle periodic pruning
        last_prune_time = time.monotonic()
        PRUNE_INTERVAL = 86400  # 24 hours in seconds
        while True:
            try:
                now = time.monotonic()
                if now - last_prune_time >= PRUNE_INTERVAL:
                    prune_old_base_images(client)
                    last_prune_time = now
            except Exception as e:
                logger.error("Error during prune cycle: %s", e, exc_info=True)
            time.sleep(60)

if __name__ == "__main__":
    main()