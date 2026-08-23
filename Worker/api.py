import requests
import logging
import config

logger = logging.getLogger("api")


def _url(path: str) -> str:
    return f"{config.get_scheduler_url()}{path}"


class SchedulerAPI:
    def __init__(self, worker_id: str):
        self.worker_id = worker_id

    def register_worker(self, gpu_type: str, num_gpus: int, total_vram: float, node_info: dict):
        payload = {
            "worker_id": self.worker_id,
            "gpu_type": gpu_type,
            "num_gpus": num_gpus,
            "total_vram": total_vram,
            **node_info,
        }
        try:
            resp = requests.post(_url("/workers/register"), json=payload, timeout=10)
            logger.info("Registered with scheduler: %s", resp.json())
        except Exception as e:
            logger.error("Failed to register worker: %s", e)

    def send_heartbeat(self, gpu_type: str, available_vram: float, node_info: dict):
        payload = {
            "worker_id": self.worker_id,
            "gpu_type": gpu_type,
            "available_vram": available_vram,
            **node_info,
        }
        resp = requests.post(_url("/workers/heartbeat"), json=payload, timeout=10)
        logger.info("Heartbeat sent: %s", resp.json())

    def pull_job(self, gpu_type: str, free_vram: float) -> dict | None:
        payload = {
            "worker_id": self.worker_id,
            "gpu_type": gpu_type,
            "free_vram": free_vram,
        }
        try:
            resp = requests.post(_url("/jobs/pull_job"), json=payload, timeout=10)
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
            resp = requests.post(_url("/jobs/mark_completed"), json={"job_id": job_id}, timeout=10)
            resp.raise_for_status()
            logger.info("Marked job %s completed: %s", job_id, resp.json())
        except Exception as e:
            logger.error("Failed to mark job %s completed: %s", job_id, e)

    def mark_job_failed(self, job_id: str, failure_type: str, failure_reason: str = ""):
        """Report a job failure to the scheduler.

        failure_type: "user" (train/code error -> FAILED) or "system" (infra -> RETRY_NEEDED).
        """
        payload = {
            "job_id": job_id,
            "failure_type": failure_type,
            "failure_reason": failure_reason[:2000],
        }
        try:
            resp = requests.post(_url("/jobs/mark_failed"), json=payload, timeout=10)
            resp.raise_for_status()
            logger.info("Marked job %s failed (%s): %s", job_id, failure_type, resp.json())
        except Exception as e:
            logger.error("Failed to mark job %s failed: %s", job_id, e)

    def resume_job(self, job_id: str, gpu_type: str) -> dict | None:
        """Ask the scheduler whether an in-progress job is still assigned to this
        worker so a restarted worker can resume it before the stall watchdog
        requeues it. Returns the job payload (flag='retry') when resumable, or
        None when the job is no longer in progress on this worker. Raises on
        network errors so the caller can keep its local resume state."""
        try:
            resp = requests.post(
                _url("/jobs/resume"),
                json={
                    "job_id": job_id,
                    "worker_id": self.worker_id,
                    "device": gpu_type,
                },
                timeout=10,
            )
            data = resp.json()
            if "error" in data or "message" in data:
                return None
            return data
        except Exception as e:
            logger.error("Failed to check resume eligibility for job %s: %s", job_id, e)
            raise

    def send_logs(self, job_id: str, lines: list[str]):
        """Stream training log lines to the scheduler for realtime UI display."""
        if not lines:
            return
        try:
            resp = requests.post(
                f"{_url('/jobs/logs')}/{job_id}",
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
            "ram_required": report["peak_ram_memory"],
            "step_time": report["step_wall_time"],
        }
        try:
            response = requests.post(_url("/jobs/save_vram_estimation"), json=payload, timeout=10)
            response.raise_for_status()
            logger.info("Saved VRAM estimation for job %s: %s", job_id, response.json())
        except Exception as e:
            logger.error("Failed to save VRAM estimation for job %s: %s", job_id, e)
