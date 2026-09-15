import requests
from urllib.parse import quote
from config import (
    SCHEDULER_QUEUE_URL, SCHEDULER_UPDATE_URL, SCHEDULER_LOG_URL,
    SCHEDULER_FAILURE_URL, SCHEDULER_CLAIM_URL, SCHEDULER_RELEASE_URL,
    OBJECT_STORE_URL, OBJECT_STORE_BUCKET, logger
)

def fetch_unbuilt_jobs() -> list[dict]:
    response = requests.get(SCHEDULER_QUEUE_URL, timeout=10)
    response.raise_for_status()
    return response.json().get("jobs", [])

def download_job_archive(object_key: str) -> bytes:
    download_url = f"{OBJECT_STORE_URL}/objects/{OBJECT_STORE_BUCKET}/{quote(object_key, safe='/')}"
    response = requests.get(download_url, timeout=30)
    response.raise_for_status()
    return response.content

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


def claim_job_for_building() -> dict | None:
    """Atomically claim the oldest NOT_RUNNABLE job for image building.

    POSTs to the scheduler's ``/jobs/claim_for_building`` endpoint. Returns the
    job dict on success, or ``None`` when no unbuilt jobs are available or any
    error occurs.
    """
    try:
        response = requests.post(SCHEDULER_CLAIM_URL, timeout=10)
        if response.status_code != 200:
            logger.error(
                "Claim request failed: %s %s", response.status_code, response.text
            )
            return None
        body = response.json()
        if "message" in body and body["message"] == "No unbuilt jobs available":
            return None
        if "error" in body:
            logger.error("Scheduler returned error on claim: %s", body["error"])
            return None
        return body
    except Exception as e:
        logger.error("Failed to claim job for building: %s", e)
        return None


def release_job_to_not_runnable(job_id: str) -> bool:
    """Release a job from IMAGE_BUILDING back to NOT_RUNNABLE.

    POSTs ``{"job_id": job_id}`` to the scheduler's
    ``/jobs/release_to_not_runnable`` endpoint. Returns ``True`` on success,
    ``False`` otherwise.
    """
    try:
        response = requests.post(
            SCHEDULER_RELEASE_URL, json={"job_id": job_id}, timeout=10
        )
        if response.status_code != 200:
            logger.error(
                "Release request failed for job %s: %s %s",
                job_id, response.status_code, response.text,
            )
            return False
        body = response.json()
        if "error" in body:
            logger.error("Scheduler returned error on release for job %s: %s", job_id, body["error"])
            return False
        return True
    except Exception as e:
        logger.error("Failed to release job %s to NOT_RUNNABLE: %s", job_id, e)
        return False