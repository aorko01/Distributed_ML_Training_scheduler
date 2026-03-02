from app.db.database import SessionLocal
from app.models.job_model import Job, JobStatus, VramStatus


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def schedule():
    #dummy scheduler function to be called periodically
    jobs = db.query(Job).filter(Job.status == JobStatus.PENDING).all()
    