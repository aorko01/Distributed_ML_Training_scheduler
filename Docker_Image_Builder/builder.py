import os
import io
import re
import time
import shutil
import zipfile
import tempfile
import docker

from config import logger, POLL_INTERVAL, SCHEDULER_QUEUE_URL
from database import init_db
from api import fetch_unbuilt_jobs, download_job_archive, notify_scheduler_job_ready, notify_scheduler_job_failed
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

def scan_and_process(client: docker.DockerClient):
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
                    "Job %s failed (builder/system issue), keeping pending for retry: %s",
                    job_id, failure_reason,
                )

def main():
    logger.info("Docker Image Builder service starting ...")
    logger.info("Watching scheduler queue: %s", SCHEDULER_QUEUE_URL)
    
    init_db()
    client = docker.from_env()
    docker_login(client)
    prune_old_base_images(client)

    last_prune_time = time.monotonic()
    PRUNE_INTERVAL = 86400  # 24 hours in seconds

    while True:
        try:
            now = time.monotonic()
            if now - last_prune_time >= PRUNE_INTERVAL:
                prune_old_base_images(client)
                last_prune_time = now
            scan_and_process(client)
        except Exception as e:
            logger.error("Error during scan cycle: %s", e, exc_info=True)
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()