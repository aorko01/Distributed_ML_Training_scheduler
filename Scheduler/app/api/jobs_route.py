import os
import uuid

from fastapi import APIRouter, Depends, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from jose import jwt, JWTError
from app.db.database import SessionLocal
from app.services import job_service, log_service
from app.utils.file_utils import save_to_object_store
from app.utils.auth import SECRET_KEY, ALGORITHM
from app.schemas.job_schema import Job_status_to_vram_estimation_pending, JobIDRequest,VramEstimationReport, JobFailureReport, JobResumeRequest, InteractiveBuildRequest, InteractiveReadyRequest
from app.schemas.log_schema import LogLinesRequest
from app.schemas.worker_schema import WorkerResource
from app.models.user_model import User
from app.models.job_model import Job, JobStatus, JobPriority
from app.api.deps import get_current_active_user


router = APIRouter(tags=["jobs"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ws_authenticate(websocket: WebSocket, db: Session) -> User | None:
    """Authenticate a WebSocket connection from a `token` query parameter
    (browsers cannot set headers on WebSocket connections)."""
    token = websocket.query_params.get("token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str | None = payload.get("sub")
        if user_id is None:
            return None
    except JWTError:
        return None
    return db.query(User).filter(User.user_id == user_id).first()

@router.post("/submit_job")
async def submit_job(
    zip_file: UploadFile = File(...),
    name: str = Form(""),
    command: str = Form(...),
    resume_command: str = Form(""),
    docker_base_image: str = Form(...),
    vram_required: float | None = Form(None),
    request_for_priority: bool = Form(False),
    reason_for_priority: str = Form(""),
    current_user: User = Depends(get_current_active_user),
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

    priority = (
        JobPriority.REQUESTED
        if request_for_priority
        else JobPriority.NORMAL
    )

    job_data = {
        "id": job_id,  # Pass it in
        "user_id": current_user.user_id,
        "object_key": result["object_key"],
        "name": name.strip() or None,
        "command": command,
        "resume_command": resume_command.strip() or None,
        "docker_base_image": docker_base_image,
        "config": None,
        "vram_required": vram_required,
        "priority": priority,
        "reason_for_priority": reason_for_priority.strip() or None,
    }

    db_job = job_service.create_job(db, job_data)
    return db_job


@router.post("/submit_interactive")
def submit_interactive(
    request: InteractiveBuildRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Create an interactive job derived from an existing training job.

    No zip upload is required; the builder derives the base image tag from
    base_job_id and produces an SSH + Tailscale sandbox image.
    """
    try:
        db_job = job_service.create_interactive_job(db, {
            **request.dict(),
            "user_id": current_user.user_id,
        })
        return {
            "id": db_job.id,
            "user_id": db_job.user_id,
            "object_key": db_job.object_key,
            "name": db_job.name,
            "command": db_job.command,
            "resume_command": db_job.resume_command,
            "docker_base_image": db_job.docker_base_image,
            "config": db_job.config,
            "status": db_job.status.value,
            "priority": db_job.priority.value,
            "reason_for_priority": db_job.reason_for_priority,
            "vram_required": db_job.vram_required,
            "ram_required": db_job.ram_required,
            "build_type": db_job.build_type,
            "base_job_id": db_job.base_job_id,
            "created_at": db_job.created_at,
            "updated_at": db_job.updated_at,
            "device": db_job.device,
        }
    except Exception as e:
        return {"error": str(e)}


@router.post("/logs/{job_id}")
async def ingest_job_logs(job_id: str, request: LogLinesRequest):
    """Ingest realtime log lines from the Docker Image Builder / Worker
    and append them to the job's Redis stream."""
    try:
        await log_service.publish_log_lines(job_id, request.lines)
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


@router.post("/update_job_to_vram_estimation_pending")
def update_job_to_vram_estimation_pending(
    request: Job_status_to_vram_estimation_pending, db: Session = Depends(get_db)
):
    try:
        job = job_service.set_job_vram_estimation_pending(db, request.job_id)
        return {"job_id": job.id, "status": job.status.value}

    except Exception as e:
        return {"error": str(e)}


@router.post("/mark_interactive_ready")
def mark_interactive_ready(
    request: InteractiveReadyRequest, db: Session = Depends(get_db)
):
    """Mark an interactive job as INTERACTIVE_READY after the builder finishes."""
    try:
        job = job_service.mark_interactive_ready(db, request.job_id)
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
            ram_required=request.ram_required,
            step_time=request.step_time,
        )

        return {
            "job_id": job.id,
            "status": job.status.value,
            "vram_required": job.vram_required,
            "ram_required": job.ram_required,
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


@router.post("/resume")
async def resume_job(request: JobResumeRequest, db: Session = Depends(get_db)):
    """Let a restarted worker resume an in-progress job it was running before it
    went down, as long as the scheduler still has it IN_PROGRESS on this worker
    (i.e. before the stall watchdog requeues it as RETRY_NEEDED)."""
    try:
        job_info = await job_service.get_job_for_resume(
            db, request.job_id, request.worker_id, request.device
        )
        if job_info is None:
            return {"message": "Job is not in progress on this worker"}
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


@router.post("/mark_completed")
def mark_job_completed(request: JobIDRequest, db: Session = Depends(get_db)):
    try:
        job = job_service.set_to_completed(db, request.job_id)
        return {"job_id": job.id, "status": job.status.value}
    except Exception as e:
        return {"error": str(e)}


@router.post("/mark_failed")
def mark_job_failed(request: JobFailureReport, db: Session = Depends(get_db)):
    """Record a job failure reported by the Docker Image Builder or a Worker.

    failure_type "user" -> job marked FAILED (build/training code error).
    failure_type "system" -> job marked RETRY_NEEDED (infra issue, requeued later).
    """
    try:
        job = job_service.mark_job_failed(
            db=db,
            job_id=request.job_id,
            failure_type=request.failure_type,
            failure_reason=request.failure_reason,
        )
        return {
            "job_id": job.id,
            "status": job.status.value,
            "failure_reason": job.failure_reason,
        }
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

        job = db.query(Job).filter(Job.id == job_id).first()
        status = job.status.value if job else "UNKNOWN"

        return {"job_id": job_id, "status": status, "content": content}

    except Exception as e:
        return {"error": str(e)}


@router.get("/queue_length")
def get_queue_length(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    try:
        count = job_service.get_runnable_jobs_count(db)
        return {"queue_length": count}
    except Exception as e:
        return {"error": str(e)}


@router.get("/mine")
def get_my_jobs(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    try:
        jobs = job_service.get_user_jobs(db, current_user.user_id)
        return {"jobs": jobs}
    except Exception as e:
        return {"error": str(e)}


@router.get("/mine/count")
def get_my_jobs_count(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    try:
        count = job_service.get_user_jobs_count(db, current_user.user_id)
        return {"count": count}
    except Exception as e:
        return {"error": str(e)}


@router.get("/mine/gpu_hours")
def get_my_jobs_gpu_hours(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    try:
        gpu_hours = job_service.get_user_gpu_hours(db, current_user.user_id)
        return {"gpu_hours": gpu_hours}
    except Exception as e:
        return {"error": str(e)}


@router.get("/{job_id}/logs")
def get_job_logs(
    job_id: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Return the full build.log stored in the object store for a job.
    Used for finished jobs and as the 'previous logs' shown before realtime."""
    try:
        job = job_service.get_user_job_by_id(db, current_user.user_id, job_id)
        if job is None:
            return {"error": "Job not found"}
        content = log_service.fetch_build_log_from_object_store(job_id)
        return {"job_id": job_id, "status": job["status"], "content": content}
    except Exception as e:
        return {"error": str(e)}


@router.websocket("/{job_id}/logs/stream")
async def job_logs_stream(websocket: WebSocket, job_id: str):
    """Stream a job's logs in realtime.

    On first connect (no `after` query param) it sends the full Redis stream
    history via an `init` message, then forwards new entries as `log` messages.
    On reconnect a client passes `?after=<last stream id>` to resume without
    re-sending already-seen lines. A `done` message is sent when the job
    reaches a terminal status.
    """
    await websocket.accept()

    db = SessionLocal()
    try:
        user = _ws_authenticate(websocket, db)
        if user is None:
            await websocket.close(code=4401)
            return

        job = (
            db.query(Job)
            .filter(Job.id == job_id, Job.user_id == user.user_id)
            .first()
        )
        if job is None:
            await websocket.close(code=4404)
            return

        after = websocket.query_params.get("after")
        last_id = "0"
        if after:
            last_id = after
        else:
            history = await log_service.get_log_stream_history(job_id)
            if history:
                last_id = history[-1]["id"]
            await websocket.send_json({"type": "init", "lines": history})

        while True:
            messages = await log_service.read_log_stream(job_id, last_id)
            for message in messages:
                last_id = message["id"]
                await websocket.send_json({"type": "log", **message})

            job = (
                db.query(Job)
                .filter(Job.id == job_id, Job.user_id == user.user_id)
                .first()
            )
            if job and job.status.value in log_service.TERMINAL_STATUSES:
                await websocket.send_json({"type": "done", "status": job.status.value})
                await websocket.close()
                return

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        db.close()


@router.get("/{job_id}")
def get_job_by_id(
    job_id: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    try:
        job = job_service.get_user_job_by_id(db, current_user.user_id, job_id)
        if job is None:
            return {"error": "Job not found"}
        return job
    except Exception as e:
        return {"error": str(e)}
