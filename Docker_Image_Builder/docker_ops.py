import os
import re
import shutil
import tempfile
import time
import threading
import docker
import requests

from config import (
    DOCKER_HUB_USERNAME, DOCKER_HUB_PASSWORD,
    DEBUG_SAVE_LOCAL, DEBUG_LOCAL_DIR, logger,
    OBJECT_STORE_URL, OBJECT_OUTPUT_BUCKET, OBJECT_STORE_BUCKET,
)
from database import update_base_image_usage, get_old_base_images, remove_base_image_record
from api import send_log_lines

_AUTH_ERROR_PATTERNS = ("unauthorized", "authentication required", "no basic auth credentials", "token is expired")

def _is_auth_error(error_msg: str) -> bool:
    lower = error_msg.lower()
    return any(pat in lower for pat in _AUTH_ERROR_PATTERNS)

# Transient errors raised by the classic Docker builder (and occasionally
# during push) that are caused by infrastructure / concurrency races rather
# than a defect in the user's project. When one of these surfaces as a
# BuildError we surface it as a "system" failure so the scheduler retries the
# job instead of marking it permanently FAILED.
_TRANSIENT_BUILD_ERROR_PATTERNS = (
    "failed to export image",
    "no such image",
    "connection reset",
    "connection refused",
    "tls handshake timeout",
    "broken pipe",
    "unexpected eof",
    "registry: unavailable",
    "no connection",
)

def _is_transient_build_error(error_msg: str) -> bool:
    lower = error_msg.lower()
    return any(pat in lower for pat in _TRANSIENT_BUILD_ERROR_PATTERNS)

_logged_in = False

# The classic Docker builder is not safe for concurrent builds that share a
# base image: builds reuse the same intermediate cache layers and one build's
# cleanup can delete a layer another build is still exporting, surfacing as
# `failed to export image: No such image: sha256:...`. To prevent this race we
# serialize builds per base image. Builds with *different* base images still
# run in parallel. Push/delete operations are not serialized.
_build_locks_guard = threading.Lock()
_build_locks: dict[str, threading.Lock] = {}


def _get_build_lock(base_image: str) -> threading.Lock:
    """Return a lock dedicated to ``base_image``, creating it on first use."""
    with _build_locks_guard:
        return _build_locks.setdefault(base_image, threading.Lock())

def docker_login(client: docker.DockerClient) -> bool:
    global _logged_in
    if not DOCKER_HUB_PASSWORD:
        logger.info("No Docker Hub password provided, assuming already logged in.")
        _logged_in = True
        return True
    try:
        logger.info("Logging in to Docker Hub as %s ...", DOCKER_HUB_USERNAME)
        client.login(username=DOCKER_HUB_USERNAME, password=DOCKER_HUB_PASSWORD)
        _logged_in = True
        return True
    except Exception as e:
        logger.error("Docker Hub login failed: %s", e)
        _logged_in = False
        return False

def ensure_logged_in(client: docker.DockerClient) -> bool:
    global _logged_in
    if _logged_in:
        return True
    return docker_login(client)

def generate_dockerfile(project_dir: str, command: str, base_image: str) -> str:
    if "\n" in base_image or "\r" in base_image or not base_image.strip():
        raise ValueError(f"Invalid base_image: {base_image!r}")
    if command and ("\n" in command or "\r" in command):
        raise ValueError("Invalid command: must be a single line")
    has_requirements = os.path.isfile(os.path.join(project_dir, "requirements.txt"))
    lines = [
        f"FROM {base_image}", "",
        "WORKDIR /workspace", "",
        "COPY . /workspace/", ""
    ]
    if has_requirements:
        lines += ["RUN pip install --no-cache-dir -r requirements.txt", ""]
    
    if command:
        lines += [f"CMD {command}"]
    else:
        lines += ['CMD ["python"]']
    
    return "\n".join(lines)


def save_debug_copy(job_id: str, build_dir: str) -> None:
    safe_job_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(job_id or "job"))[:64] or "job"
    debug_dir = os.path.join(DEBUG_LOCAL_DIR, safe_job_id)
    try:
        if os.path.exists(debug_dir):
            shutil.rmtree(debug_dir)
        os.makedirs(os.path.dirname(debug_dir) or ".", exist_ok=True)
        shutil.copytree(build_dir, debug_dir)
    except Exception as e:
        logger.error("Failed to save debug copy for job %s: %s", job_id, e)


def upload_build_logs(job_id: str, log_text: str) -> str | None:
    bucket_name = OBJECT_OUTPUT_BUCKET or OBJECT_STORE_BUCKET
    safe_job_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(job_id or "job"))[:64] or "job"
    object_key = f"{safe_job_id}/build.log"
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
        return None

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

    # Base-image pull/download progress is noise for the user; drop it from
    # both the persisted build.log and the realtime stream. (Callers that need
    # local diagnostics should logger.info before filtering.)
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

    # Only advance the throttle timestamp on success; otherwise the next
    # interval retries instead of assuming the logs were persisted.
    if upload_build_logs(job_id, log_text) is None:
        return last_upload_time
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
            # Preserve leading indentation (tracebacks) — only strip the
            # trailing newline chars and trailing whitespace.
            line = raw_line.strip("\r\n").rstrip()
            if line.strip():
                lines.append(line)

    deduped = []
    for line in lines:
        if not deduped or deduped[-1] != line:
            deduped.append(line)
    return deduped


