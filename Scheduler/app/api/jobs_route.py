import os
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from jose import jwt, JWTError
from app.db.database import SessionLocal
from app.services import job_service, log_service, output_service
from app.utils.file_utils import save_to_object_store
from app.utils.auth import SECRET_KEY, ALGORITHM
from app.schemas.job_schema import Job_status_to_vram_estimation_pending, JobIDRequest,VramEstimationReport, JobFailureReport, JobResumeRequest
from app.schemas.log_schema import LogLinesRequest
from app.schemas.worker_schema import WorkerResource
from app.models.user_model import User
from app.models.job_model import Job, JobStatus, JobPriority
from app.api.deps import get_current_active_user


router = APIRouter(tags=["jobs"])


class _TemporaryFileResponse(FileResponse):
    """FileResponse that also cleans up when a client disconnects mid-send."""

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            output_service.cleanup_archive(str(self.path))


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
            job_id=job_id
        )

    except Exception as e:
        return {"error": str(e)}

    priority = (
        JobPriority.REQUESTED
        if request_for_priority
        else JobPriority.NORMAL
    )

    job_data = {
        "id": job_id,
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


@router.post("/logs/{job_id}")
async def ingest_job_logs(job_id: str, request: LogLinesRequest):
    """Ingest realtime log lines from the Docker Image Builder / Worker
    and append them to the job's Redis stream."""
    try:
        await log_service.publish_log_lines(job_id, request.lines)
        return {"ok": True}
    except Exception as e:
        # Producers rely on the HTTP status to decide whether a batch was
        # accepted. Returning 200 here silently discarded live log lines.
        raise HTTPException(status_code=503, detail="Log stream unavailable") from e


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


@router.post("/claim_for_building")
def claim_job_for_building(db: Session = Depends(get_db)):
    """Atomically claim the oldest NOT_RUNNABLE job for image building.

    The job's status is set to IMAGE_BUILDING so no other builder will pull it.
    Returns the job dict, or a message when no unbuilt jobs are available.
    """
    try:
        job = job_service.claim_job_for_building(db)
        if job is None:
            return {"message": "No unbuilt jobs available"}
        return job
    except Exception as e:
        return {"error": str(e)}


@router.post("/release_to_not_runnable")
def release_job_to_not_runnable(request: JobIDRequest, db: Session = Depends(get_db)):
    """Release a job from IMAGE_BUILDING back to NOT_RUNNABLE.

    Called by the Docker Image Builder when a system-level failure occurs so
    another builder thread/instance can retry the job.
    """
    try:
        job = job_service.release_job_to_not_runnable(db, request.job_id)
        return {"job_id": job.id, "status": job.status.value}
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
        # Sanitize: confine to output_dir. file.filename is attacker-controlled
        # (e.g. "../../etc/passwd"); basename + realpath check prevents escape.
        # Go up 3 levels: api → app → Scheduler
        base_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

        output_dir = os.path.realpath(os.path.join(base_dir, "output"))
        os.makedirs(output_dir, exist_ok=True)

        safe_filename = os.path.basename(file.filename or "")
        if not safe_filename or safe_filename in (".", ".."):
            return {"error": "Invalid filename"}
        file_path = os.path.realpath(os.path.join(output_dir, safe_filename))
        if file_path != output_dir and not file_path.startswith(output_dir + os.sep):
            return {"error": "Invalid filename: path traversal blocked"}

        job_id = os.path.splitext(safe_filename)[0]
        if not job_id or "/" in job_id or "\\" in job_id or ".." in job_id:
            return {"error": "Invalid job_id derived from filename"}

        # Save file (streamed read is fine for small outputs; large outputs
        # should use the object store presigned path instead)
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

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
        # Block traversal: job_id comes from the request body and was
        # previously joined unsanitized (e.g. "../../etc/passwd" → read
        # arbitrary files with forced ".txt" suffix).
        if not job_id or "/" in job_id or "\\" in job_id or ".." in job_id:
            return {"error": f"Invalid job_id {job_id!r}"}

        # Base directory: Scheduler/ (api -> app -> Scheduler)
        base_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

        output_dir = os.path.realpath(os.path.join(base_dir, "output"))

        # File path confined to output_dir
        file_path = os.path.realpath(os.path.join(output_dir, f"{os.path.basename(job_id)}.txt"))
        if file_path != output_dir and not file_path.startswith(output_dir + os.sep):
            return {"error": f"Invalid job_id {job_id!r}"}

        if not os.path.exists(file_path):
            return {"error": f"No output file found for job_id {job_id}"}

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


@router.get("/{job_id}/output/download")
def download_job_output(
    job_id: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Download a zip of the job's submitted upload plus all output-store files.

    The archive contains ``submitted/<upload-basename>`` (the original zip
    from the uploads bucket) and ``outputs/<rel-path>`` for every object
    stored under ``<job_id>/`` in the outputs bucket.
    """
    if not output_service.is_safe_job_id(job_id):
        raise HTTPException(status_code=400, detail=f"Invalid job_id {job_id!r}")

    job = job_service.get_user_job_by_id(db, current_user.user_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        archive_path = output_service.build_job_output_zip(
            job_id, job.get("object_key")
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    raw_name = (job.get("name") or job_id).strip() or job_id
    slug = re.sub(r"\s+", "_", raw_name.strip().lower())
    slug = re.sub(r"[^a-z0-9._-]", "_", slug)[:100] or str(job_id)
    filename = f"{slug}-output.zip"

    return _TemporaryFileResponse(
        archive_path,
        media_type="application/zip",
        filename=filename,
    )


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

            # This long-lived session may already have the Job in its identity
            # map.  Force the status query to refresh it so terminal updates
            # made by the worker are observed on the existing WebSocket.
            job = (
                db.query(Job)
                .populate_existing()
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
