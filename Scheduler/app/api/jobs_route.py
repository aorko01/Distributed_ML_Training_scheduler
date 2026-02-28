from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.schemas import job_schema
from app.services import job_service


router = APIRouter(tags=["jobs"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        
@router.post("/", response_model=job_schema.JobResponse)
def submit_job(job: job_schema.JobCreate, db: Session = Depends(get_db)):
    return job_service.create_job(db, job)


