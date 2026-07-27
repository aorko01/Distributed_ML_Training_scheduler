import os
import uuid

from fastapi import APIRouter, Depends, UploadFile, File, Form
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.services import job_service
from app.utils.file_utils import save_to_object_store
from app.schemas.job_schema import Job_status_to_vram_estimation_pending, JobIDRequest,VramEstimationReport
from app.schemas.worker_schema import WorkerResource


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
    command: str = Form(...),
    docker_base_image: str = Form(...),
    vram_required: float | None = Form(None),
    db: Session = Depends(get_db)
):
    job_id = str(uuid.uuid4())  # Generate ONE shared ID here

    try:
        file_content = await zip_file.read()

        result = save_to_object_store(
            file_content=file_content,
            filename=zip_file.filename,
            require_files=["requirements.txt"],
            job_id=job_id  # Pass it in
        )

    except Exception as e:
        return {"error": str(e)}

    job_data = {
        "id": job_id,  # Pass it in
        "object_key": result["object_key"],
        "command": command,
        "docker_base_image": docker_base_image,
        "config": None,
        "vram_required": vram_required
    }

    db_job = job_service.create_job(db, job_data)
    return db_job


@router.post("/update_job_to_vram_estimation_pending")
def update_job_to_vram_estimation_pending(
    request: Job_status_to_vram_estimation_pending, db: Session = Depends(get_db)
):
    try:
        job = job_service.set_job_vram_estimation_pending(db, request.job_id)
        return {"job_id": job.id, "status": job.status.value}

    except Exception as e:
        return {"error": str(e)}


@router.get("/unbuilt_jobs")
def get_unbuilt_jobs(db: Session = Depends(get_db)):
    try:
        jobs = job_service.get_not_runnable_jobs(db)
        return {"jobs": jobs}
    except Exception as e:
        return {"error": str(e)}

@router.post("/save_vram_estimation")
def save_vram_estimation(
    request: VramEstimationReport,
    db: Session = Depends(get_db),
):
    try:
        job = job_service.save_vram_estimation(
            db=db,
            job_id=request.job_id,
            vram_required=request.vram_required,
            step_time=request.step_time,
        )

        return {
            "job_id": job.id,
            "status": job.status.value,
            "vram_required": job.vram_required,
            "step_time": job.step_time,
        }

    except Exception as e:
        return {"error": str(e)}

@router.post("/pull_job")
async def pull_job(request: WorkerResource, db: Session = Depends(get_db)):
    try:
        job_info = await job_service.get_next_job_for_worker(db, request)
        if job_info is None:
            return {"message": "No runnable jobs available"}
        return job_info
    except Exception as e:
        return {"error": str(e)}


@router.post("/update_job_to_runnable")
def update_job_to_runnable(request: JobIDRequest, db: Session = Depends(get_db)):
    try:
        job = job_service.set_job_runnable(db, request.job_id)
        return {"job_id": job.id, "status": job.status.value}

    except Exception as e:
        return {"error": str(e)}


