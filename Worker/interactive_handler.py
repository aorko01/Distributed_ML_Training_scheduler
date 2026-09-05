import json
import logging
import re
import threading
import time

import requests

import config
from telemetry import record_event
from image_cleanup import remove_image

logger = logging.getLogger("interactive_handler")

# Env var names MUST match what access-entrypoint.sh reads.
IP_LOG_RE = re.compile(r"Tailscale IP:\s*(100\.\d+\.\d+\.\d+)")
IP_DETECT_TIMEOUT = float(config.INTERACTIVE_IP_TIMEOUT)
IP_POLL_INTERVAL = 1.0

# Shared access image (sshd + tailscaled) used by all interactive sessions.
ACCESS_IMAGE = config.INTERACTIVE_ACCESS_IMAGE

SESSION_TIMEOUT = float(config.INTERACTIVE_SESSION_TIMEOUT)
NO_CONNECT_TIMEOUT = float(config.INTERACTIVE_NO_CONNECT_TIMEOUT)
IDLE_TIMEOUT = float(config.INTERACTIVE_IDLE_TIMEOUT)

# session_id -> dict of container info (env + access) for monitoring / stop.
_active_containers: dict[str, dict] = {}
_lock = threading.Lock()

# (session_id, image_tag) pairs currently being committed/pushed, to dedupe
# redelivered heartbeat commands.
_in_flight_commits: set[tuple[str, str]] = set()
_commit_lock = threading.Lock()


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

    with _lock:
        existing = _active_containers.get(session_id)
        if existing:
            logger.warning(
                "Interactive %s: session already tracked, skipping duplicate deployment.",
                session_id,
            )
            return existing.get("access_id", "")

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
    if _container_running(env_name):
        logger.warning(
            "Interactive %s: env container %s already running, skipping creation.",
            session_id, env_name,
        )
        env_container_id = _container_id(env_name) or env_name
    else:
        _remove_container_if_exists(env_name)
        env_cmd = [
            "docker", "run", "-d", "--rm",
            "--gpus", "all",
            "--userns=host",
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
    if _container_running(access_name):
        logger.warning(
            "Interactive %s: access container %s already running, skipping creation.",
            session_id, access_name,
        )
        access_container_id = _container_id(access_name) or access_name
    else:
        _remove_container_if_exists(access_name)
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
            _stop_container(session_id, env_image_tag=env_image_tag)
            api.report_interactive_ip(session_id, None, "FAILED")
            return ""

        access_container_id = access_run.stdout.strip()

    with _lock:
        _active_containers[session_id] = {
            "env_id": env_container_id,
            "access_id": access_container_id,
            "env_name": env_name,
            "access_name": access_name,
            "env_image_tag": env_image_tag,
        }

    # Prepare env container for VS Code Remote-SSH (create /home/sandbox, tools).
    _prepare_env_container(env_name, access_name)

    # 4. Poll access container logs for the tailnet IP.
    headscale_ip = _wait_for_tailscale_ip(session_id)
    if headscale_ip:
        logger.info("Interactive %s: found Tailscale IP %s", session_id, headscale_ip)
    else:
        logger.warning("Interactive %s: no Tailscale IP found", session_id)
    if not headscale_ip:
        logger.error(
            "Interactive %s: no Tailscale IP within %.0fs; stopping containers.",
            session_id, IP_DETECT_TIMEOUT,
        )
        _stop_container(session_id, env_image_tag=env_image_tag)
        api.report_interactive_ip(session_id, None, "FAILED")
        return ""

    # 5. Report to scheduler.
    logger.info(
        "Interactive %s: reporting IP %s to scheduler...",
        session_id, headscale_ip,
    )
    api.report_interactive_ip(session_id, headscale_ip, "RUNNING")
    logger.info(
        "Interactive %s: report_interactive_ip call completed for IP %s",
        session_id, headscale_ip,
    )
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


def _container_running(name: str) -> bool:
    """Return True if a container with the given name exists and is running."""
    import subprocess

    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True, text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except Exception:
        return False


def _container_id(name: str) -> str:
    """Best-effort lookup of a container's ID by name; returns "" if unknown."""
    import subprocess

    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.Id}}", name],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def _remove_container_if_exists(name: str) -> None:
    """Remove a stale (non-running) container by name; no-op if it doesn't exist."""
    import subprocess

    try:
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True, text=True,
        )
    except Exception:
        pass


