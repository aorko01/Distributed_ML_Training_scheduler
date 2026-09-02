import os
import re
import shutil
import tempfile
import time
import docker
import requests

from config import (
    DOCKER_HUB_USERNAME, DOCKER_HUB_PASSWORD,
    DEBUG_SAVE_LOCAL, DEBUG_LOCAL_DIR, logger,
    OBJECT_STORE_URL, OBJECT_OUTPUT_BUCKET, OBJECT_STORE_BUCKET,
    DOCKER_BUILD_NO_CACHE,
    DOCKER_BUILD_ATTEMPTS, DOCKER_BUILD_CHUNK_SIZE, DOCKER_BUILD_RETRY_BACKOFF,
)
from database import update_base_image_usage, get_old_base_images, remove_base_image_record
from api import send_log_lines

def docker_login(client: docker.DockerClient):
    if DOCKER_HUB_PASSWORD:
        logger.info("Logging in to Docker Hub as %s ...", DOCKER_HUB_USERNAME)
        client.login(username=DOCKER_HUB_USERNAME, password=DOCKER_HUB_PASSWORD)
    else:
        logger.info("No Docker Hub password provided, assuming already logged in.")

def _split_requirements(build_dir: str, project_dir: str, chunk_size: int) -> list[str]:
    """Split a requirements.txt into chunk files for layered pip installs.

    Returns a list of filenames (relative to build_dir) to install in order.
    If the file doesn't exist, contains pip options/includes, or fits in one
    chunk, returns ["requirements.txt"] (single-layer fallback).
    """
    req_path = os.path.join(project_dir, "requirements.txt")
    if not os.path.exists(req_path):
        return []

    with open(req_path, "r") as f:
        lines = [line.strip() for line in f if line.strip()]

    if not lines:
        return []

    # If any line looks like a pip option, reference, or include, do not split
    for line in lines:
        if line.startswith("-") or "-r " in line or "-c " in line or line.startswith("--"):
            return ["requirements.txt"]

    if len(lines) <= chunk_size:
        return ["requirements.txt"]

    chunks = []
    for i in range(0, len(lines), chunk_size):
        chunk_lines = lines[i:i + chunk_size]
        chunk_name = f"requirements-part-{i // chunk_size + 1:03d}.txt"
        chunk_path = os.path.join(build_dir, chunk_name)
        with open(chunk_path, "w") as f:
            f.write("\n".join(chunk_lines) + "\n")
        chunks.append(chunk_name)

    return chunks


def _write_dockerignore(build_dir: str) -> None:
    """Write a conservative .dockerignore into the build context directory."""
    entries = [
        ".git", ".gitignore", "__pycache__", "*.pyc", "*.pyo",
        ".pytest_cache", ".mypy_cache", ".DS_Store", ".idea", ".vscode",
        ".venv", "venv", "*.egg-info",
    ]
    with open(os.path.join(build_dir, ".dockerignore"), "w") as f:
        f.write("\n".join(entries) + "\n")


def _is_containerd_export_error(text: str) -> bool:
    """Return True if the error text looks like a containerd layer-export failure."""
    lowered = text.lower()
    return (
        "failed to export layer" in lowered
        or "creatediff" in lowered
        or "mount callback failed" in lowered
        or (("lstat" in lowered) and ("no such file or directory" in lowered or "snapshot" in lowered))
    )

def generate_dockerfile(project_dir: str, command: str, base_image: str, requirement_files: list[str] | None = None) -> str:
    has_requirements = os.path.exists(os.path.join(project_dir, "requirements.txt"))
    lines = [
        f"FROM {base_image}", "",
        "ENV PYTHONDONTWRITEBYTECODE=1",
        "ENV PIP_BREAK_SYSTEM_PACKAGES=1",
        "RUN find / -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true",
        "",
        "WORKDIR /workspace", "",
    ]

    if requirement_files is not None and requirement_files:
        # Copy only requirement files first for better layer caching
        copy_files = " ".join(requirement_files)
        lines += [f"COPY {copy_files} /workspace/", ""]
        for rf in requirement_files:
            lines += [f"RUN pip install --no-cache-dir -r {rf}", ""]
        lines += ["COPY . /workspace/", ""]
    elif has_requirements:
        lines += ["COPY . /workspace/", "",
                  "RUN pip install --no-cache-dir -r requirements.txt", ""]
    else:
        lines += ["COPY . /workspace/", ""]

    if command:
        lines += [f"CMD {command}"]
    else:
        lines += ['CMD ["python"]']

    return "\n".join(lines)


