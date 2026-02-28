from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.heartbeat_schema import HeartbeatSchema
from app.services.heartbeat_service import save_heartbeat
from app.db.database import async_session  # async SQLAlchemy session

router = APIRouter(prefix="/heartbeat", tags=["heartbeat"])

# Dependency to get async DB session
async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session

@router.post("/")
async def heartbeat(data: HeartbeatSchema, db: AsyncSession = Depends(get_db)):
    """
    Receives a heartbeat from a worker and saves it to Redis.
    If the worker does not exist in Redis, validates against the DB first.
    """
    worker_id = data.worker_id
    available_vram = data.available_vram
    gpu_type = data.gpu_type

    # Save heartbeat to Redis (and validate DB if needed)
    await save_heartbeat(worker_id, available_vram, gpu_type)

    return {"status": "success", "worker_id": worker_id}