def _prepare_env_container(env_name: str, access_name: str) -> None:
    """Best-effort preparation of the env container for interactive sessions.

    - Creates /home/sandbox owned by the sandbox user (only so sshd's auth user
      has a home for authorized_keys; the shipped shell no longer uses it).
    - Ensures the project dir exists (/workspace, falling back to /output).
    - Ensures a root login profile so ``bash -l`` with ``HOME=/root`` sources
      conda/virtualenv/PATH setup from the base image.
    - Installs basic tools (curl, tar, gzip) if available.

    Never raises — all failures are logged and swallowed so the session can
    still proceed.
    """
    import subprocess

    # 1. Resolve sandbox UID/GID from the access container.
    uid, gid = "1000", "1000"
    try:
        id_out = subprocess.run(
            ["docker", "exec", access_name, "id", "-u", "sandbox"],
            capture_output=True, text=True, timeout=10,
        )
        if id_out.returncode == 0 and id_out.stdout.strip().isdigit():
            uid = id_out.stdout.strip()
        gid_out = subprocess.run(
            ["docker", "exec", access_name, "id", "-g", "sandbox"],
            capture_output=True, text=True, timeout=10,
        )
        if gid_out.returncode == 0 and gid_out.stdout.strip().isdigit():
            gid = gid_out.stdout.strip()
    except Exception as exc:
        logger.warning("Could not resolve sandbox UID/GID from %s: %s", access_name, exc)

    # 2. Create /home/sandbox in the env container, owned by sandbox. Kept only
    #    for the sshd login user's authorized_keys; the interactive shell (root,
    #    HOME=/root) does not use it.
    try:
        subprocess.run(
            ["docker", "exec", "-u", "0", env_name, "bash", "-c",
             f"mkdir -p /home/sandbox && chown {uid}:{gid} /home/sandbox && chmod 755 /home/sandbox"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:
        logger.warning("Failed to create /home/sandbox in %s: %s", env_name, exc)

    # 2b. Ensure the project dir exists. enter-env.sh lands the shell in
    #     /workspace (the image's WORKDIR); derived images that use / or /output
    #     are covered by the parallel fallback.
    try:
        subprocess.run(
            ["docker", "exec", "-u", "0", env_name, "bash", "-c",
             "mkdir -p /workspace 2>/dev/null || mkdir -p /output"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:
        logger.warning("Failed to ensure project dir in %s: %s", env_name, exc)

    # 2c. Ensure a root login profile so `bash -l` with HOME=/root sources
    #     ~/.bashrc (conda/virtualenv/PATH from the base image), mirroring what
    #     generate_env_dockerfile bakes for direct builds. Non-destructive:
    #     existing profiles are left untouched.
    try:
        subprocess.run(
            ["docker", "exec", "-u", "0", env_name, "bash", "-c",
             "mkdir -p /root && "
             "[ -f /root/.bash_profile ] || printf '%s\\n' "
             "'[ -f \"$HOME/.bashrc\" ] && . \"$HOME/.bashrc\"' > /root/.bash_profile; "
             "[ -f /root/.bashrc ] || printf '%s\\n' "
             "'# Generated by worker interactive prep' > /root/.bashrc"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:
        logger.warning("Failed to set up /root login profile in %s: %s", env_name, exc)

    # 3. Best-effort tool install (curl, tar, gzip) — skip on non-Debian images.
    try:
        subprocess.run(
            ["docker", "exec", "-u", "0", env_name, "bash", "-c",
             "command -v curl >/dev/null 2>&1 && command -v tar >/dev/null 2>&1 && command -v gzip >/dev/null 2>&1 && exit 0 || true"],
            capture_output=True, text=True, timeout=10,
        )
        check = subprocess.run(
            ["docker", "exec", "-u", "0", env_name, "bash", "-c",
             "command -v curl >/dev/null 2>&1 && command -v tar >/dev/null 2>&1 && command -v gzip >/dev/null 2>&1"],
            capture_output=True, text=True, timeout=10,
        )
        if check.returncode != 0:
            subprocess.run(
                ["docker", "exec", "-u", "0", env_name, "bash", "-c",
                 "if command -v apt-get >/dev/null 2>&1; then "
                 "apt-get update -qq && "
                 "apt-get install -y --no-install-recommends curl ca-certificates tar gzip && "
                 "rm -rf /var/lib/apt/lists/*; fi"],
                capture_output=True, text=True, timeout=300,
            )
    except Exception as exc:
        logger.warning("Best-effort tool install in %s skipped: %s", env_name, exc)

    # 4. Diagnostics.
    try:
        df_out = subprocess.run(
            ["docker", "exec", env_name, "df", "-h", "/"],
            capture_output=True, text=True, timeout=10,
        )
        logger.info("Env %s df -h /: %s", env_name, df_out.stdout.strip())
        ls_out = subprocess.run(
            ["docker", "exec", env_name, "ls", "-ld", "/home", "/home/sandbox"],
            capture_output=True, text=True, timeout=10,
        )
        logger.info("Env %s ls -ld /home /home/sandbox: %s", env_name, ls_out.stdout.strip())
    except Exception as exc:
        logger.warning("Diagnostics for %s failed: %s", env_name, exc)


def _wait_for_tailscale_ip(session_id: str) -> str | None:
    import subprocess

    access_name = _container_name(session_id, "access")
    deadline = time.time() + IP_DETECT_TIMEOUT
    while time.time() < deadline:
        logs = subprocess.run(
            ["docker", "logs", access_name],
            capture_output=True, text=True,
        )
        raw_output = logs.stdout + logs.stderr
        logger.debug(
            "Interactive %s: polling access logs (raw output: %r)",
            session_id, raw_output,
        )
        match = IP_LOG_RE.search(raw_output)
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


def _kill_terminal(session_id: str) -> bool:
    """Terminate active SSH sessions for a session, disconnecting any connected
    users ('kill the terminal'). Kills sshd session processes in the access
    container and the shells they spawned (via nsenter) inside the env
    container. The bracket pattern avoids pkill matching its own cmdline."""
    import subprocess

    ok = True
    for name, pattern in (
        (_container_name(session_id, "access"), "[s]shd: sandbox"),
        (_container_name(session_id, "env"), "[b]ash -l"),
    ):
        try:
            result = subprocess.run(
                ["docker", "exec", name, "sh", "-c",
                 f'pkill -TERM -f "{pattern}" 2>/dev/null; true'],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                ok = False
        except Exception as e:
            logger.warning("Failed to kill terminal processes in %s: %s", name, e)
            ok = False
    return ok


def _monitor_container(api, session_id: str):
    """Report 'stopped' once either container exits or a session timeout
    (hard cap / no-connect / idle) is reached. On timeout the terminal is
    killed first (active SSH sessions terminated), then both containers are
    stopped and the scheduler is told the job is INTERACTIVE_STOPPED."""
    import subprocess

    with _lock:
        info = _active_containers.get(session_id)
    if not info:
        return

    env_name = info["env_name"]
    access_name = info["access_name"]

    started_at = time.time()
    ever_connected = False
    last_activity_at = None
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
            last_activity_at = time.time()
        elapsed = time.time() - started_at
        if SESSION_TIMEOUT > 0 and elapsed >= SESSION_TIMEOUT:
            stop_reason = "session_timeout"
            break
        if NO_CONNECT_TIMEOUT > 0 and not ever_connected and elapsed >= NO_CONNECT_TIMEOUT:
            stop_reason = "no_connect_timeout"
            break
        if (
            IDLE_TIMEOUT > 0
            and ever_connected
            and last_activity_at is not None
            and (time.time() - last_activity_at) >= IDLE_TIMEOUT
        ):
            stop_reason = "idle_timeout"
            break
        time.sleep(5)

    with _lock:
        info = _active_containers.pop(session_id, None) or {}
    logger.info("Interactive containers for session %s stopped (%s); reporting stopped.",
                session_id, stop_reason)

    timeout_reasons = ("session_timeout", "no_connect_timeout", "idle_timeout")
    if stop_reason in timeout_reasons:
        _kill_terminal(session_id)
    _stop_container(session_id, env_image_tag=info.get("env_image_tag"))
    status = "INTERACTIVE_STOPPED" if stop_reason in timeout_reasons else "STOPPED"
    try:
        api.report_interactive_ip(session_id, None, status)
    except Exception as e:
        logger.error("Failed to report stopped state for %s: %s", session_id, e)


def _stop_container(session_id: str, env_image_tag: str | None = None) -> bool:
    """Stop and force-remove both the env and access containers for a session.

    Falls back to ``docker rm -f`` when ``docker stop`` fails (e.g. the
    container is paused, unresponsive, or already exited but not auto-removed).
    After container cleanup, best-effort removes the derived env image.
    """
    import subprocess

    env_name = _container_name(session_id, "env")
    access_name = _container_name(session_id, "access")

    # Read the image tag from tracked state if the caller didn't supply one.
    if env_image_tag is None:
        with _lock:
            info = _active_containers.get(session_id) or {}
            env_image_tag = info.get("env_image_tag")

    success = True
    for name in (env_name, access_name):
        result = subprocess.run(
            ["docker", "stop", "-t", "10", name],
            capture_output=True, text=True,
        )
        stderr = (result.stderr or "").strip()
        if result.returncode == 0:
            continue
        # Container already gone (removed by --rm or never created) — fine.
        if "No such container" in stderr:
            continue
        logger.warning("docker stop failed for %s: %s", name, stderr)
        # Force-remove as a fallback (covers paused / OOM-killed / dead
        # containers that ignore SIGTERM).
        rm_result = subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True, text=True,
        )
        rm_stderr = (rm_result.stderr or "").strip()
        if rm_result.returncode == 0 or "No such container" in rm_stderr:
            logger.info("Force-removed container %s", name)
        else:
            logger.error("Failed to force-remove container %s: %s", name, rm_stderr)
            success = False

    with _lock:
        _active_containers.pop(session_id, None)

    # Best-effort: remove the derived env image so disk doesn't fill up.
    if env_image_tag:
        remove_image(env_image_tag)

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


def commit_and_push_container(session_id: str, image_tag: str, command: str) -> tuple[bool, str]:
    """Commit the env container's current state to a new image and push it to
    Docker Hub. Returns (success, error_message).

    The env image's CMD is ``sleep infinity``, so the training command is baked
    into the committed image via ``docker commit --change`` — otherwise the
    batch pipeline's fresh training run would just sleep forever.
    """
    import subprocess

    env_name = _container_name(session_id, "env")

    # Bake the training command into the image's CMD so the batch pipeline
    # executes the right command instead of `sleep infinity`.
    cmd_change = f"CMD {json.dumps(['sh', '-c', command])}"

    logger.info("Committing %s -> %s (CMD: %s)", env_name, image_tag, command)
    record_event("info", f"Committing interactive session {session_id} to {image_tag}")

    commit = subprocess.run(
        ["docker", "commit", "--change", cmd_change, env_name, image_tag],
        capture_output=True, text=True,
    )
    if commit.returncode != 0:
        reason = commit.stderr.strip() or f"docker commit failed for {env_name}"
        logger.error("Commit %s -> %s failed: %s", env_name, image_tag, reason)
        return False, reason

    logger.info("Pushing committed image %s to Docker Hub ...", image_tag)
    push = subprocess.run(
        ["docker", "push", image_tag], capture_output=True, text=True,
    )
    if push.returncode != 0:
        reason = push.stderr.strip() or f"docker push failed for {image_tag}"
        logger.error("Push %s failed: %s", image_tag, reason)
        return False, reason

    logger.info("Committed %s -> %s and pushed to Docker Hub.", env_name, image_tag)
    record_event("info", f"Committed and pushed {image_tag} to Docker Hub")
    # The image is now on Docker Hub; drop the local copy to free disk.
    remove_image(image_tag)
    return True, ""
