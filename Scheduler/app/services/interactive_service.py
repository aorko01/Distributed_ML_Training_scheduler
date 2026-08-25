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
from app.models.worker_model import Worker
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


def create_session(db: Session, user_id: str, base_job_id: str) -> dict:
    """Full deployment chain for an interactive session:

    (a) create/reuse the interactive job record,
    (b) ask the Gateway to generate a session SSH keypair,
    (c) get a Headscale pre-auth key from the headscale_mgmt service,
    (d) select a worker (prefer the one that ran the base job),
    (e) dispatch `POST /api/interactive/run` to the worker,
    (f) persist the InteractiveSession as DEPLOYING.
    """
    base_job = db.query(Job).filter(Job.id == base_job_id).first()
    if not base_job:
        raise InteractiveServiceError("Base job not found")
    if base_job.user_id != user_id:
        raise InteractiveServiceError("Base job does not belong to this user")

    # (a) Interactive job record (idempotent per base job while pending/ready).
    job = job_service.create_interactive_job(
        db, {"base_job_id": base_job_id, "user_id": user_id}
    )
    image_tag = f"{os.getenv('DOCKER_HUB_USERNAME', 'aorko123')}/{job.id}:latest"

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

    # (d) Worker selection: prefer the worker running the base job, else any online.
    worker = _select_worker(db, base_job)
    if worker is None:
        _delete_gateway_key(session_id)
        raise InteractiveServiceError("No online worker available for interactive session")

    # (f) Persist session first so the worker's report_ip callback finds it.
    record = InteractiveSession(
        id=str(uuid.uuid4()),
        job_id=job.id,
        user_id=user_id,
        base_job_id=base_job_id,
        session_id=session_id,
        gateway_session_id=session_id,
        worker_id=worker.worker_id,
        ssh_public_key=ssh_public_key,
        headscale_auth_key=headscale_auth_key,
        status=InteractiveSessionStatus.DEPLOYING,
    )
    db.add(record)

    job.status = JobStatus.INTERACTIVE_DEPLOYING
    db.commit()
    db.refresh(record)

    # (e) Dispatch to the worker (push-based; not part of the pull queue).
    payload = {
        "flag": "interactive",
        "session_id": session_id,
        "image_tag": image_tag,
        "headscale_url": HEADSCALE_URL,
        "headscale_auth_key": headscale_auth_key,
        "ssh_public_key": ssh_public_key,
    }
    try:
        _dispatch_to_worker(worker.hostname or worker.ip_address, 8600, payload)
    except Exception as e:
        record.status = InteractiveSessionStatus.FAILED
        job.status = JobStatus.FAILED
        job.failure_reason = f"Interactive dispatch failed: {e}"[:2000]
        db.commit()
        _delete_gateway_key(session_id)
        raise InteractiveServiceError(f"Worker dispatch failed: {e}")

    return {
        "session_id": record.session_id,
        "job_id": record.job_id,
        "status": record.status.value,
    }


def _select_worker(db: Session, base_job: Job) -> Worker | None:
    """Prefer a worker already associated with this base job (its image is
    likely cached there), else fall back to the first online worker."""
    previous = (
        db.query(InteractiveSession)
        .filter(
            InteractiveSession.base_job_id == base_job.id,
            InteractiveSession.worker_id.isnot(None),
        )
        .order_by(InteractiveSession.created_at.desc())
        .first()
    )

    online_workers = db.query(Worker).filter(Worker.is_testing.isnot(True)).all()
    if not online_workers:
        return None

    if previous:
        for w in online_workers:
            if w.worker_id == previous.worker_id:
                return w
    return online_workers[0]


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
            "FAILED": JobStatus.INTERACTIVE_DEPLOYING,
            "STOPPED": JobStatus.INTERACTIVE_STOPPED,
        }
        if status in mapping:
            job.status = mapping[status]

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
    if record.status == InteractiveSessionStatus.STOPPED:
        return {"session_id": session_id, "status": "STOPPED"}

    # Best-effort cleanup of gateway key + headscale auth key. The container
    # itself is torn down by the worker (out of scope for the MVP chain).
    if record.gateway_session_id:
        _delete_gateway_key(record.gateway_session_id)
    if record.headscale_auth_key:
        _revoke_headscale_key(record.headscale_auth_key)

    record.status = InteractiveSessionStatus.STOPPED
    record.headscale_ip = None
    record.stopped_at = datetime.now(timezone.utc)

    job = db.query(Job).filter(Job.id == record.job_id).first()
    if job:
        job.status = JobStatus.INTERACTIVE_STOPPED

    db.commit()

    return {"session_id": session_id, "status": record.status.value}


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


def _dispatch_to_worker(worker_host: str, worker_port: int, payload: dict):
    url = f"http://{worker_host}:{worker_port}/api/interactive/run"
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error("Dispatch to worker %s failed: %s", worker_host, e)
        raise


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
