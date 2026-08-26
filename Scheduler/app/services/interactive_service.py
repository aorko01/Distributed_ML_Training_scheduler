import logging
import os
import uuid
from datetime import datetime, timezone

import requests
from sqlalchemy.orm import Session

from app.models.interactive_session_model import (
    InteractiveSession,
    InteractiveSessionStatus,
)
from app.models.job_model import Job, JobStatus
from app.services import job_service

logger = logging.getLogger("interactive_service")

# Service URLs / credentials (set via environment).
GATEWAY_API_URL = os.getenv("GATEWAY_API_URL", "http://gateway:8200").rstrip("/")
HEADSCALE_MGMT_URL = os.getenv("HEADSCALE_MGMT_URL", "http://headscale-mgmt:8100").rstrip("/")
HEADSCALE_MGMT_AUTH_TOKEN = os.getenv("HEADSCALE_MGMT_AUTH_TOKEN", "change-me")
HEADSCALE_URL = os.getenv("HEADSCALE_URL", "")
PREAUTH_KEY_EXPIRY = int(os.getenv("PREAUTH_KEY_EXPIRY", "3600"))


class InteractiveServiceError(Exception):
    pass


def create_session(db: Session, user_id: str, base_job_id: str,
                   name: str | None = None) -> dict:
    """Full deployment chain for an interactive session:

    (a) create/reuse the interactive job record,
    (b) ask the Gateway to generate a session SSH keypair,
    (c) get a Headscale pre-auth key from the headscale_mgmt service,
    (d) persist the InteractiveSession as PENDING.

    There is no direct dispatch to any worker: the session is picked up by
    whichever idle worker next pulls a job (`POST /jobs/pull_job` returns it
    with flag="interactive" once the image is built). Workers are never told
    about scheduling decisions out-of-band.
    """
    base_job = db.query(Job).filter(Job.id == base_job_id).first()
    if not base_job:
        raise InteractiveServiceError("Base job not found")
    if base_job.user_id != user_id:
        raise InteractiveServiceError("Base job does not belong to this user")

    # (a) Interactive job record (idempotent per base job while pending/ready).
    job = job_service.create_interactive_job(
        db, {"base_job_id": base_job_id, "user_id": user_id, "name": name}
    )

    # A PENDING session for this job already exists (e.g. duplicate request);
    # return it instead of minting another set of keys.
    existing = (
        db.query(InteractiveSession)
        .filter(
            InteractiveSession.job_id == job.id,
            InteractiveSession.status.in_([
                InteractiveSessionStatus.PENDING,
                InteractiveSessionStatus.DEPLOYING,
                InteractiveSessionStatus.RUNNING,
                InteractiveSessionStatus.STOPPING,
            ]),
        )
        .first()
    )
    if existing:
        return {
            "session_id": existing.session_id,
            "job_id": existing.job_id,
            "status": existing.status.value,
        }

    session_id = str(uuid.uuid4())

    # (b) Gateway SSH keypair for this session.
    try:
        resp = requests.post(
            f"{GATEWAY_API_URL}/keys",
            json={"session_id": session_id},
            timeout=15,
        )
        resp.raise_for_status()
        gateway_data = resp.json()
    except Exception as e:
        raise InteractiveServiceError(f"Gateway key generation failed: {e}")
    ssh_public_key = gateway_data["public_key"]

    # (c) Headscale pre-auth key.
    try:
        resp = requests.post(
            f"{HEADSCALE_MGMT_URL}/auth-keys",
            json={
                "expiry_seconds": PREAUTH_KEY_EXPIRY,
                "reusable": True,
                "ephemeral": True,
            },
            headers={"Authorization": f"Bearer {HEADSCALE_MGMT_AUTH_TOKEN}"},
            timeout=15,
        )
        resp.raise_for_status()
        headscale_auth_key = resp.json()["auth_key"]
    except Exception as e:
        _delete_gateway_key(session_id)
        raise InteractiveServiceError(f"Headscale auth key creation failed: {e}")

    # (d) Persist as PENDING; an idle worker claims it via pull_job once the
    # builder has marked the image INTERACTIVE_READY.
    record = InteractiveSession(
        id=str(uuid.uuid4()),
        job_id=job.id,
        user_id=user_id,
        base_job_id=base_job_id,
        session_id=session_id,
        gateway_session_id=session_id,
        worker_id=None,
        ssh_public_key=ssh_public_key,
        headscale_auth_key=headscale_auth_key,
        status=InteractiveSessionStatus.PENDING,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return {
        "session_id": record.session_id,
        "job_id": record.job_id,
        "status": record.status.value,
    }


def update_session_ip(db: Session, session_id: str, headscale_ip: str | None,
                      status: str = "RUNNING") -> dict:
    record = get_session(db, session_id)
    if not record:
        raise InteractiveServiceError("Interactive session not found")

    if status == "RUNNING":
        record.headscale_ip = headscale_ip
        record.status = InteractiveSessionStatus.RUNNING
    elif status == "FAILED":
        record.status = InteractiveSessionStatus.FAILED
    elif status == "STOPPED":
        record.headscale_ip = None
        record.status = InteractiveSessionStatus.STOPPED
        record.stopped_at = datetime.now(timezone.utc)
    else:
        raise InteractiveServiceError(f"Unknown report status '{status}'")

    # Mirror state onto the associated interactive job.
    job = db.query(Job).filter(Job.id == record.job_id).first()
    if job:
        mapping = {
            "RUNNING": JobStatus.INTERACTIVE_RUNNING,
            "FAILED": JobStatus.FAILED,
            "STOPPED": JobStatus.INTERACTIVE_STOPPED,
        }
        if status in mapping:
            job.status = mapping[status]
        if status == "FAILED" and headscale_ip is None:
            job.failure_reason = "Worker reported interactive deployment failure"

    db.commit()
    db.refresh(record)

    return {
        "session_id": record.session_id,
        "headscale_ip": record.headscale_ip,
        "status": record.status.value,
    }


def stop_session(db: Session, session_id: str) -> dict:
    record = get_session(db, session_id)
    if not record:
        raise InteractiveServiceError("Interactive session not found")
    if record.status in (InteractiveSessionStatus.STOPPING,
                         InteractiveSessionStatus.STOPPED):
        return {"session_id": session_id, "status": record.status.value}

    # Best-effort cleanup of gateway key + headscale auth key. The container
    # itself is torn down by the worker: the stop command rides on the next
    # heartbeat response (workers have no inbound-reachable endpoint), and the
    # worker confirms via report_ip(STOPPED).
    if record.gateway_session_id:
        _delete_gateway_key(record.gateway_session_id)
    if record.headscale_auth_key:
        _revoke_headscale_key(record.headscale_auth_key)

    if record.status == InteractiveSessionStatus.PENDING:
        # Not claimed by any worker yet: cancel outright.
        record.status = InteractiveSessionStatus.STOPPED
        record.stopped_at = datetime.now(timezone.utc)
        job = db.query(Job).filter(Job.id == record.job_id).first()
        if job:
            job.status = JobStatus.INTERACTIVE_STOPPED
    else:
        # Claimed/running on a worker: flag STOPPING so the assigned worker
        # receives the stop command in its next heartbeat response.
        record.status = InteractiveSessionStatus.STOPPING

    db.commit()

    return {"session_id": session_id, "status": record.status.value}


def get_stop_sessions_for_worker(db: Session, worker_id: str) -> list[str]:
    """Sessions this worker must tear down; delivered via heartbeat response.

    Entries stay STOPPING until the worker reports the container stopped
    through report_ip, so redelivery until confirmation is safe (the worker's
    stop is idempotent)."""
    records = (
        db.query(InteractiveSession)
        .filter(
            InteractiveSession.worker_id == worker_id,
            InteractiveSession.status == InteractiveSessionStatus.STOPPING,
        )
        .all()
    )
    return [r.session_id for r in records]


def get_session(db: Session, session_id: str) -> InteractiveSession | None:
    return (
        db.query(InteractiveSession)
        .filter(InteractiveSession.session_id == session_id)
        .first()
    )


def list_sessions(db: Session) -> list[dict]:
    sessions = (
        db.query(InteractiveSession)
        .order_by(InteractiveSession.created_at.desc())
        .all()
    )
    return [
        {
            "session_id": s.session_id,
            "job_id": s.job_id,
            "user_id": s.user_id,
            "base_job_id": s.base_job_id,
            "worker_id": s.worker_id,
            "headscale_ip": s.headscale_ip,
            "status": s.status.value,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in sessions
    ]


def _delete_gateway_key(session_id: str):
    try:
        requests.delete(f"{GATEWAY_API_URL}/keys/{session_id}", timeout=10)
    except Exception as e:
        logger.warning("Failed to delete gateway key for %s: %s", session_id, e)


def _revoke_headscale_key(key: str):
    try:
        requests.delete(
            f"{HEADSCALE_MGMT_URL}/auth-keys/{key}",
            headers={"Authorization": f"Bearer {HEADSCALE_MGMT_AUTH_TOKEN}"},
            timeout=10,
        )
    except Exception as e:
        logger.warning("Failed to revoke headscale key: %s", e)
