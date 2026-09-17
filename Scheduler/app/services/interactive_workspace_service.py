import hashlib
import json
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from app.models.interactive_workspace_model import InteractiveWorkspace as Workspace, InteractiveImageRevision as Revision, new_id
from app.models.job_model import Job, JobStatus
from app.schemas.interactive_workspace_schema import resolve_base_image
from app.utils.file_utils import save_to_object_store
from app.utils.interactive_archive import validate_archive

LEASE_SECONDS = 45
MAX_ATTEMPTS = 3
IMAGE_JOB_STATES = {JobStatus.VRAM_ESTIMATION_PENDING, JobStatus.RUNNABLE, JobStatus.IN_PROGRESS, JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.RETRY_NEEDED}
# User build output can contain arbitrary secrets. Publish bounded stage logs only;
# never persist raw pip/Docker output on this new path.
SAFE_LOG_LINES = {'Resolving base image', 'Building workload image', 'Pushing workload image', 'Image ready', 'Build failed', 'Build cancelled'}
PUBLIC_FAILURES = {'user': 'Workload build failed. Check the archive, dependencies and source image.', 'system': 'Build infrastructure unavailable; retry limit reached.'}


def now():
    return datetime.now(timezone.utc)


def owned(db, owner, workspace_id):
    item = db.query(Workspace).filter_by(id=workspace_id, owner_user_id=owner).first()
    if not item:
        raise HTTPException(404, 'Workspace not found')
    return item


def revision(db, workspace_id):
    return db.query(Revision).filter_by(workspace_id=workspace_id).order_by(Revision.revision_number.desc()).first()


def public(db, item):
    rev = revision(db, item.id)
    return {'id': item.id, 'name': item.name, 'source_type': item.source_type, 'source_job_id': item.source_job_id,
            'current_revision_id': item.current_revision_id, 'created_at': item.created_at,
            'revision': {key: getattr(rev, key) for key in ('id', 'revision_number', 'origin', 'state', 'image_tag', 'image_digest_ref', 'failure_type', 'failure_reason', 'created_at', 'updated_at')}}


def request_hash(name, source, value, data=None):
    body = json.dumps([name, source, value, hashlib.sha256(data).hexdigest() if data is not None else None], separators=(',', ':'))
    return hashlib.sha256(body.encode()).hexdigest()


def create(db, owner, key, name, source, value, data=None):
    digest = request_hash(name, source, value, data)
    # Serialize retries from the same owner across processes, including storage.
    # This also prevents duplicate upload objects for the same request.
    from app.models.user_model import User
    db.query(User).filter_by(user_id=owner).with_for_update().one()
    existing = db.query(Workspace).filter_by(owner_user_id=owner, request_key=key).first()
    if existing:
        if existing.request_hash != digest:
            raise HTTPException(409, 'Idempotency key reused for a different request')
        return public(db, existing)
    workspace_id, revision_id = new_id(), new_id()
    values = {}
    if source == 'UPLOAD':
        if resolve_base_image(value) is None:
            raise HTTPException(422, 'Unsupported base image')
        try:
            validate_archive(data)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            result = save_to_object_store(data, 'workspace.zip', require_files=['requirements.txt'], job_id=f'interactive/{owner}/{revision_id}')
        except Exception:
            raise HTTPException(502, 'Workspace storage unavailable') from None
        values = {'source_object_key': result['object_key'], 'requested_base_image': value}
    else:
        job = db.query(Job).filter_by(id=value, user_id=owner).with_for_update().first()
        if not job or not job.image_tag or job.status not in IMAGE_JOB_STATES:
            raise HTTPException(404, 'Source job image not found')
        values = {'source_image_tag': job.image_tag}
    item = Workspace(id=workspace_id, owner_user_id=owner, name=name, source_type=source,
                     source_job_id=value if source == 'EXISTING_JOB' else None, request_key=key, request_hash=digest)
    rev = Revision(id=revision_id, workspace_id=workspace_id, revision_number=1, origin=source, state='QUEUED', **values)
    try:
        db.add(item)
        db.flush()
        db.add(rev)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(Workspace).filter_by(owner_user_id=owner, request_key=key).first()
        if not existing or existing.request_hash != digest:
            raise HTTPException(409, 'Conflicting request') from None
        return public(db, existing)
    return public(db, item)


def cancel(db, owner, workspace_id):
    item = owned(db, owner, workspace_id)
    rev = revision(db, item.id)
    changed = db.query(Revision).filter(Revision.id == rev.id, Revision.state.in_(['QUEUED', 'BUILDING'])).update({'state': 'CANCELLED', 'lease_until': None}, synchronize_session=False)
    db.commit()
    db.expire_all()
    if not changed and revision(db, item.id).state != 'CANCELLED':
        raise HTTPException(409, 'Only queued or building images can be cancelled')
    return public(db, item)


def expire(db, timestamp=None):
    timestamp = timestamp or now()
    rows = db.query(Revision).filter(Revision.state == 'BUILDING', Revision.lease_until <= timestamp).all()
    count = 0
    for rev in rows:
        count += db.query(Revision).filter_by(id=rev.id, state='BUILDING', attempt_id=rev.attempt_id).filter(Revision.lease_until <= timestamp).update({
            'state': 'FAILED' if rev.attempt_count >= MAX_ATTEMPTS else 'QUEUED',
            'failure_type': 'system', 'failure_reason': PUBLIC_FAILURES['system'] if rev.attempt_count >= MAX_ATTEMPTS else None,
            'excluded_builder_id': rev.builder_id, 'excluded_until': timestamp + timedelta(seconds=LEASE_SECONDS),
            'builder_id': None, 'attempt_id': None, 'started_at': None, 'lease_until': None}, synchronize_session=False)
    db.commit()
    return count


