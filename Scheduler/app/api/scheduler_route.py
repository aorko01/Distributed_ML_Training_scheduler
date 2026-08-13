from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.services import scheduler_service

router = APIRouter(tags=["scheduler"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/health")
def health_check():
    return {"status": "ok", "service": "scheduler"}


@router.get("/overview")
async def cluster_overview(db: Session = Depends(get_db)):
    """Aggregate cluster stats for the admin Overview page."""
    return await scheduler_service.get_overview(db)


@router.get("/throughput")
def job_throughput(db: Session = Depends(get_db)):
    """Completed-job counts bucketed by daily/weekly/monthly/yearly."""
    return scheduler_service.get_throughput(db)
