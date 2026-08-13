from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.redis import redis_client
from app.models.worker_model import Worker
from app.models.job_model import Job, JobStatus
from app.db.database import SessionLocal
from app.schemas.heartbeat_schema import HeartbeatSchema
import time

HEARTBEAT_TTL = 15


def get_total_gpus(db: Session) -> int:
    """Total number of GPUs across all registered workers."""
    total = db.query(func.coalesce(func.sum(Worker.num_gpus), 0)).scalar()
    return int(total)


def _apply_worker_metrics(worker: Worker, metrics: dict):
    """Apply optional host/resource metrics to a Worker ORM instance."""
    for field in ("hostname", "ip_address", "available_vram", "gpu_load", "cpu_load", "mem_usage"):
        value = metrics.get(field)
        if value is not None:
            setattr(worker, field, value)


def register_or_update_worker_service(db: Session, worker_info):
    """Registers or updates a worker in DB (sync)"""
    from sqlalchemy import func
    metrics = worker_info.model_dump()
    db_worker = db.query(Worker).filter(Worker.worker_id == worker_info.worker_id).first()
    if db_worker:
        db_worker.last_registered = func.now()
        _apply_worker_metrics(db_worker, metrics)
    else:
        db_worker = Worker(
            worker_id=worker_info.worker_id,
            gpu_type=worker_info.gpu_type,
            num_gpus=worker_info.num_gpus,
            total_vram=worker_info.total_vram,
        )
        _apply_worker_metrics(db_worker, metrics)
        db.add(db_worker)
    db.commit()
    return db_worker


async def process_heartbeat(Heartbeat: HeartbeatSchema):
    """Handles heartbeat from worker, updates Redis and DB metrics."""
    key = f"worker:{Heartbeat.worker_id}"

    exists_in_redis = await redis_client.exists(key)
    if exists_in_redis:
        await _update_redis_worker(key, Heartbeat)
        await _update_db_worker_metrics(Heartbeat)
        return True

    # Worker not in Redis, check DB
    db = SessionLocal()
    try:
        worker = db.query(Worker).filter(Worker.worker_id == Heartbeat.worker_id).first()
        if not worker:
            return False
        await _update_redis_worker(key, Heartbeat)
        await _update_db_worker_metrics(Heartbeat)
        return True
    finally:
        db.close()


async def _update_redis_worker(key: str, Heartbeat: HeartbeatSchema):
    mapping = {
        "available_vram": Heartbeat.available_vram,
        "gpu_type": Heartbeat.gpu_type,
        "last_heartbeat": int(time.time()),
    }
    if Heartbeat.gpu_load is not None:
        mapping["gpu_load"] = Heartbeat.gpu_load
    if Heartbeat.cpu_load is not None:
        mapping["cpu_load"] = Heartbeat.cpu_load
    if Heartbeat.mem_usage is not None:
        mapping["mem_usage"] = Heartbeat.mem_usage
    await redis_client.hset(key, mapping=mapping)
    await redis_client.expire(key, HEARTBEAT_TTL)


async def _update_db_worker_metrics(Heartbeat: HeartbeatSchema):
    """Persist live resource metrics for a worker into the DB."""
    db = SessionLocal()
    try:
        worker = db.query(Worker).filter(Worker.worker_id == Heartbeat.worker_id).first()
        if worker:
            _apply_worker_metrics(
                worker,
                {
                    "hostname": Heartbeat.hostname,
                    "ip_address": Heartbeat.ip_address,
                    "available_vram": Heartbeat.available_vram,
                    "gpu_load": Heartbeat.gpu_load,
                    "cpu_load": Heartbeat.cpu_load,
                    "mem_usage": Heartbeat.mem_usage,
                },
            )
            db.commit()
    finally:
        db.close()


async def get_all_workers(db: Session) -> list[dict]:
    """Return every registered worker with live status and resource metrics."""
    workers = db.query(Worker).order_by(Worker.first_seen).all()
    result = []
    for worker in workers:
        online = bool(await redis_client.exists(f"worker:{worker.worker_id}"))
        running_jobs = (
            db.query(func.count(Job.id))
            .filter(Job.status == JobStatus.IN_PROGRESS, Job.device == worker.gpu_type)
            .scalar()
        )
        result.append(
            {
                "worker_id": worker.worker_id,
                "hostname": worker.hostname,
                "ip_address": worker.ip_address,
                "gpu_type": worker.gpu_type,
                "num_gpus": worker.num_gpus,
                "total_vram": worker.total_vram,
                "available_vram": worker.available_vram,
                "gpu_load": worker.gpu_load,
                "cpu_load": worker.cpu_load,
                "mem_usage": worker.mem_usage,
                "status": "online" if online else "offline",
                "running_jobs": int(running_jobs),
                "first_seen": worker.first_seen.isoformat() if worker.first_seen else None,
                "last_registered": (
                    worker.last_registered.isoformat() if worker.last_registered else None
                ),
            }
        )
    return result