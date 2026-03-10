import os

from fastapi import APIRouter, Depends, UploadFile, File, Form
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.services import job_service
from app.utils.file_utils import save_and_extract_zip

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
    try:
        file_content = await zip_file.read()

        result = save_and_extract_zip(
            file_content=file_content,
            filename=zip_file.filename,
            entry_file=entry_file,
            require_files=["requirements.txt"]
        )

    except Exception as e:
        return {"error": str(e)}

    job_data = {
        "script_path": result["script_path"],
        "config": None
    }

    db_job = job_service.create_job(db, job_data)
    return db_job
