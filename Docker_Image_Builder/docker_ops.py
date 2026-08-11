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
)
from database import update_base_image_usage, get_old_base_images, remove_base_image_record
from api import send_log_lines

def docker_login(client: docker.DockerClient):
    if DOCKER_HUB_PASSWORD:
        logger.info("Logging in to Docker Hub as %s ...", DOCKER_HUB_USERNAME)
        client.login(username=DOCKER_HUB_USERNAME, password=DOCKER_HUB_PASSWORD)
    else:
        logger.info("No Docker Hub password provided, assuming already logged in.")

def generate_dockerfile(project_dir: str, command: str, base_image: str) -> str:
    has_requirements = os.path.exists(os.path.join(project_dir, "requirements.txt"))
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

    return True


def maybe_upload_build_logs(job_id: str, log_text: str, last_upload_time: float | None, force: bool = False) -> float | None:
    if not log_text:
        return last_upload_time

    now = time.monotonic()
    if not force and last_upload_time is not None and (now - last_upload_time) < 60:
        return last_upload_time

    upload_build_logs(job_id, log_text)
    return now


def build_push_and_clean(client: docker.DockerClient, job_id: str, project_dir: str, command: str, base_image: str) -> bool:
    image_tag = f"{DOCKER_HUB_USERNAME}/{job_id}:latest"
    build_dir = tempfile.mkdtemp(prefix=f"build_{job_id}_")
    
    # Track base image usage
    update_base_image_usage(base_image)

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
        try:
            _, build_logs = client.images.build(path=build_dir, tag=image_tag, rm=True, forcerm=True)
            for chunk in build_logs:
                if "stream" not in chunk or not chunk["stream"].strip():
                    continue
                chunk_lines = []
                for stream_line in chunk["stream"].splitlines():
                    stream_line = stream_line.strip()
                    if not stream_line or not should_upload_build_line(stream_line):
                        continue

                    build_log_buffer.append(stream_line)
                    chunk_lines.append(stream_line)
                    logger.info("  [build] %s", stream_line)
                    last_upload_time = maybe_upload_build_logs(job_id, "\n".join(build_log_buffer), last_upload_time)
                if chunk_lines:
                    send_log_lines(job_id, chunk_lines)
        except docker.errors.BuildError as e:
            logger.error("Build failed for job %s: %s", job_id, e)
            if str(e).strip():
                build_log_buffer.append(str(e).strip())
                send_log_lines(job_id, [str(e).strip()])
            maybe_upload_build_logs(job_id, "\n".join(build_log_buffer), last_upload_time, force=True)
            return False

        if build_log_buffer:
            maybe_upload_build_logs(job_id, "\n".join(build_log_buffer), last_upload_time, force=True)

        # Push image
        logger.info("Pushing image %s ...", image_tag)
        push_output = client.images.push(repository=f"{DOCKER_HUB_USERNAME}/{job_id}", tag="latest", stream=True, decode=True)
        for chunk in push_output:
            if "status" in chunk:
                status_line = f"{chunk['status']} {chunk.get('progress', '')}".strip()
                logger.info("  [push] %s", status_line)
            if "error" in chunk:
                logger.error("Push error for job %s: %s", job_id, chunk["error"])
                return False
                
    except Exception as e:
        logger.error("Unexpected error for job %s: %s", job_id, e)
        return False
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)

    # Clean up the local built image now that it's successfully pushed
    logger.info("Deleting local built image %s ...", image_tag)
    try:
        client.images.remove(image=image_tag, force=True)
    except Exception as e:
        logger.warning("Failed to delete local image %s: %s", image_tag, e)

    return True

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