import logging
import threading

import docker

import config
import runtime_config
import job_state
from telemetry import record_event

logger = logging.getLogger("container_health")

# Docker container states that indicate the container is healthy.
# "INTERACTIVE_READY" is a scheduler-side status, not a Docker state, but
# included per the requirement — no Docker container will ever report it.
HEALTHY_STATUSES = {"running", "INTERACTIVE_READY"}

# Containers managed by interactive_handler are named
# "interactive-{session_id[:24]}-{role}" and have their own lifecycle
# monitoring — skip them.
INTERACTIVE_PREFIX = "interactive-"

# Seed containers are ephemeral (created briefly to prepare output mounts).
SEED_PREFIX = "seed-"

# VRAM estimation containers are transient (docker run --rm via subprocess) but
# use the same image pattern as training containers — skip them.
VRAM_PREFIX = "vram-"

# Training containers launched by the executor are named
# "train-<job_id>-<uuid>" so the health check can identify them reliably
# (image tags are unreliable for containers in the "created" state).
TRAIN_PREFIX = "train-"

# Image prefix for training containers launched by the executor.
_TRAINING_IMAGE_PREFIX = f"{config.DOCKER_HUB_USERNAME}/"


def check_and_cleanup_containers():
    """Scan all Docker containers and clean up unhealthy or orphaned ones.

    Two categories of containers are targeted:

    1. **Orphaned / stale training containers** — containers whose image
       matches the ``DOCKER_HUB_USERNAME/<job_id>:latest`` pattern but that
       have no corresponding active job in ``job_state`` (or belong to a
       *different* job).  This catches:
       - A training container left running after its job completed on another
         worker and was reassigned here.
       - A container from a previous run that the executor failed to clean up
         (e.g. worker crash between ``docker run`` and ``Popen`` exit).

       Training containers are checked **before** the unhealthy-status check
       so that a ``created``-state container the executor is currently starting
       (which matches the active job) is not accidentally killed.

    2. **Unhealthy containers** — any non-interactive, non-training container
       whose Docker status is not ``running`` or ``INTERACTIVE_READY`` (e.g.
       exited, dead, created, paused).  These are leftover from jobs that
       finished or crashed but whose container was never removed.
    """
    try:
        client = docker.from_env()
    except Exception as e:
        logger.error("Cannot connect to Docker daemon for health check: %s", e)
        return

    try:
        containers = client.containers.list(all=True)
    except Exception as e:
        logger.error("Failed to list containers for health check: %s", e)
        return

    active_job_id = _get_active_job_id()

    for container in containers:
        name = container.name or ""

        # Skip containers managed by other subsystems.
        if (
            name.startswith(INTERACTIVE_PREFIX)
            or name.startswith(SEED_PREFIX)
            or name.startswith(VRAM_PREFIX)
        ):
            continue

        # Training containers are only legitimate if they belong to the
        # currently active job. Kill anything else — an orphan (no active job)
        # or a stale container from a job reassigned to another worker —
        # regardless of Docker status. This also protects containers the
        # executor is currently starting: the executor persists the job id
        # BEFORE docker run, so a starting container always matches the active
        # job and is skipped here.
        #
        # Identification is name-based first (the executor names training
        # containers "train-<job_id>-<uuid>"), which is reliable even for
        # "created" containers where image tags may be unavailable. The
        # image-tag check is kept as a fallback for pre-existing containers
        # not named by the executor.
        if name.startswith(TRAIN_PREFIX):
            container_job_id = _extract_job_id_from_name(name)
        elif _is_training_container(container):
            container_job_id = _extract_job_id(container)
        else:
            container_job_id = None

        if container_job_id is not None:
            if container_job_id != active_job_id:
                reason = (
                    "orphaned" if active_job_id is None
                    else f"stale (expected job {active_job_id}, got {container_job_id})"
                )
                logger.warning(
                    "Training container %s (id=%s, job=%s) is %s; killing it.",
                    name, container.short_id, container_job_id, reason,
                )
                record_event(
                    "warn",
                    f"Killing {reason} training container {name} (job={container_job_id})",
                )
                _kill_container(container)
            continue

        # Non-training containers: kill any in an unhealthy Docker state.
        if container.status not in HEALTHY_STATUSES:
            logger.warning(
                "Container %s (id=%s) has unhealthy status '%s'; killing it.",
                name, container.short_id, container.status,
            )
            record_event(
                "warn",
                f"Killing unhealthy container {name} (status={container.status})",
            )
            _kill_container(container)


