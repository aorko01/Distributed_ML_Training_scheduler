"""Helpers for identifying and removing Docker images after jobs/sessions end.

Only *derived* images (those built by the scheduler for a specific job) are
removed.  Base/shared images such as pytorch, python, and the access-sshd
image are always left intact.
"""
import logging
import subprocess

import config

logger = logging.getLogger("image_cleanup")

# Repos that must never be removed by job/session cleanup.
# Built from the configured access image so it stays in sync with config.
_PROTECTED_REPOS: set[str] = set()

def _repo_of(image_tag: str) -> str:
    """Strip the tag/digest suffix from an image reference to get the repo."""
    # e.g. "aorko123/access-sshd:latest" -> "aorko123/access-sshd"
    #      "python:3.11" -> "python"
    #      "pytorch/pytorch:2.1.0" -> "pytorch/pytorch"
    for sep in ("@", ":"):
        if sep in image_tag:
            return image_tag.rsplit(sep, 1)[0]
    return image_tag


def _ensure_protected():
    if not _PROTECTED_REPOS:
        _PROTECTED_REPOS.add(_repo_of(config.INTERACTIVE_ACCESS_IMAGE))


def is_derived_image(image_tag: str) -> bool:
    """Return True if *image_tag* is a job-specific image that may be safely removed.

    Returns False for:
    - empty or None tags
    - protected/shared repos (e.g. access-sshd)
    - any repo not under the configured DOCKER_HUB_USERNAME (official base
      images like pytorch, python, alpine, etc.)
    """
    if not image_tag:
        return False
    _ensure_protected()
    repo = _repo_of(image_tag)
    if repo in _PROTECTED_REPOS:
        return False
    # Only touch images under our own namespace (e.g. aorko123/<job_id>:latest).
    prefix = f"{config.DOCKER_HUB_USERNAME}/"
    return repo.startswith(prefix)


def remove_image(image_tag: str) -> bool:
    """Best-effort removal of *image_tag*. Returns True on success.

    - Silently returns False (no-op) if the tag is not a derived image.
    - Treats ``No such image`` as success (already gone).
    - Never raises — all errors are logged and swallowed.
    """
    if not is_derived_image(image_tag):
        return False
    try:
        result = subprocess.run(
            ["docker", "rmi", "-f", image_tag],
            capture_output=True, text=True, timeout=120,
        )
        stderr = (result.stderr or "").strip()
        if result.returncode == 0:
            logger.info("Removed Docker image %s", image_tag)
            return True
        if "No such image" in stderr or "image is referenced in multiple repositories" in stderr:
            logger.debug("Image %s already gone or ambiguous: %s", image_tag, stderr)
            return True
        logger.warning("Failed to remove image %s: %s", image_tag, stderr)
        return False
    except Exception as exc:
        logger.warning("Exception removing image %s: %s", image_tag, exc)
        return False
