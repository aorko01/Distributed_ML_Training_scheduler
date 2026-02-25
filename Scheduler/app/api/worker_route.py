from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.schemas.worker_schema import WorkerInfo, WorkerResponse
from app.services.worker_service import register_or_update_worker

router = APIRouter()

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/register", response_model=WorkerResponse)
def register_worker(worker: WorkerInfo, db: Session = Depends(get_db)):
    register_or_update_worker(db, worker)
    return WorkerResponse(message="Worker registered successfully", worker_id=worker.worker_id)