def resolve_interactive_base_image(env_config: dict | None) -> str:
    """Resolve the base image for a direct interactive build.

    Resolution order (maximum user flexibility):
      1. Explicit "base_image" override in the job config -> used verbatim.
      2. pytorch_version set  -> official pytorch/pytorch image, optionally
         pinned to a CUDA release via cuda_version ("12.1", or a full variant
         tag suffix like "12.1-cudnn9-devel").
      3. Fallback             -> plain python:{python_version}-slim image.
    """
    env = env_config or {}

    explicit = env.get("base_image")
    if explicit:
        return explicit.strip()

    python_version = (env.get("python_version") or "3.11").strip()
    pytorch_version = (env.get("pytorch_version") or "").strip()
    cuda_version = (env.get("cuda_version") or "").strip()

    if pytorch_version:
        if cuda_version:
            # If the caller supplied a full variant suffix (e.g.
            # "12.1-cudnn9-devel"), use it verbatim; otherwise assume the
            # default cudnn9 runtime variant published by the pytorch images.
            suffix = "" if "-" in cuda_version else "-cudnn9-runtime"
            return f"pytorch/pytorch:{pytorch_version}-cuda{cuda_version}{suffix}"
        return f"pytorch/pytorch:{pytorch_version}"

    return f"python:{python_version}-slim"


def generate_env_dockerfile(base_image: str, with_project: bool = False, requirement_files: list[str] | None = None) -> str:
    """Generate a Dockerfile for a clean interactive environment image.

    This image contains NO sshd and NO Tailscale/Headscale — it is a pure
    training/environment container that stays alive via ``sleep infinity``.
    Access (SSH + tailnet) is provided by a separate, shared access image
    (``aorko123/access-sshd:latest``) that joins the env container's PID
    namespace via nsenter.

    Derived builds (with_project=False) are based on a training job's image
    that already contains the training code. Direct builds (with_project=True)
    start from a fresh base and copy the uploaded project into /workspace,
    installing requirements.txt if present.
    """
    lines = [
        f"FROM {base_image}", "",
        "USER root",
        "ENV PYTHONDONTWRITEBYTECODE=1",
        "ENV PIP_BREAK_SYSTEM_PACKAGES=1",
        "RUN find / -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true",
        "# Prepare home directory for VS Code Remote-SSH (sandbox user, UID 1000)",
        "RUN mkdir -p /home/sandbox && chown 1000:1000 /home/sandbox && chmod 755 /home/sandbox",
        "# Install tools needed by VS Code server installer (skip silently on non-Debian)",
        "RUN if command -v apt-get >/dev/null 2>&1; then apt-get update -qq && apt-get install -y --no-install-recommends curl ca-certificates tar gzip && rm -rf /var/lib/apt/lists/*; fi",
        "",
    ]

    if with_project:
        lines += ["WORKDIR /workspace", ""]
        if requirement_files is not None and requirement_files:
            copy_files = " ".join(requirement_files)
            lines += [
                f"COPY {copy_files} /workspace/",
                "",
            ]
            for rf in requirement_files:
                lines += [f"RUN pip install --no-cache-dir -r {rf}", ""]
            lines += ["COPY . /workspace/", ""]
        else:
            lines += [
                "COPY . /workspace/",
                "",
                "RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi",
                "",
            ]

    lines += [
        'RUN if [ ! -f /root/.bash_profile ]; then \\',
        "      printf '\\n# Source .bashrc for login shells (conda/virtualenv activation)\\n[ -f \"$HOME/.bashrc\" ] && . \"$HOME/.bashrc\"\\n' > /root/.bash_profile; \\",
        '    fi',
        '',
        'CMD ["sleep", "infinity"]',
    ]
    return "\n".join(lines)


