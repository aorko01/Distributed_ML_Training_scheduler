from fastapi import APIRouter, HTTPException
from app.schemas.worker_schema import WorkerInfo
from app.schemas.heartbeat_schema import HeartbeatSchema
from app.services.worker_service import register_or_update_worker_service, process_heartbeat

router = APIRouter(tags=["workers"])

@router.post("/register")
async def register_worker(worker: WorkerInfo):
    await register_or_update_worker_service(worker)
    return {"status": "success", "worker_id": worker.worker_id}


@router.post("/heartbeat")
async def worker_heartbeat(data: HeartbeatSchema):
    success = await process_heartbeat(
        worker_id=data.worker_id,
        available_vram=data.available_vram,
        gpu_type=data.gpu_type
    )
    if not success:
        raise HTTPException(status_code=404, detail="Worker not registered")
    return {"status": "success", "worker_id": data.worker_id}