def build_push_and_clean(client: docker.DockerClient, job_id: str, project_dir: str, command: str, base_image: str) -> tuple[str, str] | None:
    """Build, push and clean up the job image.

    Returns None on success, or a (failure_type, reason) tuple on failure where
    failure_type is "user" (build/code error -> job FAILED) or "system"
    (infra/daemon/registry error -> job RETRY_NEEDED).
    """
    image_tag = f"{DOCKER_HUB_USERNAME}/{job_id}:latest"
    safe_job_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(job_id or "job"))[:64] or "job"
    build_dir = tempfile.mkdtemp(prefix=f"build_{safe_job_id}_")

    try:
        for item in os.listdir(project_dir):
            src = os.path.join(project_dir, item)
            dst = os.path.join(build_dir, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

        dockerfile_content = generate_dockerfile(project_dir, command, base_image)
        with open(os.path.join(build_dir, "Dockerfile"), "w") as f:
            f.write(dockerfile_content)

        if DEBUG_SAVE_LOCAL:
            save_debug_copy(job_id, build_dir)

        logger.info("Building image %s ...", image_tag)
        build_log_buffer = []
        last_upload_time = None

        # Emit a diagnostic header so users have full context for debugging.
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

        # The classic Docker builder shares a single global build cache and is
        # unsafe for concurrent builds that reuse the same base image: one
        # build's intermediate cleanup can drop a cached layer that another
        # build still needs, raising `failed to export image: No such image`.
        # Serialize builds per base image to eliminate the race.
        build_lock = _get_build_lock(base_image)
        with build_lock:
            try:
                _, build_logs = client.images.build(path=build_dir, tag=image_tag, rm=True, forcerm=True)
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
            except docker.errors.BuildError as e:
                logger.error("Build failed for job %s: %s", job_id, e)
                error_lines = _extract_build_log_lines(e)
                full_msg = str(e) + "\n" + "\n".join(error_lines)
                if _is_transient_build_error(full_msg):
                    # Infrastructure / concurrency race: retry instead of
                    # permanently failing the user's job.
                    reason = f"Transient build failure (infra race): {e}"
                    emit_build_lines(job_id, build_log_buffer, [
                        "Build failed with BuildError:",
                        *error_lines,
                    ])
                    maybe_upload_build_logs(job_id, "\n".join(build_log_buffer), last_upload_time, force=True)
                    return "system", reason
                reason = f"Build failed: {e}"
                if not error_lines:
                    error_lines = [str(e).strip()]
                emit_build_lines(job_id, build_log_buffer, [
                    "Build failed with BuildError:",
                    *error_lines,
                ])
                maybe_upload_build_logs(job_id, "\n".join(build_log_buffer), last_upload_time, force=True)
                return "user", reason

        emit_build_lines(job_id, build_log_buffer, [
            "Docker build completed successfully.",
        ])
        maybe_upload_build_logs(job_id, "\n".join(build_log_buffer), last_upload_time, force=True)

        # Push image (capture push status so registry/network issues are debuggable).
        # Push progress/status is logged locally only; only build-related lines are
        # streamed to the scheduler UI.
        logger.info("Pushing image %s ...", image_tag)
        push_failed = False
        for attempt in range(2):
            push_output = client.images.push(repository=image_tag.rsplit(":", 1)[0], tag="latest", stream=True, decode=True)
            auth_error = False
            for chunk in push_output:
                if "error" in chunk:
                    error_msg = chunk["error"]
                    logger.error("Push error for job %s: %s", job_id, error_msg)
                    if _is_auth_error(error_msg) and attempt == 0:
                        logger.warning("Auth error during push, re-logging in and retrying ...")
                        auth_error = True
                        break
                    error_line = f"Push error: {error_msg}"
                    emit_build_lines(job_id, build_log_buffer, [error_line])
                    maybe_upload_build_logs(job_id, "\n".join(build_log_buffer), last_upload_time, force=True)
                    push_failed = True
                    break
                status = chunk.get("status", "").strip()
                progress = chunk.get("progress", "").strip()
                if status:
                    logger.info("  [push] %s%s", status, f" {progress}" if progress else "")
            if auth_error:
                global _logged_in
                _logged_in = False
                if not ensure_logged_in(client):
                    emit_build_lines(job_id, build_log_buffer, ["Push failed: could not re-login to Docker Hub"])
                    maybe_upload_build_logs(job_id, "\n".join(build_log_buffer), last_upload_time, force=True)
                    push_failed = True
                continue
            break
        if push_failed:
            return "system", error_line
        maybe_upload_build_logs(job_id, "\n".join(build_log_buffer), last_upload_time, force=True)
                
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

    logger.info("Deleting local built image %s ...", image_tag)
    try:
        client.images.remove(image=image_tag, force=True)
    except Exception as e:
        logger.warning("Failed to delete local image %s: %s", image_tag, e)

    # Only record base-image usage after a successful build+push. Recording
    # it up-front pollutes the LRU DB with failed/injected bases and prevents
    # pruning of images that were never actually usable.
    try:
        update_base_image_usage(base_image)
    except Exception as e:
        logger.warning("Failed to record base image usage for %s: %s", base_image, e)

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