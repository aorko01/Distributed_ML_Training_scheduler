from sqlalchemy.orm import Session
from app.models.job_model import Job, JobStatus

def create_job(db: Session, job_data: dict):
    db_job = Job(
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