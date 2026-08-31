import logging
import re
import threading
import time

import requests

import config
from telemetry import record_event

logger = logging.getLogger("interactive_handler")

# Env var names MUST match what access-entrypoint.sh reads.
IP_LOG_RE = re.compile(r"Tailscale IP:\s*(100\.\d+\.\d+\.\d+)")
IP_DETECT_TIMEOUT = float(config.INTERACTIVE_IP_TIMEOUT)
IP_POLL_INTERVAL = 1.0

# Shared access image (sshd + tailscaled) used by all interactive sessions.
ACCESS_IMAGE = config.INTERACTIVE_ACCESS_IMAGE

SESSION_TIMEOUT = float(config.INTERACTIVE_SESSION_TIMEOUT)
NO_CONNECT_TIMEOUT = float(config.INTERACTIVE_NO_CONNECT_TIMEOUT)

# session_id -> dict of container info (env + access) for monitoring / stop.
_active_containers: dict[str, dict] = {}
_lock = threading.Lock()


def run_interactive_session(api, session_id: str, env_image_tag: str,
                            access_image_tag: str, headscale_url: str,
                            headscale_auth_key: str, ssh_public_key: str) -> str:
    """Run an interactive two-container session and report its tailnet IP.

    Architecture: a clean env container (training image, no SSH/Headscale)
    runs ``sleep infinity`` and stays alive. A separate access container
    (shared ``aorko123/access-sshd:latest`` image) joins the env container's
    PID namespace via ``--pid container:<env>`` and routes the user's shell
    into it via ``nsenter`` in sshd's ``ForceCommand``.

    1. Pull env_image_tag and access_image_tag.
    2. Start the env container: ``docker run -d --rm --gpus all <env> sleep infinity``.
    3. Start the access container: ``docker run -d --rm --pid container:<env>
       --userns=host --cap-add ALL --device /dev/net/tun -e ... <access>``.
    4. Poll ``docker logs <access>`` for "Tailscale IP: 100.x.x.x".
    5. POST /interactive/report_ip to the Scheduler.
    6. Monitor BOTH containers; on either exit, stop both and report STOPPED.
    """
    import subprocess

    record_event("info", f"Interactive session {session_id} deployment started")

    # 1. Pull images
    for image_tag in (env_image_tag, access_image_tag):
        pull = subprocess.run(
            ["docker", "pull", image_tag], capture_output=True, text=True
        )
        if pull.returncode != 0:
            reason = pull.stderr.strip() or f"Failed to pull {image_tag}"
            logger.error("Interactive %s: %s", session_id, reason)
            api.report_interactive_ip(session_id, None, "FAILED")
            return ""

    env_name = _container_name(session_id, "env")
    access_name = _container_name(session_id, "access")

    # 2. Start env container (clean training image, stays alive via sleep infinity)
    env_cmd = [
        "docker", "run", "-d", "--rm",
        "--gpus", "all",
        "--name", env_name,
        env_image_tag,
        "sleep", "infinity",
    ]
    logger.info("Running env container: %s", " ".join(env_cmd))
    env_run = subprocess.run(env_cmd, capture_output=True, text=True)
    if env_run.returncode != 0:
        reason = env_run.stderr.strip() or "Env container failed to start"
        logger.error("Interactive %s: %s", session_id, reason)
        api.report_interactive_ip(session_id, None, "FAILED")
        return ""

    env_container_id = env_run.stdout.strip()

    # 3. Start access container (shared PID namespace with env container)
    access_cmd = [
        "docker", "run", "-d", "--rm",
        "--pid", f"container:{env_name}",
        # Run in the host user namespace with all capabilities so nsenter (run as
        # root via setuid) can enter the env container's namespaces. --userns=host
        # is required even when the daemon has userns-remap enabled (--privileged
        # alone does NOT disable userns-remap). --cap-add ALL covers SYS_ADMIN,
        # SYS_PTRACE, NET_ADMIN, NET_RAW.
        "--userns=host",
        "--cap-add", "ALL",
        "--device", "/dev/net/tun",
        "-e", f"HEADSCALE_URL={headscale_url}",
        "-e", f"HEADSCALE_AUTHKEY={headscale_auth_key}",
        "-e", f"SESSION_ID={session_id}",
        "-e", f"SSH_PUBLIC_KEY={ssh_public_key}",
        "--name", access_name,
        access_image_tag,
    ]
    logger.info("Running access container: %s", " ".join(access_cmd))
    access_run = subprocess.run(access_cmd, capture_output=True, text=True)
    if access_run.returncode != 0:
        reason = access_run.stderr.strip() or "Access container failed to start"
        logger.error("Interactive %s: %s", session_id, reason)
        _stop_container(session_id)
        api.report_interactive_ip(session_id, None, "FAILED")
        return ""

    access_container_id = access_run.stdout.strip()

    with _lock:
        _active_containers[session_id] = {
            "env_id": env_container_id,
            "access_id": access_container_id,
            "env_name": env_name,
            "access_name": access_name,
        }

    # 4. Poll access container logs for the tailnet IP.
    headscale_ip = _wait_for_tailscale_ip(session_id)
    if not headscale_ip:
        logger.error(
            "Interactive %s: no Tailscale IP within %.0fs; stopping containers.",
            session_id, IP_DETECT_TIMEOUT,
        )
        _stop_container(session_id)
        api.report_interactive_ip(session_id, None, "FAILED")
        return ""

    # 5. Report to scheduler.
    api.report_interactive_ip(session_id, headscale_ip, "RUNNING")
    record_event("info", f"Interactive session {session_id} running at {headscale_ip}")

    # 6. Monitor both containers in background.
    monitor = threading.Thread(
        target=_monitor_container,
        args=(api, session_id),
        name=f"interactive-monitor-{session_id[:8]}",
        daemon=True,
    )
    monitor.start()

    return access_container_id


