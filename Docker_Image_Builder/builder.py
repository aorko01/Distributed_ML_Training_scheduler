import os
import io
import re
import time
import shutil
import zipfile
import tempfile
import threading
import docker
from concurrent.futures import ThreadPoolExecutor

from config import logger, POLL_INTERVAL, MAX_CONCURRENT_BUILDS, SCHEDULER_QUEUE_URL
from config import BUILDER_ID, HEARTBEAT_INTERVAL, HEARTBEAT_TIMEOUT
from database import init_db
from api import (
    download_job_archive, notify_scheduler_job_ready, notify_scheduler_job_failed,
    claim_job_for_building, release_job_to_not_runnable,
    send_builder_heartbeat,
)
from docker_ops import (
    docker_login,
    build_push_and_clean,
    image_tag_for_attempt,
    prune_old_base_images,
)


class BuildLeaseRegistry:
    """Thread-safe active-build registry shared with the heartbeat thread."""

    def __init__(self, lease_timeout: float = HEARTBEAT_TIMEOUT):
        self._lock = threading.Lock()
        self._active: dict[str, tuple[str, threading.Event]] = {}
        self._last_heartbeat: float | None = None
        self._lease_timeout = lease_timeout

    def register(self, job_id: str, attempt_id: str) -> threading.Event:
        cancel_event = threading.Event()
        with self._lock:
            self._active[attempt_id] = (job_id, cancel_event)
        return cancel_event

    def unregister(self, attempt_id: str) -> None:
        with self._lock:
            self._active.pop(attempt_id, None)

    def heartbeat_payload(self) -> list[dict[str, str]]:
        with self._lock:
            return [
                {"job_id": job_id, "attempt_id": attempt_id}
                for attempt_id, (job_id, _event) in self._active.items()
            ]

    def accept_heartbeat(self, response: dict) -> None:
        now = time.monotonic()
        with self._lock:
            self._last_heartbeat = now
            timeout = response.get("heartbeat_timeout_seconds")
            if isinstance(timeout, (int, float)) and timeout > 0:
                self._lease_timeout = float(timeout)

            for stale in response.get("cancel_builds", []):
                active = self._active.get(stale.get("attempt_id"))
                if active and active[0] == stale.get("job_id"):
                    active[1].set()

    def scheduler_available(self) -> bool:
        with self._lock:
            return bool(
                self._last_heartbeat is not None
                and time.monotonic() - self._last_heartbeat < self._lease_timeout
            )

    def should_cancel(self, attempt_id: str) -> bool:
        with self._lock:
            active = self._active.get(attempt_id)
            if active is None:
                return True
            heartbeat_expired = bool(
                self._last_heartbeat is None
                or time.monotonic() - self._last_heartbeat >= self._lease_timeout
            )
            if heartbeat_expired:
                active[1].set()
            return active[1].is_set()

    def cancel_all(self) -> None:
        with self._lock:
            for _job_id, cancel_event in self._active.values():
                cancel_event.set()


def heartbeat_loop(
    registry: BuildLeaseRegistry, stop_event: threading.Event
) -> None:
    while not stop_event.is_set():
        response = send_builder_heartbeat(BUILDER_ID, registry.heartbeat_payload())
        if response is not None:
            registry.accept_heartbeat(response)
        stop_event.wait(HEARTBEAT_INTERVAL)

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

def _wait_for_retry(stop_event: threading.Event | None) -> None:
    """Wait between polls, while allowing a graceful service shutdown."""
    if stop_event is None:
        time.sleep(POLL_INTERVAL)
    else:
        stop_event.wait(POLL_INTERVAL)


def _release_for_retry(job_id: str, attempt_id: str, reason: str) -> None:
    """Return a claimed job to the queue rather than stranding it in flight."""
    if release_job_to_not_runnable(job_id, BUILDER_ID, attempt_id):
        logger.info("Job %s released for retry after %s.", job_id, reason)
    else:
        logger.error("Could not release job %s for retry after %s.", job_id, reason)


