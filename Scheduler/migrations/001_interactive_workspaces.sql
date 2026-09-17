-- Additive, idempotent PostgreSQL migration. Execute before starting upgraded API.
CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_id_user_id ON jobs(id, user_id);
CREATE TABLE IF NOT EXISTS interactive_workspaces (
 id VARCHAR PRIMARY KEY, owner_user_id VARCHAR NOT NULL REFERENCES users(user_id),
 name VARCHAR(120) NOT NULL, source_type VARCHAR NOT NULL, source_job_id VARCHAR,
 current_revision_id VARCHAR, request_key VARCHAR(128) NOT NULL, request_hash VARCHAR(64) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(owner_user_id, request_key), UNIQUE(id, owner_user_id),
 FOREIGN KEY(source_job_id, owner_user_id) REFERENCES jobs(id, user_id),
 CHECK((source_type='UPLOAD' AND source_job_id IS NULL) OR (source_type='EXISTING_JOB' AND source_job_id IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS interactive_image_revisions (
 id VARCHAR PRIMARY KEY, workspace_id VARCHAR NOT NULL REFERENCES interactive_workspaces(id),
 revision_number INTEGER NOT NULL, origin VARCHAR NOT NULL,
 source_object_key VARCHAR, source_image_tag VARCHAR, requested_base_image VARCHAR,
 resolved_base_digest VARCHAR, state VARCHAR NOT NULL DEFAULT 'QUEUED', image_tag VARCHAR,
 image_digest_ref VARCHAR, failure_type VARCHAR, failure_reason VARCHAR,
 builder_id VARCHAR, attempt_id VARCHAR UNIQUE, started_at TIMESTAMPTZ, lease_until TIMESTAMPTZ,
 excluded_builder_id VARCHAR, excluded_until TIMESTAMPTZ, attempt_count INTEGER NOT NULL DEFAULT 0,
 build_logs JSON NOT NULL DEFAULT '[]',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(workspace_id, revision_number), UNIQUE(id, workspace_id),
 CHECK(revision_number > 0 AND attempt_count >= 0),
 CHECK(origin IN ('UPLOAD','EXISTING_JOB','SNAPSHOT')),
 CHECK(state IN ('QUEUED','BUILDING','IMAGE_READY','FAILED','CANCELLED')),
 CHECK(state != 'IMAGE_READY' OR (image_tag IS NOT NULL AND image_digest_ref IS NOT NULL AND resolved_base_digest IS NOT NULL)),
 CHECK(state != 'BUILDING' OR (builder_id IS NOT NULL AND attempt_id IS NOT NULL AND started_at IS NOT NULL AND lease_until IS NOT NULL)),
 CHECK((origin='UPLOAD' AND source_object_key IS NOT NULL AND requested_base_image IS NOT NULL AND source_image_tag IS NULL)
 OR (origin='EXISTING_JOB' AND source_image_tag IS NOT NULL AND source_object_key IS NULL AND requested_base_image IS NULL) OR origin='SNAPSHOT')
);
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_workspace_current_revision') THEN
  ALTER TABLE interactive_workspaces ADD CONSTRAINT fk_workspace_current_revision
  FOREIGN KEY(current_revision_id,id) REFERENCES interactive_image_revisions(id,workspace_id);
 END IF;
END $$;
CREATE INDEX IF NOT EXISTS ix_interactive_workspaces_owner_user_id ON interactive_workspaces(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_interactive_image_revisions_workspace_id ON interactive_image_revisions(workspace_id);
CREATE INDEX IF NOT EXISTS ix_interactive_image_revisions_state ON interactive_image_revisions(state);
CREATE INDEX IF NOT EXISTS ix_interactive_image_revisions_builder_id ON interactive_image_revisions(builder_id);
CREATE INDEX IF NOT EXISTS ix_interactive_image_revisions_lease_until ON interactive_image_revisions(lease_until);
CREATE UNIQUE INDEX IF NOT EXISTS ix_interactive_image_revisions_attempt_id ON interactive_image_revisions(attempt_id);

CREATE OR REPLACE FUNCTION dml_interactive_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='interactive_workspaces' THEN
  IF (OLD.owner_user_id,OLD.source_type,OLD.source_job_id,OLD.request_key,OLD.request_hash)
     IS DISTINCT FROM (NEW.owner_user_id,NEW.source_type,NEW.source_job_id,NEW.request_key,NEW.request_hash) THEN
   RAISE EXCEPTION 'workspace provenance is immutable';
  END IF;
 ELSE
  IF OLD.state='IMAGE_READY' AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
   RAISE EXCEPTION 'ready revision is immutable';
  END IF;
  IF (OLD.workspace_id,OLD.revision_number,OLD.origin,OLD.source_object_key,OLD.source_image_tag,OLD.requested_base_image)
     IS DISTINCT FROM (NEW.workspace_id,NEW.revision_number,NEW.origin,NEW.source_object_key,NEW.source_image_tag,NEW.requested_base_image) THEN
   RAISE EXCEPTION 'revision provenance is immutable';
  END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS interactive_workspace_immutable ON interactive_workspaces;
CREATE TRIGGER interactive_workspace_immutable BEFORE UPDATE ON interactive_workspaces FOR EACH ROW EXECUTE FUNCTION dml_interactive_immutable();
DROP TRIGGER IF EXISTS interactive_revision_immutable ON interactive_image_revisions;
CREATE TRIGGER interactive_revision_immutable BEFORE UPDATE ON interactive_image_revisions FOR EACH ROW EXECUTE FUNCTION dml_interactive_immutable();
