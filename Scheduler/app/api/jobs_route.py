import os
import uuid

from fastapi import APIRouter, Depends, UploadFile, File, Form
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.services import job_service
from app.utils.file_utils import save_and_extract_zip
from app.schemas.job_schema import Job_status_to_pending


router = APIRouter(tags=["jobs"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/submit_job")
async def submit_job(
    zip_file: UploadFile = File(...),
    entry_file: str = Form(...),
    db: Session = Depends(get_db)
):
    job_id = str(uuid.uuid4())  # Generate ONE shared ID here

    try:
        file_content = await zip_file.read()

        result = save_and_extract_zip(
            file_content=file_content,
            filename=zip_file.filename,
            entry_file=entry_file,
            require_files=["requirements.txt"],
            job_id=job_id  # Pass it in
        )

    except Exception as e:
        return {"error": str(e)}

    job_data = {
        "id": job_id,  # Pass it in
        "script_path": result["script_path"],
        "config": None
    }

    db_job = job_service.create_job(db, job_data)
    return db_job


@router.post("/update_job_to_pending")
def Update_job_to_pending(
    request: Job_status_to_pending,
    db: Session = Depends(get_db)
):
    try:
        job = job_service.set_job_pending(db, request.job_id)
        return {
            "job_id": job.id,
            "status": job.status.value
        }

    except Exception as e:
        return {"error": str(e)}