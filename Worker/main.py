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
import interactive_handler
import container_health

logger = logging.getLogger("worker")

def heartbeat_loop(api: SchedulerAPI, stop_event: threading.Event):
    logger.info("Heartbeat thread started.")
    record_event("info", "Heartbeat thread started")
    while not stop_event.is_set():
        if is_paused():
            stop_event.wait(1.0)
            continue
        try:
            gpu_type, _, free_vram, _, gpu_load = get_gpu_info()
            node_info = collect_node_info()
            response = api.send_heartbeat(
                gpu_type, free_vram,
                {**node_info, "gpu_load": gpu_load, "gpus_in_use": count_gpus_in_use(),
                 "interactive_ssessions": interactive_handler.get_active_sessions()},
            )
            record_heartbeat(True)
            _handle_scheduler_commands(api, response or {})
        except Exception as e:
            logger.error("Heartbeat error: %s", e)
            record_heartbeat(False, str(e))
        stop_event.wait(runtime_config.get("heartbeat_interval"))

def _handle_scheduler_commands(api: SchedulerAPI, response: dict):
    """Act on commands piggybacked on the heartbeat response.

    The scheduler cannot reach workers directly (no public IP), so stop
    requests for interactive sessions are delivered here. Delivery repeats
    until the worker reports the container stopped via report_ip; stopping is
    idempotent."""

    for session_id in response.get("stop_sessions", []):
        logger.info("Scheduler requested stop for interactive session %s", session_id)
        record_event("info", f"Stopping interactive session {session_id} (scheduler request)")
        if not interactive_handler.stop_session_container(session_id):
            # Container not tracked locally (e.g. worker restarted after the
            # session was dispatched). Report stopped so the scheduler stops
            # redelivering and closes out the session.
            try:
                api.report_interactive_ip(session_id, None, "STOPPED")
            except Exception as e:
                logger.error("Failed to report stopped state for %s: %s", session_id, e)

def job_loop(executor: JobExecutor, api: SchedulerAPI, stop_event: threading.Event):
    logger.info("Job thread started.")
    record_event("info", "Job polling thread started")
    while not stop_event.is_set():
        if is_paused():
            stop_event.wait(1.0)
            continue
        try:
            # If the worker died mid-job and came back before the scheduler's
            # stall watchdog requeued it, pick the job back up first.
            executor.resume_persisted_job_if_any()

            gpu_type, _, free_vram, _, _ = get_gpu_info()
            job = api.pull_job(gpu_type, free_vram)
            if job:
                executor.process_job(job)
        except Exception as e:
            logger.error("Error processing job: %s", e)
        stop_event.wait(runtime_config.get("job_poll_interval"))

def main():
    worker_id = get_or_create_worker_id()
    logger.info("Worker starting. ID: %s", worker_id)
    record_event("info", f"Worker starting (id {worker_id})")

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

    container_health_thread = threading.Thread(
        target=container_health.container_health_loop,
        args=(stop_event,),
        name="container-health",
        daemon=True,
    )
    container_health_thread.start()

    api_host = os.getenv("WORKER_API_HOST", "127.0.0.1")
    api_port = int(os.getenv("WORKER_API_PORT", "8600"))
    server.set_executor_api(api)
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

if __name__ == "__main__":
    main()