def generate_access_dockerfile() -> str:
    """Generate a Dockerfile for the shared access/SSH image.

    This is a static, reusable image (``aorko123/access-sshd:latest``) that
    contains sshd + tailscaled. It is launched with ``--pid container:<env>``
    so its sshd ``ForceCommand`` can nsenter into the env container's
    namespaces, routing the user's shell into the training container.
    """
    return r"""# Shared access/SSH image for interactive sessions.
# Build & push ONCE (manually) to Docker Hub:
#   docker build -t aorko123/access-sshd:latest ./AccessContainer
#   docker push aorko123/access-sshd:latest
# The worker pulls aorko123/access-sshd:latest per session.
FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        openssh-server \
        curl \
        ca-certificates \
        bash \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

# nsenter must run as root to enter the env container's namespaces (needs
# CAP_SYS_ADMIN). The SSH login user is the unprivileged `sandbox`, so make
# nsenter setuid-root. The entrypoint re-asserts this at runtime to survive a
# worker daemon with userns-remap (which remaps image-layer file ownership
# away from root and would otherwise silently break this setuid bit).
RUN chmod u+s /usr/bin/nsenter

# Install Tailscale (for tailnet connectivity).
RUN curl -fsSL https://tailscale.com/install.sh | sh

# Create the sandbox user that sshd will authenticate.
RUN id -u sandbox >/dev/null 2>&1 || useradd --create-home --shell /bin/bash sandbox

RUN mkdir -p /run/sshd /etc/ssh/sshd_config.d /var/run/tailscale /var/lib/tailscale

COPY access-entrypoint.sh /usr/local/bin/access-entrypoint.sh
RUN chmod +x /usr/local/bin/access-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/access-entrypoint.sh"]
"""


def generate_access_entrypoint() -> str:
    """Generate the entrypoint shell script for the shared access image.

    Validates required runtime environment variables (HEADSCALE_URL,
    HEADSCALE_AUTHKEY, SESSION_ID, SSH_PUBLIC_KEY), starts tailscaled, waits
    for its control socket, connects to Headscale via ``tailscale up``,
    configures authorized_keys for the sandbox user, writes an sshd_config.d
    snippet with a ``ForceCommand`` that nsenters into the env container's
    PID 1 namespaces, and finally runs ``sshd -D`` as the foreground process.
    Prints ``Tailscale IP: 100.x.x.x`` so the worker can detect the tailnet IP.
    """
    return r"""#!/bin/bash
set -e

: "${HEADSCALE_URL:?HEADSCALE_URL is required}"
: "${HEADSCALE_AUTHKEY:?HEADSCALE_AUTHKEY is required}"
: "${SESSION_ID:?SESSION_ID is required}"
: "${SSH_PUBLIC_KEY:?SSH_PUBLIC_KEY is required}"

# nsenter must run as root to enter the env container's namespaces
# (CAP_SYS_ADMIN). The SSH login user is the unprivileged `sandbox`, so make
# nsenter setuid-root. Re-assert ownership + setuid at runtime because a
# worker daemon with userns-remap enabled remaps image-layer file ownership
# away from root (which would silently break the setuid bit baked into the
# image). This runs as root (no USER directive in the Dockerfile).
chown root:root /usr/bin/nsenter
chmod u+s /usr/bin/nsenter

echo "Starting access container for session ${SESSION_ID}..."

# Start Tailscale daemon.
tailscaled \
    --state=/var/lib/tailscale/tailscaled.state \
    --socket=/var/run/tailscale/tailscaled.sock &

echo "Waiting for Tailscale daemon..."
until [ -S /var/run/tailscale/tailscaled.sock ]; do
    sleep 1
done

echo "Connecting to Headscale..."
tailscale up \
    --login-server="$HEADSCALE_URL" \
    --authkey="$HEADSCALE_AUTHKEY" \
    --hostname="$SESSION_ID"

echo "Tailscale connected."

# SSH setup: install the gateway's public key for the sandbox user.
mkdir -p /home/sandbox/.ssh
echo "$SSH_PUBLIC_KEY" > /home/sandbox/.ssh/authorized_keys
chown -R sandbox:sandbox /home/sandbox/.ssh
chmod 700 /home/sandbox/.ssh
chmod 600 /home/sandbox/.ssh/authorized_keys

# sshd config: force every connection to nsenter into the env container's
# PID 1 (the env container's `sleep infinity`), entering all its namespaces
# (mount, UTS, IPC, net, PID) so the user lands inside the training container.
# HOME=/root ensures ~/.bash_profile is sourced (conda/virtualenv activation).
# PATH includes /opt/conda/bin and /usr/local/bin as fallbacks for Python tools.
mkdir -p /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/sandbox.conf <<'SSHD'
PasswordAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
AllowUsers sandbox
AllowTcpForwarding yes
ForceCommand nsenter -t 1 -m -u -i -n -p -- /bin/bash -c 'export HOME=/root; export PATH=/opt/conda/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin; exec /bin/bash -l'
SSHD

echo "Tailscale IP: $(tailscale ip -4 || true)"

echo "Starting SSH server..."
exec /usr/sbin/sshd -D -e
"""


