from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.jobs import crud, schemas


router = APIRouter(prefix="/jobs", tags=["jobs"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        
@router.post("/", response_model=schemas.JobResponse)
def submit_job(job: schemas.JobCreate, db: Session = Depends(get_db)):
    return crud.create_job(db, job)


