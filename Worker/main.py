import time
import logging
import threading
import os

from hardware import get_or_create_worker_id, get_gpu_info, collect_node_info, count_gpus_in_use
from api import SchedulerAPI
from executor import JobExecutor
from telemetry import record_heartbeat, record_event, is_paused
import runtime_config
import server

logger = logging.getLogger("worker")

def heartbeat_loop(api: SchedulerAPI, executor: JobExecutor, stop_event: threading.Event):
    logger.info("Heartbeat thread started.")
    record_event("info", "Heartbeat thread started")
    while not stop_event.is_set():
        if is_paused():
            stop_event.wait(1.0)
            continue
        try:
            gpu_type, total_vram, free_vram, _, gpu_load = get_gpu_info()
            reported_free = executor.get_effective_free_vram(free_vram, total_vram)
            node_info = collect_node_info()
            api.send_heartbeat(
                gpu_type, reported_free,
                {**node_info, "gpu_load": gpu_load, "gpus_in_use": count_gpus_in_use()},
            )
            record_heartbeat(True)
        except Exception as e:
            logger.error("Heartbeat error: %s", e)
            record_heartbeat(False, str(e))
        stop_event.wait(runtime_config.get("heartbeat_interval"))

def job_loop(executor: JobExecutor, api: SchedulerAPI, stop_event: threading.Event):
    logger.info("Job thread started.")
    record_event("info", "Job polling thread started")
    active_threads: list[threading.Thread] = []
    try:
        while not stop_event.is_set():
            if is_paused():
                stop_event.wait(1.0)
                continue
            try:
                active_threads = [t for t in active_threads if t.is_alive()]
                max_jobs = int(runtime_config.get("max_concurrent_jobs") or 2)
                if executor.active_jobs_count < max_jobs:
                    if executor.has_unresumed_job():
                        resuming = getattr(executor, "_resuming", None)
                        if not isinstance(resuming, set) or not resuming:
                            executor._resuming.add("__resume__")
                            threading.Thread(
                                target=executor.resume_persisted_job_if_any,
                                daemon=True, name="resume",
                            ).start()
                    gpu_type, total_vram, free_vram, _, _ = get_gpu_info()
                    effective_free = executor.get_effective_free_vram(free_vram, total_vram)
                    job = api.pull_job(gpu_type, effective_free)
                    if job:
                        job_id = job.get("job_id") or job.get("id")
                        if job_id and executor.try_begin_job(job_id, job.get("vram_required")):
                            t = threading.Thread(
                                target=executor.process_job, args=(job,),
                                name=f"job-{job_id}", daemon=True,
                            )
                            t.start()
                            active_threads.append(t)
                        else:
                            stop_event.wait(runtime_config.get("job_poll_interval"))
                    else:
                        stop_event.wait(runtime_config.get("job_poll_interval"))
                else:
                    stop_event.wait(runtime_config.get("job_poll_interval"))
            except Exception as e:
                logger.error("Error processing job: %s", e)
        for t in active_threads:
            t.join(timeout=1.0)
    finally:
        for t in active_threads:
            if t.is_alive():
                t.join(timeout=1.0)

def main():
    worker_id = get_or_create_worker_id()
    logger.info("Worker starting. ID: %s", worker_id)
    record_event("info", f"Worker starting (id {worker_id})")

    api = SchedulerAPI(worker_id)
    executor = JobExecutor(api)

    gpu_type, total_vram, free_vram, num_gpus, gpu_load = get_gpu_info()
    node_info = collect_node_info()
    api.register_worker(
        gpu_type, num_gpus, total_vram,
        {**node_info, "gpu_load": gpu_load, "available_vram": free_vram, "gpus_in_use": count_gpus_in_use()},
    )

    stop_event = threading.Event()

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop, args=(api, executor, stop_event), name="heartbeat", daemon=True
    )
    job_thread = threading.Thread(
        target=job_loop, args=(executor, api, stop_event), name="job", daemon=True
    )
    heartbeat_thread.start()
    job_thread.start()

    api_host = os.getenv("WORKER_API_HOST", "127.0.0.1")
    api_port = int(os.getenv("WORKER_API_PORT", "8600"))
    server.run_in_thread(api_host, api_port)
    logger.info("Worker Agent API listening on http://%s:%s", api_host, api_port)
    record_event("info", f"Worker Agent API listening on http://{api_host}:{api_port}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Worker shutting down.")
        record_event("info", "Worker shutting down")
        stop_event.set()
        job_thread.join(timeout=5.0)
        heartbeat_thread.join(timeout=2.0)

if __name__ == "__main__":
    main()