def ensure_access_image(client: docker.DockerClient) -> bool:
    """Ensure the shared access image (``aorko123/access-sshd:latest``) is available.

    The access image is built and pushed to Docker Hub ONCE by the operator
    (see ``AccessContainer/Dockerfile``). This function simply pulls it if it
    is not already present locally. Returns True if the image is available,
    False otherwise.
    """
    access_image = "aorko123/access-sshd:latest"
    try:
        client.images.get(access_image)
        logger.info("Access image %s already present locally.", access_image)
        return True
    except docker.errors.ImageNotFound:
        pass
    except Exception as e:
        logger.warning("Could not check for access image %s: %s", access_image, e)

    logger.info("Pulling access image %s ...", access_image)
    try:
        client.images.pull(access_image)
        logger.info("Successfully pulled access image %s.", access_image)
        return True
    except Exception as e:
        logger.error(
            "Failed to pull access image %s: %s. "
            "The operator must build and push it first: "
            "docker build -t aorko123/access-sshd:latest ./AccessContainer && "
            "docker push aorko123/access-sshd:latest",
            access_image, e,
        )
        return False

def save_debug_copy(job_id: str, build_dir: str) -> None:
    debug_dir = os.path.join(DEBUG_LOCAL_DIR, job_id)
    try:
        if os.path.exists(debug_dir):
            shutil.rmtree(debug_dir)
        os.makedirs(os.path.dirname(debug_dir) or ".", exist_ok=True)
        shutil.copytree(build_dir, debug_dir)
        os.chmod(debug_dir, 0o777)
        for root, dirs, files in os.walk(debug_dir):
            for d in dirs: os.chmod(os.path.join(root, d), 0o777)
            for f in files: os.chmod(os.path.join(root, f), 0o666)
    except Exception as e:
        logger.error("Failed to save debug copy for job %s: %s", job_id, e)


def upload_build_logs(job_id: str, log_text: str) -> str:
    bucket_name = OBJECT_OUTPUT_BUCKET or OBJECT_STORE_BUCKET
    object_key = f"{job_id}/build.log"
    payload = log_text.encode("utf-8")

    try:
        response = requests.post(
            f"{OBJECT_STORE_URL}/objects/upload",
            data={"bucket": bucket_name, "object_key": object_key},
            files={"file": ("build.log", payload, "text/plain")},
            timeout=30,
        )
        response.raise_for_status()
        logger.info("Uploaded build logs for job %s to %s/%s", job_id, bucket_name, object_key)
    except Exception as exc:
        logger.warning("Failed to upload build logs for job %s: %s", job_id, exc)

    return object_key


