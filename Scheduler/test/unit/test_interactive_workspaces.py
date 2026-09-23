import io
import zipfile
from datetime import timedelta
from unittest.mock import patch
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.api.deps import get_db, get_current_active_user
from app.api.interactive_workspace_route import router, internal_router
from app.models.interactive_workspace_model import InteractiveWorkspace as Workspace, InteractiveImageRevision as Revision
from app.models.job_model import JobStatus
from app.schemas.interactive_workspace_schema import Ready, Failure, Heartbeat, Logs
from app.services import interactive_workspace_service as service
from test.helpers import make_user, make_job


def archive(files=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as zip:
        for name, content in (files or {'requirements.txt': '', 'probe.py': 'print(1)'}).items():
            zip.writestr(zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0)), content)
    return output.getvalue()


@pytest.fixture
def created(db):
    user = make_user(db)
    with patch.object(service, 'save_to_object_store', return_value={'object_key': 'private/key'}):
        item = service.create(db, user.user_id, 'request-key-123456', 'test', 'UPLOAD', 'pytorch-2.5.1-cuda12.4', archive())
    return user, item


def ready_body(item):
    tag = f'user/interactive-{item["workspace_id"]}:revision-{item["id"]}-attempt-{item["attempt_id"]}'
    return Ready(builder_id=item['builder_id'], revision_id=item['id'], attempt_id=item['attempt_id'], image_tag=tag,
                 image_digest_ref=tag.split(':')[0] + '@sha256:' + 'a' * 64,
                 resolved_base_digest='pytorch/pytorch@sha256:' + 'b' * 64)


def test_upload_idempotency_and_private_status(db, created):
    user, item = created
    with patch.object(service, 'save_to_object_store') as store:
        retry = service.create(db, user.user_id, 'request-key-123456', 'test', 'UPLOAD', 'pytorch-2.5.1-cuda12.4', archive())
        assert retry['id'] == item['id']
        store.assert_not_called()
        with pytest.raises(HTTPException) as exc:
            service.create(db, user.user_id, 'request-key-123456', 'changed', 'UPLOAD', 'pytorch-2.5.1-cuda12.4', archive())
        assert exc.value.status_code == 409
    assert db.query(Workspace).count() == db.query(Revision).count() == 1
    assert 'private/key' not in str(item)


def test_from_job_ownership_and_image_states(db):
    a, b = make_user(db), make_user(db)
    job = make_job(db, a.user_id, status=JobStatus.FAILED, image_tag='user/source:build-a')
    ready = service.create(db, a.user_id, 'existing-request-123', 'debug', 'EXISTING_JOB', job.id)
    assert ready['revision']['state'] == 'QUEUED'
    assert 'user/source' not in str(ready)
    for identifier in (job.id, 'absent', make_job(db, b.user_id).id):
        with pytest.raises(HTTPException) as exc:
            service.create(db, b.user_id, 'new-request-' + identifier, 'debug', 'EXISTING_JOB', identifier)
        assert exc.value.status_code == 404


def test_claim_fencing_expiry_heartbeat_and_ready(db, created):
    user, workspace = created
    first = service.claim(db, 'builder-a')
    assert service.claim(db, 'builder-b') is None
    rev = db.get(Revision, first['id'])
    rev.lease_until = service.now() - timedelta(seconds=1)
    db.commit()
    assert service.expire(db) == 1
    assert service.claim(db, 'builder-a') is None
    second = service.claim(db, 'builder-b')
    assert first['attempt_id'] != second['attempt_id']
    with pytest.raises(HTTPException):
        service.mark_ready(db, ready_body(first))
    response = service.heartbeat(db, Heartbeat(builder_id='builder-a', active_builds=[{'revision_id': first['id'], 'attempt_id': first['attempt_id']}]))
    assert response['cancel_builds']
    service.mark_ready(db, ready_body(second))
    assert service.mark_ready(db, ready_body(second)) == {'status': 'ok'}
    item = service.public(db, service.owned(db, user.user_id, workspace['id']))
    assert item['revision']['state'] == 'IMAGE_READY'
    assert item['current_revision_id'] == second['id']
    with pytest.raises(HTTPException):
        service.cancel(db, user.user_id, workspace['id'])


def test_cancel_rejects_callbacks_and_wrong_owner(db, created):
    user, workspace = created
    claim = service.claim(db, 'builder')
    other = make_user(db)
    with pytest.raises(HTTPException) as exc:
        service.owned(db, other.user_id, workspace['id'])
    assert exc.value.status_code == 404
    assert service.cancel(db, user.user_id, workspace['id'])['revision']['state'] == 'CANCELLED'
    with pytest.raises(HTTPException):
        service.mark_ready(db, ready_body(claim))
    assert service.claim(db, 'other-builder') is None


@pytest.mark.parametrize('kind', ['user', 'system'])
def test_failure_and_bounded_retry_redaction(db, created, kind):
    _, workspace = created
    for index in range(3 if kind == 'system' else 1):
        claim = service.claim(db, f'builder-{index}')
        service.failure(db, Failure(builder_id=claim['builder_id'], revision_id=claim['id'], attempt_id=claim['attempt_id'], failure_type=kind, failure_reason='secret-token-value'))
    rev = service.revision(db, workspace['id'])
    assert rev.state == 'FAILED'
    assert 'secret-token' not in rev.failure_reason


