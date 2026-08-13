from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.services import worker_service
from app.schemas.worker_schema import WorkerInfo, WorkerResponse
from app.schemas.heartbeat_schema import HeartbeatSchema,HeartbeatResponse

router = APIRouter(tags=["workers"])

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/register", response_model=WorkerResponse)
def register_worker(worker: WorkerInfo, db: Session = Depends(get_db)):
    """Registers or updates a worker in the database."""
    db_worker = worker_service.register_or_update_worker_service(db, worker)
    return WorkerResponse(message="success", worker_id=db_worker.worker_id)


@router.post("/heartbeat", response_model=HeartbeatResponse)
async def worker_heartbeat(Heartbeat: HeartbeatSchema):
    success = await worker_service.process_heartbeat(Heartbeat)
    if not success:
        raise HTTPException(status_code=404, detail="Worker not registered")
    return HeartbeatResponse(status="success", worker_id=Heartbeat.worker_id)


@router.get("/total_gpus")
def get_total_gpus(db: Session = Depends(get_db)):
    """Returns the total number of GPUs across all registered workers."""
    total = worker_service.get_total_gpus(db)
    return {"total_gpus": total}