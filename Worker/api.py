import requests
import logging
from config import (
    REGISTER_URL, HEARTBEAT_URL, PULL_JOB_URL,
    SAVE_VRAM_ESTIMATION_URL, MARK_COMPLETED_URL, SEND_LOG_URL,
)

logger = logging.getLogger("api")

class SchedulerAPI:
    def __init__(self, worker_id: str):
        self.worker_id = worker_id

    def register_worker(self, gpu_type: str, num_gpus: int, total_vram: float):
        payload = {
            "worker_id": self.worker_id,
            "gpu_type": gpu_type,
            "num_gpus": num_gpus,
            "total_vram": total_vram,
        }
        try:
            resp = requests.post(REGISTER_URL, json=payload, timeout=10)
            logger.info("Registered with scheduler: %s", resp.json())
        except Exception as e:
            logger.error("Failed to register worker: %s", e)

    def send_heartbeat(self, gpu_type: str, available_vram: float):
        payload = {
            "worker_id": self.worker_id,
            "gpu_type": gpu_type,
            "available_vram": available_vram,
        }
        resp = requests.post(HEARTBEAT_URL, json=payload, timeout=10)
        logger.info("Heartbeat sent: %s", resp.json())

    def pull_job(self, gpu_type: str, free_vram: float) -> dict | None:
        payload = {
            "worker_id": self.worker_id,
            "gpu_type": gpu_type,
            "free_vram": free_vram,
        }
        try:
            resp = requests.post(PULL_JOB_URL, json=payload, timeout=10)
            data = resp.json()

            if "message" in data:
                logger.info("No runnable jobs: %s", data["message"])
                return None
            if "error" in data:
                logger.error("Error from scheduler: %s", data["error"])
                return None
            return data
        except Exception as e:
            logger.error("Failed to pull job: %s", e)
            return None

    def mark_job_completed(self, job_id: str):
        try:
            resp = requests.post(MARK_COMPLETED_URL, json={"job_id": job_id}, timeout=10)
            resp.raise_for_status()
            logger.info("Marked job %s completed: %s", job_id, resp.json())
        except Exception as e:
            logger.error("Failed to mark job %s completed: %s", job_id, e)

    def send_logs(self, job_id: str, lines: list[str]):
        """Stream training log lines to the scheduler for realtime UI display."""
        if not lines:
            return
        try:
            resp = requests.post(
                f"{SEND_LOG_URL}/{job_id}",
                json={"lines": lines},
                timeout=5,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.debug("Failed to stream logs for job %s: %s", job_id, e)

    def save_vram_estimation(self, job_id: str, report: dict):
        payload = {
            "job_id": job_id,
            "vram_required": report["peak_reserved_memory"],
            "step_time": report["step_wall_time"],
        }
        try:
            response = requests.post(SAVE_VRAM_ESTIMATION_URL, json=payload, timeout=10)
            response.raise_for_status()
            logger.info("Saved VRAM estimation for job %s: %s", job_id, response.json())
        except Exception as e:
            logger.error("Failed to save VRAM estimation for job %s: %s", job_id, e)