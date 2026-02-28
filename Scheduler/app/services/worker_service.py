from app.core.redis import redis_client
from app.models.worker_model import Worker
from app.db.database import SessionLocal
import time

HEARTBEAT_TTL = 15

async def register_or_update_worker_service(worker_info):
    """Registers or updates a worker in DB (sync)"""
    from sqlalchemy import func
    db = SessionLocal()
    try:
        db_worker = db.query(Worker).filter(Worker.worker_id == worker_info.worker_id).first()
        if db_worker:
            db_worker.last_registered = func.now()
        else:
            db_worker = Worker(
                worker_id=worker_info.worker_id,
                mac_address=worker_info.mac_address,
                gpu_type=worker_info.gpu_type,
                num_gpus=worker_info.num_gpus,
                total_vram=worker_info.total_vram,
            )
            db.add(db_worker)
        db.commit()
        return db_worker
    finally:
        db.close()


async def process_heartbeat(worker_id: str, available_vram: float, gpu_type: str):
    """Handles heartbeat from worker and updates Redis"""
    key = f"worker:{worker_id}"

    exists_in_redis = await redis_client.exists(key)
    if exists_in_redis:
        await redis_client.hset(
            key,
            mapping={
                "available_vram": available_vram,
                "gpu_type": gpu_type,
                "last_heartbeat": int(time.time())
            }
        )
        await redis_client.expire(key, HEARTBEAT_TTL)
        return True

    # Worker not in Redis, check DB
    db = SessionLocal()
    try:
        worker = db.query(Worker).filter(Worker.worker_id == worker_id).first()
        if not worker:
            return False
        await redis_client.hset(
            key,
            mapping={
                "available_vram": available_vram,
                "gpu_type": gpu_type,
                "last_heartbeat": int(time.time())
            }
        )
        await redis_client.expire(key, HEARTBEAT_TTL)
        return True
    finally:
        db.close()