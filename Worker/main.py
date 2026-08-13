import time
import logging

from config import HEARTBEAT_INTERVAL, JOB_POLL_INTERVAL
from hardware import get_or_create_worker_id, get_gpu_info, collect_node_info
from api import SchedulerAPI
from executor import JobExecutor

logger = logging.getLogger("worker")

def main():
    worker_id = get_or_create_worker_id()
    logger.info("Worker starting. ID: %s", worker_id)

    # Initialize components
    api = SchedulerAPI(worker_id)
    executor = JobExecutor(api)

    # Register node
    gpu_type, total_vram, free_vram, num_gpus, gpu_load = get_gpu_info()
    node_info = collect_node_info()
    api.register_worker(
        gpu_type, num_gpus, total_vram,
        {**node_info, "gpu_load": gpu_load, "available_vram": free_vram},
    )

    last_heartbeat = 0

    while True:
        now = time.time()
        gpu_type, _, free_vram, _, gpu_load = get_gpu_info()
        node_info = collect_node_info()

        # Handle Heartbeat
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            try:
                api.send_heartbeat(gpu_type, free_vram, {**node_info, "gpu_load": gpu_load})
            except Exception as e:
                logger.error("Heartbeat error: %s", e)
            last_heartbeat = now

        # Handle Jobs
        try:
            job = api.pull_job(gpu_type, free_vram)
            if job:
                executor.process_job(job)
        except Exception as e:
            logger.error("Error processing job: %s", e)

        time.sleep(JOB_POLL_INTERVAL)

if __name__ == "__main__":
    main()