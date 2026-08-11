import os
import requests
import logging
from config import (
    REGISTER_URL, HEARTBEAT_URL, PULL_JOB_URL, 
    UPLOAD_OUTPUT_URL, SAVE_VRAM_ESTIMATION_URL
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

    def upload_output(self, job_id: str, file_path: str):
        if not os.path.exists(file_path):
            logger.error("Output file not found: %s", file_path)
            return

        try:
            with open(file_path, "rb") as f:
                files = {"file": (os.path.basename(file_path), f)}
                resp = requests.post(UPLOAD_OUTPUT_URL, files=files, timeout=30)
                logger.info("Uploaded output for job %s: %s", job_id, resp.text)
        except Exception as e:
            logger.error("Failed to upload output file for job %s: %s", job_id, e)

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