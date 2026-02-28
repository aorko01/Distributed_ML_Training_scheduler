import time
from app.core.redis import redis_client
from app.models.worker_model import Worker
from app.db.database import SessionLocal

HEARTBEAT_TTL = 15

async def save_heartbeat(worker_id: str, available_vram: float, gpu_type: str):
    key = f"worker:{worker_id}"

    # Check if worker already exists in Redis
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
        return

    #If not in Redis, validate against synchronous DB
    db = SessionLocal()
    try:
        worker = db.query(Worker).filter(Worker.worker_id == worker_id).first()
        if not worker:
            # Worker not in DB, ignore heartbeat
            return

        # 3️⃣ Insert into Redis
        await redis_client.hset(
            key,
            mapping={
                "available_vram": available_vram,
                "gpu_type": gpu_type,
                "last_heartbeat": int(time.time())
            }
        )
        await redis_client.expire(key, HEARTBEAT_TTL)
    finally:
        db.close()