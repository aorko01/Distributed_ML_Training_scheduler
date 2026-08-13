import time
import logging
import threading

from config import HEARTBEAT_INTERVAL, JOB_POLL_INTERVAL
from hardware import get_or_create_worker_id, get_gpu_info, collect_node_info, count_gpus_in_use
from api import SchedulerAPI
from executor import JobExecutor

logger = logging.getLogger("worker")

def heartbeat_loop(api: SchedulerAPI, stop_event: threading.Event):
    logger.info("Heartbeat thread started.")
    while not stop_event.is_set():
        try:
            gpu_type, _, free_vram, _, gpu_load = get_gpu_info()
            node_info = collect_node_info()
            api.send_heartbeat(
                gpu_type, free_vram,
                {**node_info, "gpu_load": gpu_load, "gpus_in_use": count_gpus_in_use()},
            )
        except Exception as e:
            logger.error("Heartbeat error: %s", e)
        stop_event.wait(HEARTBEAT_INTERVAL)

def job_loop(executor: JobExecutor, api: SchedulerAPI, stop_event: threading.Event):
    logger.info("Job thread started.")
    while not stop_event.is_set():
        try:
            gpu_type, _, free_vram, _, _ = get_gpu_info()
            job = api.pull_job(gpu_type, free_vram)
            if job:
                executor.process_job(job)
        except Exception as e:
            logger.error("Error processing job: %s", e)
        stop_event.wait(JOB_POLL_INTERVAL)

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
        {**node_info, "gpu_load": gpu_load, "available_vram": free_vram, "gpus_in_use": count_gpus_in_use()},
    )

    stop_event = threading.Event()

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop, args=(api, stop_event), name="heartbeat", daemon=True
    )
    job_thread = threading.Thread(
        target=job_loop, args=(executor, api, stop_event), name="job", daemon=True
    )
    heartbeat_thread.start()
    job_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Worker shutting down.")
        stop_event.set()

if __name__ == "__main__":
    main()
