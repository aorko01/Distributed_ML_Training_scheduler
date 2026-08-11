import requests
from urllib.parse import quote
from config import (
    SCHEDULER_QUEUE_URL, SCHEDULER_UPDATE_URL, SCHEDULER_LOG_URL,
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