@router.post("/upload_output")
async def upload_output_file(
    file: UploadFile = File(...), db: Session = Depends(get_db)
):
    try:
        # Extract job_id from filename
        job_id = os.path.splitext(file.filename)[0]

        # Go up 3 levels: api → app → Scheduler
        base_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

        # Create output dir
        output_dir = os.path.join(base_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        # Save file
        file_path = os.path.join(output_dir, file.filename)

        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # ✅ mark job as completed
        job_service.set_to_completed(db, job_id)

        return {
            "message": "File uploaded and job marked as completed",
            "file_path": file_path,
            "job_id": job_id,
        }

    except Exception as e:
        return {"error": str(e)}


@router.post("/get_output_by_id")
def get_output_by_id(request: JobIDRequest, db: Session = Depends(get_db)):
    try:
        job_id = request.job_id

        # Base directory: Scheduler/ (api -> app -> Scheduler)
        base_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

        # Output directory: Scheduler/output
        output_dir = os.path.join(base_dir, "output")

        # File path
        file_path = os.path.join(output_dir, f"{job_id}.txt")

        if not os.path.exists(file_path):
            return {"error": f"No output file found for job_id {job_id}"}

        # Read file content
        with open(file_path, "r") as f:
            content = f.read()

        job = db.query(job_service.Job).filter(job_service.Job.id == job_id).first()
        status = job.status.value if job else "UNKNOWN"

        return {"job_id": job_id, "status": status, "content": content}

    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# Frontend bridge — appended below; original code above is untouched.
# Handles POST /jobs (resolves to POST /jobs because router is mounted at
# prefix="/jobs" in main.py).
# =============================================================================

import io as _io
import uuid as _uuid
import zipfile as _zipfile
from typing import Optional as _Optional
from pydantic import BaseModel as _BaseModel
from app.db.database import SessionLocal as _SessionLocal
from app.utils.file_utils import save_to_object_store as _save_to_object_store
from app.api.dashboard_route import build_dashboard_data as _build_dashboard_data


class FrontendSubmissionPayload(_BaseModel):
    """Matches the SubmissionPayload TypeScript interface in mockApi.ts."""
    projectTitle: str
    description: str
    runCommand: str
    vramMode: str = "auto"       # "auto" | "manual"
    vram: int = 24               # GB, only used when vramMode == "manual"
    torchVersion: str = ""
    cudaVersion: str = ""
    assetFile: _Optional[str] = None


@router.post("")
def submit_frontend_job(payload: FrontendSubmissionPayload):
    """
    Accepts the JSON SubmissionPayload sent by the Vite frontend's submitJob().
    The frontend only passes the filename string (not the actual File blob), so
    we synthesise a valid in-memory ZIP containing requirements.txt to satisfy
    save_to_object_store's validation, then persist the Job and return fresh
    DashboardData so the UI can update immediately.

    Route resolves to POST /jobs (router mounted at prefix="/jobs" in main.py).
    """
    job_id = str(_uuid.uuid4())

    # Build a valid dummy ZIP (contains requirements.txt for validation) ------
    buf = _io.BytesIO()
    with _zipfile.ZipFile(buf, mode="w", compression=_zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "requirements.txt",
            f"# auto-generated placeholder\ntorch=={payload.torchVersion or '2.3'}\n",
        )
        zf.writestr(
            "train.py",
            f"# placeholder entrypoint\n# run: {payload.runCommand}\n",
        )
    dummy_zip_bytes = buf.getvalue()
    dummy_filename = payload.assetFile or "training_bundle.zip"

    db = _SessionLocal()
    try:
        # Attempt to store in the object store; fall back to a local key ------
        try:
            store_result = _save_to_object_store(
                file_content=dummy_zip_bytes,
                filename=dummy_filename,
                require_files=["requirements.txt"],
                job_id=job_id,
            )
            object_key = store_result["object_key"]
        except Exception:
            object_key = f"{job_id}/{dummy_filename}"

        # Derive a Docker base image from the chosen torch/CUDA versions ------
        torch_ver = (payload.torchVersion or "2.3").strip()
        cuda_ver = (
            (payload.cudaVersion or "CUDA 12.1")
            .replace("CUDA ", "cu")
            .replace(".", "")
            .strip()
        )
        docker_image = f"pytorch/pytorch:{torch_ver}-{cuda_ver}-cudnn8-runtime"

        vram_required = float(payload.vram) if payload.vramMode == "manual" else None

        job_data = {
            "id":                job_id,
            "object_key":        object_key,
            "command":           payload.runCommand or "python train.py",
            "docker_base_image": docker_image,
            "config": {
                "projectTitle": payload.projectTitle,
                "description":  payload.description,
                "torchVersion": payload.torchVersion,
                "cudaVersion":  payload.cudaVersion,
                "vramMode":     payload.vramMode,
                "assetFile":    payload.assetFile,
            },
            "vram_required": vram_required,
        }

        job_service.create_job(db, job_data)

        # Return fresh dashboard state so the UI refreshes immediately --------
        return _build_dashboard_data(db)

    finally:
        db.close()
