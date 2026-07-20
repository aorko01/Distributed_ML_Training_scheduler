from app.db.database import SessionLocal
from sqlalchemy.orm import Session
from app.models.job_model import Job, JobStatus


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def schedule(db: Session):
    # dummy scheduler function to be called periodically
    jobs = db.query(Job).filter(Job.status == JobStatus.RUNNABLE).all()
    return jobs
