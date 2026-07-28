"""
Fault-detection watchdog.

Design: polling over Redis keyspace-notifications.
-----------------------------------------------------
Redis *could* push an "expired" pub/sub event the instant a worker's heartbeat
key times out (`notify-keyspace-events Ex`), which sounds more elegant. It's
deliberately not used here:
  - it requires non-default Redis server config, which is easy to forget when
    the deployment/infra changes (docker-compose image swap, managed Redis, etc.)
  - pub/sub delivery isn't durable -- if the scheduler process is briefly down
    or the subscriber connection drops, expiry events are lost forever and the
    job would hang in IN_PROGRESS with nobody watching it.
  - it needs its own reconnect/backoff logic to be reliable, at which point
    it's more moving parts than a poll loop for the same guarantee.

A simple poll loop is stateless and self-healing: every tick it recomputes
"which IN_PROGRESS jobs have a worker that's actually still alive" from
scratch, straight from Redis. A missed tick just means one extra
WATCHDOG_INTERVAL of delay before a dead job is noticed, not a permanently
stuck job.
"""

import asyncio
import logging
import os

from app.core.redis import redis_client
from app.db.database import SessionLocal
from app.services.job_service import get_in_progress_assignments, requeue_job_after_worker_death

logger = logging.getLogger("watchdog")

WATCHDOG_INTERVAL = int(os.getenv("WATCHDOG_INTERVAL", "5"))  # seconds


async def _sweep_once():
    db = SessionLocal()
    try:
        assignments = get_in_progress_assignments(db)
        if not assignments:
            return

        # One round-trip for all worker keys instead of one per job.
        worker_ids = {worker_id for _job_id, worker_id in assignments}
        pipe = redis_client.pipeline()
        for worker_id in worker_ids:
            pipe.exists(f"worker:{worker_id}")
        alive_flags = await pipe.execute()
        alive = {
            worker_id: bool(flag)
            for worker_id, flag in zip(worker_ids, alive_flags)
        }

        for job_id, worker_id in assignments:
            if alive.get(worker_id):
                continue  # heartbeat still active, job is fine

            job = requeue_job_after_worker_death(db, job_id, worker_id)
            if job is None:
                continue  # already moved on (completed/reassigned) concurrently

            if job.status.value == "FAILED":
                logger.error(
                    "Job %s exhausted retries after worker %s died; marking FAILED.",
                    job_id, worker_id,
                )
            else:
                logger.warning(
                    "Worker %s missed its heartbeat while running job %s "
                    "(attempt %d/%s) -- requeued as RETRY_PENDING.",
                    worker_id, job_id, job.retry_count,
                    os.getenv("MAX_JOB_RETRIES", "3"),
                )
    except Exception:
        # Never let one bad sweep kill the loop -- log and try again next tick.
        logger.exception("Watchdog sweep failed")
    finally:
        db.close()


async def run_watchdog_loop():
    logger.info("Fault-recovery watchdog started (interval=%ss).", WATCHDOG_INTERVAL)
    while True:
        await _sweep_once()
        await asyncio.sleep(WATCHDOG_INTERVAL)