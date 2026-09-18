"""Isolated real PostgreSQL migration/claim gate; no Docker/Redis dependency."""
import os
from pathlib import Path
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, DBAPIError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    url = make_url(os.environ['DATABASE_URL'])
    if url.get_backend_name() != 'postgresql':
        raise ValueError('This gate requires PostgreSQL')
    schema = 'interactive_test_' + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA {schema}'))
    os.environ['DATABASE_URL'] = url.update_query_dict({'options': '-csearch_path=' + schema}).render_as_string(hide_password=False)
    try:
        from app.db.database import Base, engine, legacy_tables, run_migrations, SessionLocal
        from app.models.user_model import User
        from app.models.job_model import Job
        import app.models.worker_model
        import app.models.resource_request_model
        from app.models.interactive_workspace_model import InteractiveWorkspace as Workspace, InteractiveImageRevision as Revision
        from app.services import interactive_workspace_service as service
        from app.schemas.interactive_workspace_schema import Ready
        # Simulate pre-interactive production schema (no composite job constraint).
        Base.metadata.create_all(engine, tables=legacy_tables())
        assert set(inspect(engine).get_table_names()) == {'users', 'jobs', 'workers', 'resource_requests'}, 'Migration-owned tables must not be created before upgrade'
        with engine.begin() as connection:
            connection.execute(text('ALTER TABLE jobs DROP CONSTRAINT uq_jobs_id_user_id'))
        run_migrations()
        run_migrations()  # Explicit additive migration must be rerunnable.
        assert {'interactive_workspaces', 'interactive_image_revisions', 'interactive_runtimes', 'worker_assignments',
                'workspace_save_operations', 'workspace_snapshot_artifacts', 'workspace_training_submissions'} <= set(inspect(engine).get_table_names()), 'Ordered migrations must create the full interactive schema'
        owner, workspace_id, revision_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        with SessionLocal() as db:
            db.add(User(user_id=owner, username='test-user', email='test@example.com', name='Test', hashed_password='test'))
            db.commit()
            db.add(Workspace(id=workspace_id, owner_user_id=owner, name='test', source_type='UPLOAD', request_key='request-123456789', request_hash='a'*64))
            db.commit()
            db.add(Revision(id=revision_id, workspace_id=workspace_id, revision_number=1, origin='UPLOAD', source_object_key='test/key', requested_base_image='pytorch-2.5.1-cuda12.4'))
            db.commit()
        def claim(name):
            with SessionLocal() as db:
                return service.claim(db, name)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim, ['builder-a', 'builder-b']))
        claimed = [result for result in results if result is not None]
        assert len(claimed) == 1, 'Concurrent builders must claim exactly once'
        current = claimed[0]
        tag = f'user/interactive-{workspace_id}:revision-{revision_id}-attempt-{current["attempt_id"]}'
        with SessionLocal() as db:
            service.mark_ready(db, Ready(builder_id=current['builder_id'], revision_id=revision_id, attempt_id=current['attempt_id'],
                image_tag=tag, image_digest_ref=tag.split(':')[0]+'@sha256:'+'a'*64, resolved_base_digest='pytorch/pytorch@sha256:'+'b'*64))
            assert db.get(Workspace, workspace_id).current_revision_id == revision_id
            # Ready image and provenance immutability are database enforced.
            for statement in ('UPDATE interactive_image_revisions SET state=\'QUEUED\' WHERE id=:id',
                              'UPDATE interactive_workspaces SET source_type=\'EXISTING_JOB\' WHERE id=:id'):
                try:
                    db.execute(text(statement), {'id': revision_id if 'image_revisions' in statement else workspace_id})
                    db.commit()
                except DBAPIError:
                    db.rollback()
                else:
                    raise AssertionError('Database accepted immutable mutation')
        print('PostgreSQL additive migration, concurrent claim and immutability gate passed')
        engine.dispose()
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin.dispose()


if __name__ == '__main__':
    main()
