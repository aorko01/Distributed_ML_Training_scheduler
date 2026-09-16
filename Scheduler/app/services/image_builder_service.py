"""Heartbeat and cancellation coordination for Docker image builders."""

import os
import time

from sqlalchemy.orm import Session

from app.core.redis import redis_client
from app.models.job_model import Job, JobStatus
from app.schemas.job_schema import ImageBuilderHeartbeat


HEARTBEAT_TIMEOUT_SECONDS = max(
    1, int(os.getenv("IMAGE_BUILDER_HEARTBEAT_TIMEOUT_SECONDS", "45"))
)
HEARTBEAT_KEY_TTL_SECONDS = max(HEARTBEAT_TIMEOUT_SECONDS * 4, 300)

BUILDER_HEARTBEAT_PREFIX = "image_builder_heartbeat:"
ATTEMPT_HEARTBEAT_PREFIX = "image_build_attempt_heartbeat:"


async def process_heartbeat(db: Session, heartbeat: ImageBuilderHeartbeat) -> dict:
    """Record valid attempt heartbeats and return stale attempts to cancel.

    The database assignment is authoritative. A builder that comes back after
    its job was requeued/reassigned reports its old attempt and receives a
    cancellation command instead of being allowed to publish stale output.
    """
    now = int(time.time())
    await redis_client.set(
        BUILDER_HEARTBEAT_PREFIX + heartbeat.builder_id,
        now,
        ex=HEARTBEAT_KEY_TTL_SECONDS,
    )

    job_ids = {active.job_id for active in heartbeat.active_builds}
    jobs = db.query(Job).filter(Job.id.in_(job_ids)).all() if job_ids else []
    jobs_by_id = {job.id: job for job in jobs}
    cancel_builds = []

    for active in heartbeat.active_builds:
        job = jobs_by_id.get(active.job_id)
        is_current = bool(
            job is not None
            and job.status == JobStatus.IMAGE_BUILDING
            and job.image_builder_id == heartbeat.builder_id
            and job.image_build_attempt_id == active.attempt_id
        )
        if not is_current:
            cancel_builds.append(active.model_dump())
            continue

        await redis_client.set(
            ATTEMPT_HEARTBEAT_PREFIX + active.attempt_id,
            now,
            ex=HEARTBEAT_KEY_TTL_SECONDS,
        )

    return {
        "status": "ok",
        "heartbeat_timeout_seconds": HEARTBEAT_TIMEOUT_SECONDS,
        "cancel_builds": cancel_builds,
    }
