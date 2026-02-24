from sqlalchemy.orm import Session
from app.models.job_model import Job, JobStatus
from app.schemas.job_schema import JobCreate

def create_job(db: Session, job: JobCreate):
    db_job = Job(
        script_path=job.script_path,
        dataset_path=job.dataset_path,
        config=job.config,
        status=JobStatus.PENDING
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