import logging
import threading

import docker

import runtime_config
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


def check_and_cleanup_containers():
    """List all Docker containers; kill any non-interactive ones that are not in a healthy status."""
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

    for container in containers:
        name = container.name or ""
        if name.startswith(INTERACTIVE_PREFIX):
            continue
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


def _kill_container(container):
    """Forcefully kill a container, removing it if kill fails."""
    name = container.name or container.short_id
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
    """Periodically scan containers and clean up unhealthy ones."""
    logger.info("Container health thread started.")
    record_event("info", "Container health thread started")
    while not stop_event.is_set():
        try:
            check_and_cleanup_containers()
        except Exception as e:
            logger.error("Error during container health check: %s", e)
        stop_event.wait(runtime_config.get("container_health_interval"))
