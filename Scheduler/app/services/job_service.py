from app.models.worker_model import Worker
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.models.job_model import Job, JobStatus
from app.schemas.worker_schema import WorkerResource


def create_job(db: Session, job_data: dict):
    db_job = Job(
        id=job_data["id"],
        object_key=job_data["object_key"],
        script_path=job_data["script_path"],
        config=job_data.get("config"),
        vram_required=job_data.get("vram_required")
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

    job.status = JobStatus.VRAM_ESTIMATION_PENDING

    db.commit()
    db.refresh(job)

    return job


def set_job_runnable(db: Session, job_id: str):
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise Exception("Job not found")

    if job.status != JobStatus.VRAM_ESTIMATION_PENDING:
        raise Exception("Job is not in VRAM_ESTIMATION_PENDING state")

    job.status = JobStatus.RUNNABLE

    db.commit()
    db.refresh(job)

    return job


def get_not_runnable_jobs(db: Session):
    jobs = (
        db.query(Job)
        .filter(Job.status == JobStatus.NOT_RUNNABLE)
        .order_by(Job.created_at)
        .all()
    )

    return [
        {
            "id": job.id,
            "object_key": job.object_key,
            "script_path": job.script_path,
            "config": job.config,
            "status": job.status.value,
            "vram_required": job.vram_required,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }
        for job in jobs
    ]


def get_first_runnable_job(db: Session, request: WorkerResource):
    # Get worker
    worker = db.query(Worker).filter(Worker.worker_id == request.worker_id).first()
    
    if not worker:
        raise Exception("Worker not found")

    # Get first runnable job the worker can run based on free VRAM.
    job = (
        db.query(Job)
        .filter(
            Job.status == JobStatus.RUNNABLE,
            or_(Job.vram_required.is_(None), Job.vram_required <= request.free_vram),
        )
        .order_by(Job.created_at)
        .first()
    )

    if not job:
        return None

    job.status = JobStatus.IN_PROGRESS
    db.commit()
    db.refresh(job)  # refresh to get updated values

    # Convert to dict
    job_dict = {
        "id": job.id,
        "object_key": job.object_key,
        "script_path": job.script_path,
        "config": job.config,
        "status": job.status.value,
        "vram_required": job.vram_required,
        "created_at": job.created_at,
        "updated_at": job.updated_at
    }

    return job_dict

def set_to_completed(db: Session, job_id: str):
    job = db.query(Job).filter(Job.id == job_id).first()

    if not job:
        raise Exception("Job not found")

    if job.status != JobStatus.IN_PROGRESS:
        raise Exception("Job is not in IN_PROGRESS state")

    job.status = JobStatus.COMPLETED

    db.commit()
    db.refresh(job)

    return job