_STEP_PREFIX_RE = re.compile(r"^#\d+\s+")


def should_upload_build_line(line: str) -> bool:
    if not line:
        return False

    normalized = line.strip().lower()
    if not normalized:
        return False

    normalized = _STEP_PREFIX_RE.sub("", normalized)

    if normalized.startswith("waiting"):
        return False

    if normalized.startswith("push") or "pushing" in normalized:
        return False

    # Base-image pull/download progress is noise for the user; log it only.
    if (
        normalized.startswith("pulling")
        or normalized.startswith("downloading")
        or normalized.startswith("extracting")
        or normalized.startswith("digest:")
        or normalized.startswith("pull")
        or "downloaded newer image" in normalized
        or "pull complete" in normalized
        or "download complete" in normalized
        or "verifying checksum" in normalized
    ):
        return False

    return True


def maybe_upload_build_logs(job_id: str, log_text: str, last_upload_time: float | None, force: bool = False) -> float | None:
    if not log_text:
        return last_upload_time

    now = time.monotonic()
    if not force and last_upload_time is not None and (now - last_upload_time) < 60:
        return last_upload_time

    upload_build_logs(job_id, log_text)
    return now


def emit_build_lines(job_id: str, build_log_buffer: list[str], lines: list[str]) -> None:
    """Append build lines to the buffer and stream them to the scheduler.

    Keeps the in-memory buffer and the realtime UI stream in sync so both the
    object-store build.log and the scheduler log show the same ordered output.
    Lines containing embedded newlines (e.g. the Dockerfile) are split so each
    appears as its own log entry.
    """
    normalized = []
    for line in lines:
        normalized.extend(str(line).splitlines() or [""])
    if not normalized:
        return
    lines = normalized
    build_log_buffer.extend(lines)
    send_log_lines(job_id, lines)
    for line in lines:
        logger.info("  [build] %s", line)


def _extract_build_log_lines(error: docker.errors.BuildError) -> list[str]:
    """Extract the raw docker build output from a BuildError.

    The exception's message only carries a one-line summary; the full log
    (pip resolution errors, conflicting versions, stack traces, etc.) lives
    in the `build_log` attribute. Returns cleaned, de-duplicated lines.
    """
    lines = []
    for entry in getattr(error, "build_log", None) or []:
        if not isinstance(entry, dict):
            continue
        text = entry.get("stream") or entry.get("error") or entry.get("status")
        if not text:
            continue
        for raw_line in str(text).replace("\r", "\n").splitlines():
            line = raw_line.strip()
            if line:
                lines.append(line)

    deduped = []
    for line in lines:
        if not deduped or deduped[-1] != line:
            deduped.append(line)
    return deduped


