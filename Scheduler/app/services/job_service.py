from app.models.worker_model import Worker
from sqlalchemy.orm import Session
from app.models.job_model import Job, JobStatus
from app.schemas.worker_schema import WorkerResource

def create_job(db: Session, job_data: dict):
    db_job = Job(
        id=job_data["id"],
        script_path=job_data["script_path"],
        config=job_data.get("config")
    )
    db.add(db_job)
    #Tells the session: “I want to insert this object into the database.”
    #Object is staged, not yet written to the database.
    # SQLAlchemy keeps track of changes in a transactional “unit of work.”
    db.commit()
    #Writes the staged object into the database.
    # A SQL INSERT statement is executed.
    # After this, the job exists in the database.
    # The transaction is committed; the changes are now permanent.
    db.refresh(db_job)
    return db_job

def set_job_pending(db: Session, job_id: str):
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise Exception("Job not found")

    if job.status != JobStatus.NOT_RUNNABLE:
        raise Exception("Job is not in NOT_RUNNABLE state")

    job.status = JobStatus.PENDING

    db.commit()
    db.refresh(job)

    return job


def get_first_pending_job(db: Session,request: WorkerResource):
    # Query the first job with status PENDING
    
    worker = db.query(Worker).filter(Worker.id == request.worker_id).first()
    
    if not worker:
        raise Exception("Worker not found")
    
    
    job = db.query(Job).filter(Job.status == JobStatus.PENDING).order_by(Job.created_at).first()
    
    if not job:
        # If no pending job exists, raise an exception or return None
        return None
    
    # Convert job object to dictionary to send all fields
    job_dict = {
        "id": job.id,
        "script_path": job.script_path,
        "config": job.config,
        "status": job.status.value,  # Enum to string
        "vram_required": job.vram_required,
        "created_at": job.created_at,
        "updated_at": job.updated_at
    }
    
    return job_dict