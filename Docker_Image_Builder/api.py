import os
import tempfile
import requests
from urllib.parse import quote
from config import (
    SCHEDULER_QUEUE_URL, SCHEDULER_UPDATE_URL, SCHEDULER_INTERACTIVE_UPDATE_URL, SCHEDULER_LOG_URL,
    SCHEDULER_FAILURE_URL, SCHEDULER_PENDING_URL, OBJECT_STORE_URL, OBJECT_STORE_BUCKET, logger
)


def fetch_unbuilt_jobs() -> list[dict]:
    response = requests.get(SCHEDULER_QUEUE_URL, timeout=10)
    response.raise_for_status()
    return response.json().get("jobs", [])


def download_job_archive(object_key: str) -> str:
    """Download job archive to a temp file and return the file path."""
    download_url = f"{OBJECT_STORE_URL}/objects/{OBJECT_STORE_BUCKET}/{quote(object_key, safe='/')}"
    response = requests.get(download_url, stream=True, timeout=60)
    response.raise_for_status()

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    try:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                tmp.write(chunk)
    finally:
        tmp.close()

    logger.info("Downloaded archive for object_key=%s to %s (%d bytes)",
                object_key, tmp.name, os.path.getsize(tmp.name))
    return tmp.name


def send_log_lines(job_id: str, lines: list[str]) -> None:
    """Stream build log lines to the scheduler for realtime UI display."""
    if not lines:
        return
    try:
        response = requests.post(
            f"{SCHEDULER_LOG_URL}/{job_id}",
            json={"lines": lines},
            timeout=5,
        )
        response.raise_for_status()
    except Exception as e:
        logger.debug("Failed to stream logs for job %s: %s", job_id, e)


def notify_scheduler_job_pending(job_id: str) -> bool:
    """Notify the scheduler that the builder has started working on a job.

    Returns True only when this builder now owns the job (scheduler claimed
    the atomic NOT_RUNNABLE -> PENDING transition for us). Returns False when
    the job was already claimed by someone else, not found, or unreachable —
    the caller must back off and NOT build.

    Distinguishable already-claimed handling: the scheduler answers the loser
    of the atomic-claim race with 409 (or a body carrying ``claimed: False`` /
    ``already_claimed: True`` / ``error``). Any of those mean "someone else
    owns it" — never treat them as success, otherwise two builders/replicas
    polling the same queue would both ``docker build`` the same tag.
    """
    payload = {"job_id": job_id}
    try:
        response = requests.post(SCHEDULER_PENDING_URL, json=payload, timeout=10)
        if response.status_code in (409, 423):
            # Already claimed by another builder/replica — back off.
            logger.warning(
                "Job %s already claimed by another builder (scheduler %s), backing off.",
                job_id, response.status_code,
            )
            return False
        if response.status_code == 200:
            try:
                body = response.json()
            except Exception:
                logger.error("Scheduler pending response for job %s was not JSON: %s", job_id, response.text)
                return False
            if not isinstance(body, dict):
                logger.error("Scheduler pending response for job %s was unexpected: %r", job_id, body)
                return False
            if "error" in body:
                logger.warning("Scheduler did not grant pending for job %s (already claimed?): %s",
                               job_id, body["error"])
                return False
            # Explicit claim flags (new scheduler protocol). Only the claim
            # winner gets claimed=True; the loser must back off even on 200.
            if body.get("already_claimed") is True or body.get("claimed") is False:
                logger.warning("Job %s already claimed by another builder, backing off.", job_id)
                return False
            logger.info("Scheduler notified of pending status for job %s", job_id)
            return True
        logger.error("Scheduler pending notification failed for job %s: %s %s", job_id, response.status_code, response.text)
    except Exception as e:
        logger.error("Failed to contact scheduler for pending job %s: %s", job_id, e)
    return False


def notify_scheduler_job_ready(job_id: str) -> bool:
    payload = {"job_id": job_id}
    try:
        response = requests.post(SCHEDULER_UPDATE_URL, json=payload, timeout=10)
        if response.status_code == 200:
            body = response.json()
            if "error" in body:
                logger.error("Scheduler returned error for job %s: %s", job_id, body["error"])
                return False
            logger.info("Scheduler notified successfully for job %s", job_id)
            return True
        logger.error("Scheduler notification failed for job %s: %s %s", job_id, response.status_code, response.text)
    except Exception as e:
        logger.error("Failed to contact scheduler for job %s: %s", job_id, e)
    return False


def notify_scheduler_interactive_ready(job_id: str) -> bool:
    """Notify the scheduler that an interactive job image has been built and pushed."""
    payload = {"job_id": job_id}
    try:
        response = requests.post(SCHEDULER_INTERACTIVE_UPDATE_URL, json=payload, timeout=10)
        if response.status_code == 200:
            body = response.json()
            if "error" in body:
                logger.error("Scheduler returned error for interactive job %s: %s", job_id, body["error"])
                return False
            logger.info("Scheduler notified successfully for interactive job %s", job_id)
            return True
        logger.error("Scheduler notification failed for interactive job %s: %s %s", job_id, response.status_code, response.text)
    except Exception as e:
        logger.error("Failed to contact scheduler for interactive job %s: %s", job_id, e)
    return False


def notify_scheduler_job_failed(job_id: str, failure_type: str, failure_reason: str) -> bool:
    """Report a job failure to the scheduler.

    failure_type: "user" (build/code error -> FAILED) or "system" (infra -> RETRY_NEEDED).
    """
    payload = {
        "job_id": job_id,
        "failure_type": failure_type,
        "failure_reason": failure_reason[:2000],
    }
    try:
        response = requests.post(SCHEDULER_FAILURE_URL, json=payload, timeout=10)
        if response.status_code == 200:
            body = response.json()
            if "error" in body:
                logger.error("Scheduler rejected failure report for job %s: %s", job_id, body["error"])
                return False
            logger.info("Scheduler notified of %s failure for job %s", failure_type, job_id)
            return True
        logger.error("Scheduler failure notification failed for job %s: %s %s", job_id, response.status_code, response.text)
    except Exception as e:
        logger.error("Failed to report failure for job %s: %s", job_id, e)
    return False