def worker_loop(
    client: docker.DockerClient,
    stop_event: threading.Event | None = None,
    registry: BuildLeaseRegistry | None = None,
):
    """Each builder thread runs this indefinitely.

    Atomically claims one NOT_RUNNABLE job at a time via the scheduler, builds
    its image, and reports the outcome. On system-level failures the job is
    released back to NOT_RUNNABLE so another thread/instance can retry it.
    """
    while stop_event is None or not stop_event.is_set():
        try:
            if registry is not None and not registry.scheduler_available():
                _wait_for_retry(stop_event)
                continue

            job = claim_job_for_building(BUILDER_ID)

            if job is None:
                _wait_for_retry(stop_event)
                continue

            job_id = job.get("id")
            object_key = job.get("object_key")
            command = job.get("command", "")
            base_image = job.get("docker_base_image")
            attempt_id = job.get("image_build_attempt_id")

            if not job_id or not object_key or not base_image or not attempt_id:
                logger.warning("Skipping malformed job payload: %s", job)
                if job_id and attempt_id:
                    _release_for_retry(job_id, attempt_id, "a malformed job payload")
                continue

            logger.info("=" * 50)
            logger.info("Processing job: %s", job_id)

            extract_dir = None
            result = None

            if registry is not None:
                registry.register(job_id, attempt_id)

            try:
                archive_bytes = download_job_archive(object_key)
                extract_dir = extract_job_archive(archive_bytes, job_id)
                project_dir = find_project_dir(extract_dir)
                result = build_push_and_clean(
                    client,
                    job_id,
                    project_dir,
                    command,
                    base_image,
                    build_attempt_id=attempt_id,
                    should_cancel=(
                        lambda: registry.should_cancel(attempt_id)
                        if registry is not None
                        else False
                    ),
                )
            except Exception as e:
                logger.error("Failed while processing job %s: %s", job_id, e, exc_info=True)
                result = ("system", f"Unexpected error while processing job: {e}")
            finally:
                if extract_dir:
                    shutil.rmtree(extract_dir, ignore_errors=True)
                if registry is not None:
                    registry.unregister(attempt_id)

            if result is None:
                notified = notify_scheduler_job_ready(
                    job_id,
                    BUILDER_ID,
                    attempt_id,
                    image_tag_for_attempt(job_id, attempt_id),
                )
                if notified:
                    logger.info("Job %s completed.", job_id)
                else:
                    logger.error(
                        "Job %s built but scheduler notification failed; releasing for retry.",
                        job_id,
                    )
                    _release_for_retry(
                        job_id, attempt_id, "a failed ready notification"
                    )
            else:
                failure_type, failure_reason = result
                if failure_type == "cancelled":
                    logger.warning(
                        "Cancelled stale image-build attempt %s for job %s.",
                        attempt_id,
                        job_id,
                    )
                elif failure_type == "user":
                    notified = notify_scheduler_job_failed(
                        job_id,
                        failure_type,
                        failure_reason,
                        BUILDER_ID,
                        attempt_id,
                    )
                    if notified:
                        logger.error("Job %s failed (user code), reported to scheduler.", job_id)
                    else:
                        logger.error(
                            "Job %s failed (user code), scheduler not notified; releasing for retry.",
                            job_id,
                        )
                        _release_for_retry(
                            job_id, attempt_id, "a failed failure notification"
                        )
                else:
                    logger.warning(
                        "Job %s failed (builder/system issue), releasing for retry: %s",
                        job_id, failure_reason,
                    )
                    _release_for_retry(job_id, attempt_id, "a system failure")

        except Exception as e:
            logger.error("Unexpected error in worker loop: %s", e, exc_info=True)
            _wait_for_retry(stop_event)


def _worker_entry(
    stop_event: threading.Event, registry: BuildLeaseRegistry
) -> None:
    """Give each worker thread its own Docker client/session."""
    while not stop_event.is_set():
        client = None
        try:
            client = docker.from_env()
            docker_login(client)
            worker_loop(client, stop_event, registry)
        except Exception as e:
            logger.error("Docker builder thread failed: %s", e, exc_info=True)
            stop_event.wait(POLL_INTERVAL)
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass


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

    stop_event = threading.Event()
    registry = BuildLeaseRegistry()
    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        args=(registry, stop_event),
        name="image-builder-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_BUILDS) as executor:
        futures = [
            executor.submit(_worker_entry, stop_event, registry)
            for _ in range(MAX_CONCURRENT_BUILDS)
        ]
        # Main thread stays alive to handle periodic pruning
        last_prune_time = time.monotonic()
        PRUNE_INTERVAL = 86400  # 24 hours in seconds
        try:
            while not stop_event.is_set():
                now = time.monotonic()
                if now - last_prune_time >= PRUNE_INTERVAL:
                    try:
                        prune_old_base_images(client)
                    except Exception as e:
                        logger.error("Error during prune cycle: %s", e, exc_info=True)
                    last_prune_time = now
                stop_event.wait(60)
        except KeyboardInterrupt:
            logger.info("Stopping Docker Image Builder service ...")
        finally:
            stop_event.set()
            registry.cancel_all()
            heartbeat_thread.join(timeout=2.0)
    try:
        client.close()
    except Exception:
        pass

if __name__ == "__main__":
    main()