def build_push_and_clean(client: docker.DockerClient, job_id: str, project_dir: str, command: str, base_image: str, build_type: str = "training", include_project: bool = False) -> tuple[str, str] | None:
    """Build, push and clean up the job image.

    For direct interactive builds pass include_project=True so the uploaded
    project files are copied into the sandbox image.

    Returns None on success, or a (failure_type, reason) tuple on failure where
    failure_type is "user" (build/code error -> job FAILED) or "system"
    (infra/daemon/registry error -> job RETRY_NEEDED).
    """
    if build_type == "interactive":
        image_tag = f"{DOCKER_HUB_USERNAME}/{job_id}-env:latest"
    else:
        image_tag = f"{DOCKER_HUB_USERNAME}/{job_id}:latest"
    build_dir = tempfile.mkdtemp(prefix=f"build_{job_id}_")
    _write_dockerignore(build_dir)

    # Track base image usage
    update_base_image_usage(base_image)

    try:
        if build_type == "interactive":
            # Derived interactive builds do not copy project files (the base
            # image already contains the training code). Direct interactive
            # builds (include_project=True) copy the uploaded archive and pip
            # install its requirements.txt before adding the env layers.
            # The env image is a clean training container (no SSH/Headscale);
            # access is provided by the shared aorko123/access-sshd image.
            if include_project:
                for item in os.listdir(project_dir):
                    src = os.path.join(project_dir, item)
                    dst = os.path.join(build_dir, item)
                    if os.path.isdir(src):
                        shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)

            requirement_files = _split_requirements(build_dir, project_dir, DOCKER_BUILD_CHUNK_SIZE)
            dockerfile_content = generate_env_dockerfile(base_image, with_project=include_project, requirement_files=requirement_files)
            with open(os.path.join(build_dir, "Dockerfile"), "w") as f:
                f.write(dockerfile_content)
        else:
            for item in os.listdir(project_dir):
                src = os.path.join(project_dir, item)
                dst = os.path.join(build_dir, item)
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)

            requirement_files = _split_requirements(build_dir, project_dir, DOCKER_BUILD_CHUNK_SIZE)
            dockerfile_content = generate_dockerfile(project_dir, command, base_image, requirement_files=requirement_files)
            with open(os.path.join(build_dir, "Dockerfile"), "w") as f:
                f.write(dockerfile_content)

        if DEBUG_SAVE_LOCAL:
            save_debug_copy(job_id, build_dir)

        logger.info("Building image %s ...", image_tag)
        build_log_buffer = []
        last_upload_time = None

        # Emit a diagnostic header so users have full context for debugging.
        if build_type == "interactive":
            emit_build_lines(job_id, build_log_buffer, [
                "=" * 60,
                f"Job {job_id}: building interactive env image",
                f"Target image : {image_tag}",
                f"Base image   : {base_image}",
                "--- generated Dockerfile ---",
                dockerfile_content,
                "--- end Dockerfile ---",
                "=" * 60,
            ])
        else:
            emit_build_lines(job_id, build_log_buffer, [
                "=" * 60,
                f"Job {job_id}: building Docker image",
                f"Target image : {image_tag}",
                f"Base image   : {base_image}",
                f"Command      : {command or '(default Docker CMD)'}",
                "--- generated Dockerfile ---",
                dockerfile_content,
                "--- end Dockerfile ---",
                "=" * 60,
            ])
        last_upload_time = maybe_upload_build_logs(job_id, "\n".join(build_log_buffer), last_upload_time, force=True)

        max_attempts = DOCKER_BUILD_ATTEMPTS
        build_error = None
        for attempt in range(1, max_attempts + 1):
            try:
                _, build_logs = client.images.build(path=build_dir, tag=image_tag, rm=True, forcerm=True, nocache=DOCKER_BUILD_NO_CACHE)
                for chunk in build_logs:
                    chunk_lines = []
                    if "error" in chunk:
                        chunk_lines.append(f"Docker error: {chunk['error']}")
                    if "stream" in chunk and chunk["stream"].strip():
                        for stream_line in chunk["stream"].splitlines():
                            stream_line = stream_line.strip()
                            if not stream_line or not should_upload_build_line(stream_line):
                                continue
                            chunk_lines.append(stream_line)
                    if "status" in chunk and chunk["status"].strip():
                        status_line = chunk["status"].strip()
                        if should_upload_build_line(status_line):
                            chunk_lines.append(status_line)
                    emit_build_lines(job_id, build_log_buffer, chunk_lines)
                    if chunk_lines:
                        last_upload_time = maybe_upload_build_logs(job_id, "\n".join(build_log_buffer), last_upload_time)
                break  # Build succeeded
            except (docker.errors.BuildError, docker.errors.APIError) as e:
                raw = str(e) + "\n" + "\n".join(_extract_build_log_lines(e))
                if attempt < max_attempts and _is_containerd_export_error(raw):
                    emit_build_lines(job_id, build_log_buffer, [
                        f"Containerd layer export error (attempt {attempt}/{max_attempts}): retrying ..."
                    ] + _extract_build_log_lines(e)[-3:])
                    last_upload_time = maybe_upload_build_logs(job_id, "\n".join(build_log_buffer), last_upload_time, force=True)
                    try:
                        client.images.prune(filters={"dangling": True})
                    except Exception:
                        pass
                    time.sleep(DOCKER_BUILD_RETRY_BACKOFF)
                    continue
                build_error = e
                break

        if build_error is not None:
            logger.error("Build failed for job %s: %s", job_id, build_error)
            reason = f"Build failed: {build_error}"
            error_lines = _extract_build_log_lines(build_error)
            if not error_lines:
                error_lines = [str(build_error).strip()]
            emit_build_lines(job_id, build_log_buffer, [
                "Build failed with BuildError:",
                *error_lines,
            ])
            maybe_upload_build_logs(job_id, "\n".join(build_log_buffer), last_upload_time, force=True)
            if build_type == "interactive":
                joined = "\n".join(error_lines).lower()
                if "pip" in joined or "requirements" in joined:
                    return "user", reason
                return "system", reason
            return "user", reason

        emit_build_lines(job_id, build_log_buffer, [
            "Docker build completed successfully.",
        ])
        maybe_upload_build_logs(job_id, "\n".join(build_log_buffer), last_upload_time, force=True)

        # Push image (capture push status so registry/network issues are debuggable).
        # Push progress/status is logged locally only; only build-related lines are
        # streamed to the scheduler UI.
        logger.info("Pushing image %s ...", image_tag)
        push_output = client.images.push(repository=image_tag.rsplit(":", 1)[0], tag="latest", stream=True, decode=True)
        for chunk in push_output:
            if "error" in chunk:
                logger.error("Push error for job %s: %s", job_id, chunk["error"])
                error_line = f"Push error: {chunk['error']}"
                emit_build_lines(job_id, build_log_buffer, [error_line])
                maybe_upload_build_logs(job_id, "\n".join(build_log_buffer), last_upload_time, force=True)
                return "system", error_line
            status = chunk.get("status", "").strip()
            progress = chunk.get("progress", "").strip()
            if status:
                logger.info("  [push] %s%s", status, f" {progress}" if progress else "")
        maybe_upload_build_logs(job_id, "\n".join(build_log_buffer), last_upload_time, force=True)

        # For interactive builds, ensure the shared access image is available
        # on this builder node so it can be pulled by the worker later.
        if build_type == "interactive":
            if not ensure_access_image(client):
                return "system", "Failed to pull access image aorko123/access-sshd:latest"

    except docker.errors.ImageNotFound as e:
        logger.error("Base image not found for job %s: %s", job_id, e)
        return "system", f"Base image unavailable: {e}"
    except docker.errors.APIError as e:
        logger.error("Docker API error for job %s: %s", job_id, e)
        return "system", f"Docker API error: {e}"
    except Exception as e:
        logger.error("Unexpected error for job %s: %s", job_id, e)
        return "system", f"Unexpected error: {e}"
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)

    # Clean up the local built image now that it's successfully pushed
    logger.info("Deleting local built image %s ...", image_tag)
    try:
        client.images.remove(image=image_tag, force=True)
    except Exception as e:
        logger.warning("Failed to delete local image %s: %s", image_tag, e)

    logger.info("Image %s pushed to Docker Hub and local copy cleaned up.", image_tag)
    maybe_upload_build_logs(job_id, "\n".join(build_log_buffer), last_upload_time, force=True)

    return None

def prune_old_base_images(client: docker.DockerClient):
    old_images = get_old_base_images(days=7)
    for image_name in old_images:
        logger.info("Attempting to prune old base image: %s", image_name)
        try:
            client.images.remove(image=image_name, force=True)
            logger.info("Successfully deleted old base image: %s", image_name)
            remove_base_image_record(image_name)
        except docker.errors.ImageNotFound:
            remove_base_image_record(image_name)
        except Exception as e:
            logger.warning("Could not delete base image %s (might still be in use): %s", image_name, e)