def _get_active_job_id() -> str | None:
    """Return the job_id this worker should be executing, or None."""
    state = job_state.load_running_job()
    if state:
        return state.get("job_id")
    return None


def _container_image_tags(container) -> list[str]:
    """Return the container image's tags, or ``[]`` if they can't be read.

    ``container.image`` performs a Docker lookup and can raise if the image
    was deleted while the container still exists — treat that as "unknown".
    """
    try:
        image = container.image
        return list(image.tags) if image and image.tags else []
    except Exception:
        return []


def _is_training_container(container) -> bool:
    """Return True if the container's image matches the training image pattern."""
    return any(
        tag.startswith(_TRAINING_IMAGE_PREFIX)
        for tag in _container_image_tags(container)
    )


def _extract_job_id(container) -> str | None:
    """Extract the job_id from a training container's image name.

    Training images follow the pattern ``DOCKER_HUB_USERNAME/<job_id>:latest``.
    """
    for tag in _container_image_tags(container):
        if tag.startswith(_TRAINING_IMAGE_PREFIX):
            remainder = tag[len(_TRAINING_IMAGE_PREFIX):]
            # Strip the tag suffix (e.g. ":latest")
            if ":" in remainder:
                remainder = remainder.rsplit(":", 1)[0]
            return remainder if remainder else None
    return None


def _extract_job_id_from_name(name: str) -> str | None:
    """Extract the job_id from a training container's name.

    Training containers launched by the executor are named
    ``train-<job_id>-<uuid>`` (the uuid is a 12-char hex string with no
    dashes).  The job_id is everything between the ``train-`` prefix and the
    trailing uuid segment.
    """
    if not name.startswith(TRAIN_PREFIX):
        return None
    remainder = name[len(TRAIN_PREFIX):]
    # Split off the trailing uuid segment (the last "-"-separated part).
    if "-" not in remainder:
        return None
    return remainder.rsplit("-", 1)[0]


def _kill_container(container):
    """Forcefully kill a container, removing it if kill fails.

    For containers that are not running (e.g. ``created``, ``exited``,
    ``dead``), ``kill()`` raises a 409 error because there is no running
    process to signal — skip straight to ``remove(force=True)`` in those
    cases to avoid the error.
    """
    name = container.name or container.short_id
    if container.status != "running":
        try:
            container.remove(force=True)
            logger.info("Force-removed non-running container %s (status=%s).",
                        name, container.status)
        except Exception as e:
            logger.error("Failed to remove container %s: %s", name, e)
        return

    try:
        container.kill()
        logger.info("Killed container %s.", name)
    except Exception as e:
        logger.warning("kill() failed for %s (%s); forcing remove.", name, e)
        try:
            container.remove(force=True)
            logger.info("Force-removed container %s.", name)
        except Exception as e2:
            logger.error("Failed to remove container %s: %s", name, e2)


def container_health_loop(stop_event: threading.Event):
    """Periodically scan containers and clean up unhealthy or orphaned ones."""
    logger.info("Container health thread started.")
    record_event("info", "Container health thread started")
    while not stop_event.is_set():
        try:
            check_and_cleanup_containers()
        except Exception as e:
            logger.error("Error during container health check: %s", e)
        stop_event.wait(runtime_config.get("container_health_interval"))
