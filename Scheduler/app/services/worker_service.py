from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.redis import redis_client
from app.models.worker_model import Worker
from app.models.job_model import Job, JobStatus
from app.db.database import SessionLocal
from app.schemas.heartbeat_schema import HeartbeatSchema
import time

HEARTBEAT_TTL = 15

# "last heartbeat sent" is kept in Redis (in-memory, fast read/write) instead of
# psql. It lives longer than the connection hash so we can detect workers that
# go quiet for several minutes.
LAST_HEARTBEAT_KEY_PREFIX = "worker_heartbeat:"
LAST_HEARTBEAT_TTL = 3600  # seconds; refreshed on every heartbeat

# Per-container liveness for interactive sessions, piggybacked on the worker
# heartbeat. Written by the worker (via process_heartbeat) and by
# interactive_service.update_session_ip when a container reports RUNNING.
INTERACTIVE_HEARTBEAT_KEY_PREFIX = "interactive_heartbeat:"
INTERACTIVE_HEARTBEAT_TTL = 3600  # seconds; refreshed on every heartbeat


def get_total_gpus(db: Session) -> int:
    """Total number of GPUs across all registered workers."""
    total = db.query(func.coalesce(func.sum(Worker.num_gpus), 0)).scalar()
    return int(total)


def _apply_worker_metrics(worker: Worker, metrics: dict):
    """Apply optional host/resource metrics to a Worker ORM instance."""
    for field in ("hostname", "ip_address", "available_vram", "gpus_in_use", "gpu_load", "cpu_load", "mem_usage", "cpu_cores", "total_ram", "total_disk", "available_disk"):
        value = metrics.get(field)
        if value is not None:
            setattr(worker, field, value)


def register_or_update_worker_service(db: Session, worker_info):
    """Registers or updates a worker in DB (sync)"""
    from sqlalchemy import func
    metrics = worker_info.model_dump()
    db_worker = db.query(Worker).filter(Worker.worker_id == worker_info.worker_id).first()
    if db_worker:
        db_worker.last_registered = func.now()  # type: ignore[assignment]
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
        await _update_interactive_heartbeats(Heartbeat)
        return True

    # Worker not in Redis, check DB
    db = SessionLocal()
    try:
        worker = db.query(Worker).filter(Worker.worker_id == Heartbeat.worker_id).first()
        if not worker:
            return False
        await _update_redis_worker(key, Heartbeat)
        await _update_db_worker_metrics(Heartbeat)
        await _update_interactive_heartbeats(Heartbeat)
        return True
    finally:
        db.close()


async def _update_interactive_heartbeats(Heartbeat: HeartbeatSchema):
    """Record liveness for each interactive container the worker reports as active.

    The worker piggybacks the list of running interactive session IDs on its
    heartbeat; we stamp a Redis key per session so the watchdog can detect a
    container that stops heartbeating even while the worker is still alive.
    """
    for sid in Heartbeat.interactive_ssessions:
        await redis_client.set(
            INTERACTIVE_HEARTBEAT_KEY_PREFIX + sid,
            int(time.time()),
            ex=INTERACTIVE_HEARTBEAT_TTL,
        )


async def _update_redis_worker(key: str, Heartbeat: HeartbeatSchema):
    mapping = {
        "available_vram": Heartbeat.available_vram,
        "gpu_type": Heartbeat.gpu_type,
        "last_heartbeat": int(time.time()),
    }
    if Heartbeat.gpus_in_use is not None:
        mapping["gpus_in_use"] = Heartbeat.gpus_in_use
    if Heartbeat.gpu_load is not None:
        mapping["gpu_load"] = Heartbeat.gpu_load
    if Heartbeat.cpu_load is not None:
        mapping["cpu_load"] = Heartbeat.cpu_load
    if Heartbeat.mem_usage is not None:
        mapping["mem_usage"] = Heartbeat.mem_usage
    if Heartbeat.cpu_cores is not None:
        mapping["cpu_cores"] = Heartbeat.cpu_cores
    if Heartbeat.total_ram is not None:
        mapping["total_ram"] = Heartbeat.total_ram
    if Heartbeat.total_disk is not None:
        mapping["total_disk"] = Heartbeat.total_disk
    if Heartbeat.available_disk is not None:
        mapping["available_disk"] = Heartbeat.available_disk
    await redis_client.hset(key, mapping=mapping)  # type: ignore[arg-type]
    await redis_client.expire(key, HEARTBEAT_TTL)

    # Persist the last heartbeat timestamp in Redis beyond the 15s connection
    # TTL, so the stall watcher can detect workers that stop heartbeating.
    await redis_client.set(
        LAST_HEARTBEAT_KEY_PREFIX + Heartbeat.worker_id,
        int(time.time()),
        ex=LAST_HEARTBEAT_TTL,
    )


async def get_last_heartbeat(worker_id: str) -> int | None:
    """Unix timestamp of the worker's last heartbeat, read from Redis."""
    raw = await redis_client.get(LAST_HEARTBEAT_KEY_PREFIX + worker_id)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


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
                    "gpus_in_use": Heartbeat.gpus_in_use,
                    "gpu_load": Heartbeat.gpu_load,
                    "cpu_load": Heartbeat.cpu_load,
                    "mem_usage": Heartbeat.mem_usage,
                    "cpu_cores": Heartbeat.cpu_cores,
                    "total_ram": Heartbeat.total_ram,
                    "total_disk": Heartbeat.total_disk,
                    "available_disk": Heartbeat.available_disk,
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
                "gpus_in_use": worker.gpus_in_use,
                "available_vram": worker.available_vram,
                "gpu_load": worker.gpu_load,
                "cpu_load": worker.cpu_load,
                "mem_usage": worker.mem_usage,
                "cpu_cores": worker.cpu_cores,
                "total_ram": worker.total_ram,
                "total_disk": worker.total_disk,
                "available_disk": worker.available_disk,
                "status": "online" if online else "offline",
                "running_jobs": int(running_jobs),
                "first_seen": worker.first_seen.isoformat() if worker.first_seen else None,
                "last_registered": (
                    worker.last_registered.isoformat() if worker.last_registered else None
                ),
            }
        )
    return result
