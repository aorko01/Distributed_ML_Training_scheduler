import os
import shutil
import tempfile
import docker

from config import (
    DOCKER_HUB_USERNAME, DOCKER_HUB_PASSWORD, 
    DEBUG_SAVE_LOCAL, DEBUG_LOCAL_DIR, logger
)
from database import update_base_image_usage, get_old_base_images, remove_base_image_record

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
        try:
            _, build_logs = client.images.build(path=build_dir, tag=image_tag, rm=True, forcerm=True)
            for chunk in build_logs:
                if "stream" in chunk and chunk["stream"].strip():
                    logger.info("  [build] %s", chunk["stream"].strip())
        except docker.errors.BuildError as e:
            logger.error("Build failed for job %s: %s", job_id, e)
            return False

        # Push image
        logger.info("Pushing image %s ...", image_tag)
        push_output = client.images.push(repository=f"{DOCKER_HUB_USERNAME}/{job_id}", tag="latest", stream=True, decode=True)
        for chunk in push_output:
            if "status" in chunk:
                logger.info("  [push] %s %s", chunk["status"], chunk.get("progress", ""))
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