def claim(db, builder_id):
    expire(db)
    timestamp = now()
    for _ in range(4):
        rev = db.query(Revision).filter(Revision.state == 'QUEUED', or_(Revision.excluded_until.is_(None), Revision.excluded_until <= timestamp, Revision.excluded_builder_id != builder_id)).order_by(Revision.created_at, Revision.id).with_for_update(skip_locked=True).first()
        if not rev:
            return None
        attempt_id = new_id()
        changed = db.query(Revision).filter_by(id=rev.id, state='QUEUED').update({
            'state': 'BUILDING', 'builder_id': builder_id, 'attempt_id': attempt_id, 'started_at': timestamp,
            'lease_until': timestamp + timedelta(seconds=LEASE_SECONDS), 'attempt_count': Revision.attempt_count + 1,
            'failure_type': None, 'failure_reason': None, 'build_logs': []}, synchronize_session=False)
        db.commit()
        db.expire_all()
        if changed:
            rev = db.get(Revision, rev.id)
            item = db.get(Workspace, rev.workspace_id)
            return {'kind': 'interactive', 'id': rev.id, 'revision_id': rev.id, 'workspace_id': item.id,
                    'revision_number': rev.revision_number, 'origin': rev.origin, 'source_job_id': item.source_job_id,
                    'source_object_key': rev.source_object_key, 'source_image_tag': rev.source_image_tag,
                    'base_image': resolve_base_image(rev.requested_base_image), 'builder_id': builder_id, 'attempt_id': attempt_id}
    return None


def fence(db, attempt, allow_ready=False):
    rev = db.query(Revision).filter_by(id=attempt.revision_id, builder_id=attempt.builder_id, attempt_id=attempt.attempt_id).with_for_update().first()
    if not rev or rev.state not in (['BUILDING', 'IMAGE_READY'] if allow_ready else ['BUILDING']):
        raise HTTPException(409, 'Stale build attempt')
    if rev.state == 'BUILDING':
        deadline = rev.lease_until
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if deadline <= now():
            raise HTTPException(409, 'Expired build attempt')
    return rev


def mark_ready(db, attempt):
    rev = fence(db, attempt, allow_ready=True)
    repository = attempt.image_tag.rsplit(':', 1)[0]
    expected_suffix = f':revision-{rev.id}-attempt-{rev.attempt_id}'
    if not repository.endswith('/interactive-' + rev.workspace_id) or not attempt.image_tag.endswith(expected_suffix) or attempt.image_digest_ref.split('@')[0] != repository:
        raise HTTPException(422, 'Image reference does not match build attempt')
    if rev.state == 'IMAGE_READY':
        if (rev.image_tag, rev.image_digest_ref, rev.resolved_base_digest) != (attempt.image_tag, attempt.image_digest_ref, attempt.resolved_base_digest):
            raise HTTPException(409, 'Immutable revision already published')
        return {'status': 'ok'}
    rev.state = 'IMAGE_READY'
    rev.build_logs = ((rev.build_logs or []) + ['Image ready'])[-1000:]
    rev.image_tag = attempt.image_tag
    rev.image_digest_ref = attempt.image_digest_ref
    rev.resolved_base_digest = attempt.resolved_base_digest
    rev.lease_until = None
    db.query(Workspace).filter_by(id=rev.workspace_id).update({'current_revision_id': rev.id})
    db.commit()
    return {'status': 'ok'}


def failure(db, attempt):
    rev = fence(db, attempt)
    terminal = attempt.failure_type == 'user' or rev.attempt_count >= MAX_ATTEMPTS
    rev.state = 'FAILED' if terminal else 'QUEUED'
    rev.failure_type = attempt.failure_type
    rev.failure_reason = PUBLIC_FAILURES[attempt.failure_type] if terminal else None
    rev.excluded_builder_id = rev.builder_id
    rev.excluded_until = now() + timedelta(seconds=LEASE_SECONDS)
    rev.builder_id = rev.attempt_id = rev.started_at = rev.lease_until = None
    db.commit()
    return {'status': 'ok'}


def heartbeat(db, body):
    cancellations = []
    timestamp = now()
    for active in body.active_builds:
        changed = db.query(Revision).filter_by(id=active.revision_id, state='BUILDING', builder_id=body.builder_id, attempt_id=active.attempt_id).filter(Revision.lease_until > timestamp).update({'lease_until': timestamp + timedelta(seconds=LEASE_SECONDS)}, synchronize_session=False)
        if not changed:
            cancellations.append(active.model_dump())
    db.commit()
    return {'status': 'ok', 'heartbeat_timeout_seconds': LEASE_SECONDS, 'cancel_builds': cancellations}


def logs(db, body):
    rev = fence(db, body)
    rev.build_logs = ((rev.build_logs or []) + [line for line in body.lines if line in SAFE_LOG_LINES])[-1000:]
    db.commit()
    return {'status': 'ok'}
