import hmac
import logging
import os
import stat
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session
from app.api.deps import get_db, get_current_active_user
from app.models.interactive_workspace_model import InteractiveWorkspace as Workspace
from app.models.job_model import Job
from app.schemas.interactive_workspace_schema import BASE_IMAGES, FromJob, RevisionTraining, Claim, Heartbeat, Ready, Failure, Logs
from app.services import interactive_workspace_service as service
from app.utils.interactive_archive import MAX_UPLOAD

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix='/interactive/workspaces', tags=['interactive workspaces'])
internal_router = APIRouter(prefix='/internal/interactive/builds', tags=['internal interactive builder'])


def key(value: str = Header(alias='Idempotency-Key')):
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', value):
        raise HTTPException(422, 'Invalid idempotency key')
    return value


def builder_auth(authorization: str = Header(default='')):
    try:
        path = os.environ['INTERACTIVE_BUILDER_SECRET_FILE']
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o027 or info.st_size > 256:
                raise ValueError()
            secret = os.read(fd, 257).decode('ascii').strip()
        finally:
            os.close(fd)
        if len(secret) < 32 or len(secret) > 256 or len(set(secret)) < 8:
            raise ValueError()
    except (KeyError, OSError, ValueError, UnicodeError):
        logger.exception("interactive_workspace builder_auth_unavailable")
        raise HTTPException(503, 'Builder authentication unavailable') from None
    if not hmac.compare_digest(authorization.encode(), ('Bearer ' + secret).encode()):
        logger.warning("interactive_workspace builder_auth_rejected")
        raise HTTPException(401, 'Builder authentication required')


@router.get('/base-images')
def bases(user=Depends(get_current_active_user)):
    return [{'id': identifier, 'label': reference} for identifier, reference in BASE_IMAGES.items()]


@router.get('/source-jobs')
def sources(db: Session = Depends(get_db), user=Depends(get_current_active_user)):
    jobs = db.query(Job).filter(Job.user_id == user.user_id, Job.image_tag.isnot(None), Job.status.in_(service.IMAGE_JOB_STATES)).order_by(Job.created_at.desc()).all()
    return [{'id': job.id, 'name': job.name or job.id, 'source_kind': getattr(job, 'source_kind', None) or 'ARCHIVE',
             'source_image_label': job.image_tag} for job in jobs if job.image_tag]


@router.post('/from-upload', status_code=201)
async def upload(request: Request, name: str = Form(min_length=1, max_length=120), base_image_id: str = Form(),
                 requirements: str | None = Form(default=None),
                 file: UploadFile | None = File(default=None), request_key=Depends(key), db: Session = Depends(get_db), user=Depends(get_current_active_user)):
    import json as _json
    form = await request.form()
    if set(form.keys()) - {'name', 'base_image_id', 'requirements', 'file'} or any(len(form.getlist(k)) != 1 for k in form.keys()):
        raise HTTPException(422, 'Unexpected upload fields')
    if not name.strip():
        raise HTTPException(422, 'Name required')
    parsed_requirements = None
    if requirements not in (None, ''):
        if len(requirements) > 2048:
            raise HTTPException(422, 'Requirements too large')
        try:
            raw = _json.loads(requirements)
        except Exception:
            raise HTTPException(422, 'Invalid requirements') from None
        try:
            from app.schemas.interactive_capacity_schema import ResourceRequirements
            parsed_requirements = ResourceRequirements(**raw).canonical()
        except Exception:
            raise HTTPException(422, 'Invalid requirements') from None
    if file is None or not file.filename:
        # No archive submitted: an empty workspace is created from the base
        # image with just a placeholder requirements.txt.
        return service.create(db, user.user_id, request_key, name.strip(), 'UPLOAD', base_image_id, None, parsed_requirements)
    data = await file.read(MAX_UPLOAD + 1)
    return service.create(db, user.user_id, request_key, name.strip(), 'UPLOAD', base_image_id, data, parsed_requirements)


@router.post('/from-job', status_code=201)
def from_job(body: FromJob, request_key=Depends(key), db: Session = Depends(get_db), user=Depends(get_current_active_user)):
    return service.create(db, user.user_id, request_key, body.name, 'EXISTING_JOB', body.source_job_id, None, body.requirements)


@router.get('')
def listing(db: Session = Depends(get_db), user=Depends(get_current_active_user)):
    return [service.public(db, item) for item in db.query(Workspace).filter_by(owner_user_id=user.user_id).order_by(Workspace.created_at.desc()).all()]


@router.get('/{workspace_id}')
def detail(workspace_id: str, db: Session = Depends(get_db), user=Depends(get_current_active_user)):
    return service.public(db, service.owned(db, user.user_id, workspace_id))


@router.post('/{workspace_id}/training', status_code=201)
def submit_revision_training(workspace_id: str, body: RevisionTraining, request_key=Depends(key),
                             db: Session = Depends(get_db), user=Depends(get_current_active_user)):
    return service.submit_revision_training(db, user.user_id, workspace_id, request_key, body)


@router.get('/{workspace_id}/build-logs')
def logs(workspace_id: str, db: Session = Depends(get_db), user=Depends(get_current_active_user)):
    item = service.owned(db, user.user_id, workspace_id)
    rev = service.revision(db, item.id)
    return {'lines': rev.build_logs or [], 'state': rev.state}


@router.delete('/{workspace_id}')
def cancel(workspace_id: str, db: Session = Depends(get_db), user=Depends(get_current_active_user)):
    return service.cancel(db, user.user_id, workspace_id)


@internal_router.post('/claim', dependencies=[Depends(builder_auth)])
def claim(body: Claim, db: Session = Depends(get_db)):
    return {'work_item': service.claim(db, body.builder_id)}


@internal_router.post('/heartbeat', dependencies=[Depends(builder_auth)])
def heartbeat(body: Heartbeat, db: Session = Depends(get_db)):
    return service.heartbeat(db, body)


@internal_router.post('/ready', dependencies=[Depends(builder_auth)])
def ready(body: Ready, db: Session = Depends(get_db)):
    return service.mark_ready(db, body)


@internal_router.post('/failure', dependencies=[Depends(builder_auth)])
def failure(body: Failure, db: Session = Depends(get_db)):
    return service.failure(db, body)


@internal_router.post('/release', dependencies=[Depends(builder_auth)])
def release(body: Failure, db: Session = Depends(get_db)):
    if body.failure_type != 'system':
        raise HTTPException(422, 'Release requires system failure')
    return service.failure(db, body)


@internal_router.post('/logs', dependencies=[Depends(builder_auth)])
def build_logs(body: Logs, db: Session = Depends(get_db)):
    return service.logs(db, body)


@internal_router.get('/snapshot/{operation_id}', dependencies=[Depends(builder_auth)])
def snapshot_read(operation_id: str, db: Session = Depends(get_db)):
    from app.services import snapshot_artifact_service as snapshots
    return snapshots.builder_descriptor(db, operation_id)
