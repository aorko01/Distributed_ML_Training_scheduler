"""Shared test helpers for Scheduler tests."""
import uuid


def make_user(db, username=None, email=None, password_hash="hashed"):
    from app.models.user_model import User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        user_id=str(uuid.uuid4()),
        username=username or f"user_{suffix}",
        email=email or f"user_{suffix}@example.com",
        name="Test User",
        hashed_password=password_hash,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_job(db, user_id, status=None, **overrides):
    from app.models.job_model import Job, JobPriority, JobStatus

    params = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "object_key": f"{uuid.uuid4()}/archive.zip",
        "command": "python train.py",
        "docker_base_image": "pytorch/pytorch:2.1-cuda11.8-cudnn8-runtime",
        "priority": JobPriority.NORMAL,
        "status": status or JobStatus.NOT_RUNNABLE,
    }
    params.update(overrides)
    job = Job(**params)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def make_worker(db, worker_id=None, **overrides):
    from app.models.worker_model import Worker

    params = {
        "worker_id": worker_id or str(uuid.uuid4()),
        "gpu_type": "NVIDIA A100",
        "num_gpus": 2,
        "total_vram": 80.0,
    }
    params.update(overrides)
    worker = Worker(**params)
    db.add(worker)
    db.commit()
    db.refresh(worker)
    return worker