def _container_name(session_id: str, role: str = "env") -> str:
    """Generate a deterministic container name for a session's env or access container."""
    return f"interactive-{session_id[:24]}-{role}"


def _wait_for_tailscale_ip(session_id: str) -> str | None:
    import subprocess

    access_name = _container_name(session_id, "access")
    deadline = time.time() + IP_DETECT_TIMEOUT
    while time.time() < deadline:
        logs = subprocess.run(
            ["docker", "logs", access_name],
            capture_output=True, text=True,
        )
        match = IP_LOG_RE.search(logs.stdout + logs.stderr)
        if match:
            return match.group(1)

        # Access container died before printing an IP -> fail fast.
        state = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", access_name],
            capture_output=True, text=True,
        )
        if state.stdout.strip() != "true":
            return None

        time.sleep(IP_POLL_INTERVAL)
    return None


def _has_active_connection(session_id: str) -> bool:
    """Return True if the access container currently has an active sshd session."""
    import subprocess

    access_name = _container_name(session_id, "access")
    try:
        result = subprocess.run(
            ["docker", "exec", access_name, "sh", "-c",
             'grep -la "sshd:" /proc/[0-9]*/cmdline 2>/dev/null | wc -l'],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            count = result.stdout.strip()
            return count.isdigit() and int(count) > 0
    except Exception:
        pass
    return False


def _monitor_container(api, session_id: str):
    """Report 'stopped' once either the env or access container exits."""
    import subprocess

    with _lock:
        info = _active_containers.get(session_id)
    if not info:
        return

    env_name = info["env_name"]
    access_name = info["access_name"]

    started_at = time.time()
    ever_connected = False
    stop_reason = "container_exit"

    while True:
        env_state = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", env_name],
            capture_output=True, text=True,
        )
        access_state = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", access_name],
            capture_output=True, text=True,
        )
        env_running = env_state.returncode == 0 and env_state.stdout.strip() == "running"
        access_running = access_state.returncode == 0 and access_state.stdout.strip() == "running"
        if not env_running or not access_running:
            stop_reason = "container_exit"
            break
        if _has_active_connection(session_id):
            ever_connected = True
        elapsed = time.time() - started_at
        if SESSION_TIMEOUT > 0 and elapsed >= SESSION_TIMEOUT:
            stop_reason = "session_timeout"
            break
        if NO_CONNECT_TIMEOUT > 0 and not ever_connected and elapsed >= NO_CONNECT_TIMEOUT:
            stop_reason = "no_connect_timeout"
            break
        time.sleep(5)

    with _lock:
        _active_containers.pop(session_id, None)
    logger.info("Interactive containers for session %s stopped (%s); reporting stopped.", session_id, stop_reason)
    _stop_container(session_id)
    try:
        api.report_interactive_ip(session_id, None, "STOPPED")
    except Exception as e:
        logger.error("Failed to report stopped state for %s: %s", session_id, e)


def _stop_container(session_id: str) -> bool:
    """Stop both the env and access containers for a session."""
    import subprocess

    env_name = _container_name(session_id, "env")
    access_name = _container_name(session_id, "access")

    success = True
    for name in (env_name, access_name):
        result = subprocess.run(
            ["docker", "stop", "-t", "10", name],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            logger.warning("Failed to stop container %s: %s",
                           name, result.stderr.strip())
            success = False

    with _lock:
        _active_containers.pop(session_id, None)
    return success


def stop_session_container(session_id: str) -> bool:
    """Stop both containers for a running interactive session (used by the
    heartbeat stop command)."""
    with _lock:
        exists = session_id in _active_containers
    if not exists:
        return False
    return _stop_container(session_id)


def get_active_sessions() -> list[str]:
    with _lock:
        return list(_active_containers.keys())
