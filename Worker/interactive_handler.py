import logging
import re
import threading
import time

import requests

import config
from telemetry import record_event

logger = logging.getLogger("interactive_handler")

# Env var names MUST match what interactive-entrypoint.sh reads.
IP_LOG_RE = re.compile(r"Tailscale IP:\s*(100\.\d+\.\d+\.\d+)")
IP_DETECT_TIMEOUT = float(config.INTERACTIVE_IP_TIMEOUT)
IP_POLL_INTERVAL = 1.0

# session_id -> docker container id (for monitoring / future stop support)
_active_containers: dict[str, str] = {}
_lock = threading.Lock()


def run_interactive_container(api, session_id: str, image_tag: str,
                              headscale_url: str, headscale_auth_key: str,
                              ssh_public_key: str) -> str:
    """Run an interactive sandbox container and report its tailnet IP.

    1. Pull the interactive image.
    2. `docker run -d` with TUN device + NET_ADMIN/NET_RAW caps and exactly
       the env vars interactive-entrypoint.sh expects.
    3. Poll `docker logs` for "Tailscale IP: 100.x.x.x".
    4. POST /interactive/report_ip to the Scheduler.
    5. Monitor the container in a background thread; report stopped on exit.
    """
    import subprocess

    record_event("info", f"Interactive session {session_id} deployment started")

    # 1. Pull image
    pull = subprocess.run(
        ["docker", "pull", image_tag], capture_output=True, text=True
    )
    if pull.returncode != 0:
        reason = pull.stderr.strip() or f"Failed to pull {image_tag}"
        logger.error("Interactive %s: %s", session_id, reason)
        api.report_interactive_ip(session_id, None, "FAILED")
        return ""

    cmd = [
        "docker", "run", "-d", "--rm",
        "--device", "/dev/net/tun",
        "--cap-add", "NET_ADMIN",
        "--cap-add", "NET_RAW",
        "-e", f"HEADSCALE_URL={headscale_url}",
        "-e", f"HEADSCALE_AUTHKEY={headscale_auth_key}",
        "-e", f"SESSION_ID={session_id}",
        "-e", f"SSH_PUBLIC_KEY={ssh_public_key}",
        "--name", _container_name(session_id),
        image_tag,
    ]
    logger.info("Running interactive container: %s", " ".join(cmd))

    run = subprocess.run(cmd, capture_output=True, text=True)
    if run.returncode != 0:
        reason = run.stderr.strip() or "Interactive container failed to start"
        logger.error("Interactive %s: %s", session_id, reason)
        api.report_interactive_ip(session_id, None, "FAILED")
        return ""

    container_id = run.stdout.strip()
    with _lock:
        _active_containers[session_id] = container_id

    # 3. Poll logs for the tailnet IP.
    headscale_ip = _wait_for_tailscale_ip(session_id)
    if not headscale_ip:
        logger.error(
            "Interactive %s: no Tailscale IP within %.0fs; stopping container.",
            session_id, IP_DETECT_TIMEOUT,
        )
        _stop_container(session_id)
        api.report_interactive_ip(session_id, None, "FAILED")
        return ""

    # 4. Report to scheduler.
    api.report_interactive_ip(session_id, headscale_ip, "RUNNING")
    record_event("info", f"Interactive session {session_id} running at {headscale_ip}")

    # 5. Monitor for exit in background.
    monitor = threading.Thread(
        target=_monitor_container,
        args=(api, session_id),
        name=f"interactive-monitor-{session_id[:8]}",
        daemon=True,
    )
    monitor.start()

    return container_id


def _container_name(session_id: str) -> str:
    return f"interactive-{session_id[:24]}"


def _wait_for_tailscale_ip(session_id: str) -> str | None:
    import subprocess

    deadline = time.time() + IP_DETECT_TIMEOUT
    while time.time() < deadline:
        logs = subprocess.run(
            ["docker", "logs", _container_name(session_id)],
            capture_output=True, text=True,
        )
        match = IP_LOG_RE.search(logs.stdout + logs.stderr)
        if match:
            return match.group(1)

        # Container died before printing an IP -> fail fast.
        state = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", _container_name(session_id)],
            capture_output=True, text=True,
        )
        if state.stdout.strip() != "true":
            return None

        time.sleep(IP_POLL_INTERVAL)
    return None


def _monitor_container(api, session_id: str):
    """Report 'stopped' once the container exits."""
    import subprocess

    name = _container_name(session_id)
    while True:
        state = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", name],
            capture_output=True, text=True,
        )
        if state.returncode != 0 or state.stdout.strip() != "running":
            break
        time.sleep(5)

    with _lock:
        _active_containers.pop(session_id, None)
    logger.info("Interactive container for session %s exited; reporting stopped.", session_id)
    try:
        api.report_interactive_ip(session_id, None, "STOPPED")
    except Exception as e:
        logger.error("Failed to report stopped state for %s: %s", session_id, e)


def _stop_container(session_id: str) -> bool:
    import subprocess

    result = subprocess.run(
        ["docker", "stop", "-t", "10", _container_name(session_id)],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        with _lock:
            _active_containers.pop(session_id, None)
        return True
    logger.warning("Failed to stop interactive container %s: %s",
                   session_id, result.stderr.strip())
    return False


def stop_session_container(session_id: str) -> bool:
    """Stop a running interactive container (used by a future stop endpoint)."""
    with _lock:
        exists = session_id in _active_containers
    if not exists:
        return False
    return _stop_container(session_id)


def get_active_sessions() -> list[str]:
    with _lock:
        return list(_active_containers.keys())