def test_logs_are_fenced_and_redacted(db, created):
    claim = service.claim(db, 'builder')
    body = Logs(builder_id='builder', revision_id=claim['id'], attempt_id=claim['attempt_id'], lines=['Building workload image', 'SECRET printed by pip'])
    service.logs(db, body)
    assert db.get(Revision, claim['id']).build_logs == ['Building workload image']
    with pytest.raises(HTTPException):
        service.logs(db, body.model_copy(update={'attempt_id': 'stale'}))


def test_internal_api_auth_strict_and_owner_routes(db, created, tmp_path, monkeypatch):
    user, workspace = created
    secret = tmp_path / 'secret'; secret.write_text('0123456789abcdefghijklmnopqrstuvwxyzABCDEF'); secret.chmod(0o600)
    monkeypatch.setenv('INTERACTIVE_BUILDER_SECRET_FILE', str(secret))
    app = FastAPI(); app.include_router(router); app.include_router(internal_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_active_user] = lambda: user
    client = TestClient(app)
    path = '/internal/interactive/builds/claim'
    assert client.post(path, json={'builder_id': 'test'}).status_code == 401
    headers = {'Authorization': 'Bearer ' + secret.read_text()}
    assert client.post(path, json={'builder_id': 'test', 'command': 'id'}, headers=headers).status_code == 422
    assert client.post(path, json={'builder_id': 'test'}, headers=headers).status_code == 200
    assert len(client.get('/interactive/workspaces').json()) == 1
    other = make_user(db)
    app.dependency_overrides[get_current_active_user] = lambda: other
    assert client.get('/interactive/workspaces').json() == []
    assert client.get('/interactive/workspaces/' + workspace['id']).status_code == 404
    assert client.get('/interactive/workspaces/' + workspace['id'] + '/build-logs').status_code == 404


@pytest.mark.parametrize('files', [{'../requirements.txt': ''}, {'Dockerfile': 'FROM attacker', 'requirements.txt': ''}, {'file.py': 'x'}])
def test_upload_security(db, files):
    user = make_user(db)
    with patch.object(service, 'save_to_object_store') as store, pytest.raises(HTTPException):
        service.create(db, user.user_id, 'safe-request-12345', 'test', 'UPLOAD', 'pytorch-2.5.1-cuda12.4', archive(files))
    store.assert_not_called()
    assert db.query(Workspace).count() == 0


def test_upload_route_exact_fields_and_idempotent_retry(db):
    user = make_user(db)
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_active_user] = lambda: user
    client = TestClient(app)
    data = archive()
    headers = {'Idempotency-Key': 'upload-request-123456'}
    with patch.object(service, 'save_to_object_store', return_value={'object_key': 'owned-private-source'}) as store:
        first = client.post('/interactive/workspaces/from-upload', data={'name': 'Upload', 'base_image_id': 'pytorch-2.5.1-cuda12.4'}, files={'file': ('workspace.zip', data, 'application/zip')}, headers=headers)
        assert first.status_code == 201
        second = client.post('/interactive/workspaces/from-upload', data={'name': 'Upload', 'base_image_id': 'pytorch-2.5.1-cuda12.4'}, files={'file': ('workspace.zip', data, 'application/zip')}, headers=headers)
        assert second.status_code == 201
        assert first.json()['id'] == second.json()['id']
        assert store.call_count == 1
        assert 'owned-private-source' not in first.text
        invalid = client.post('/interactive/workspaces/from-upload', data={'name': 'Upload', 'base_image_id': 'pytorch-2.5.1-cuda12.4', 'command': 'id'}, files={'file': ('workspace.zip', data, 'application/zip')}, headers=headers)
        assert invalid.status_code == 422


def test_database_ready_constraint_and_rollback(db, created):
    from sqlalchemy.exc import IntegrityError
    user, workspace = created
    rev = service.revision(db, workspace['id'])
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            rev.state = 'IMAGE_READY'
            db.flush()
    assert db.get(Workspace, workspace['id']).current_revision_id is None
    assert db.get(Revision, rev.id).state == 'QUEUED'


def test_interactive_claim_is_not_a_batch_job(db, created):
    from app.models.job_model import Job
    service.claim(db, 'builder')
    assert db.query(Job).count() == 0
    assert db.query(Revision).one().state == 'BUILDING'


def test_empty_workspace_created_without_archive(db):
    user = make_user(db)
    with patch.object(service, 'save_to_object_store', return_value={'object_key': 'empty/key'}) as store:
        item = service.create(db, user.user_id, 'empty-workspace-key1', 'fresh', 'UPLOAD', 'pytorch-2.5.1-cuda12.4', None)
    stored = store.call_args[0][0]
    with zipfile.ZipFile(io.BytesIO(stored)) as bundled:
        assert bundled.namelist() == ['requirements.txt']
        assert bundled.read('requirements.txt') == b''
    rev = service.revision(db, item['id'])
    assert rev.origin == 'UPLOAD' and rev.source_object_key == 'empty/key'
    assert db.query(Workspace).filter_by(id=item['id']).one().source_type == 'UPLOAD'


def test_empty_workspace_route_without_file(db):
    user = make_user(db)
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_active_user] = lambda: user
    client = TestClient(app)
    with patch.object(service, 'save_to_object_store', return_value={'object_key': 'empty/key'}) as store:
        response = client.post('/interactive/workspaces/from-upload',
                               data={'name': 'Fresh', 'base_image_id': 'pytorch-2.5.1-cuda12.4'},
                               headers={'Idempotency-Key': 'no-archive-key-12345'})
    assert response.status_code == 201
    assert store.called
    assert db.query(Workspace).filter_by(name='Fresh').count() == 1
