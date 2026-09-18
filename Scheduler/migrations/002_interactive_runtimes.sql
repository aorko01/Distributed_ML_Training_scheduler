-- Ordered migration under the shared advisory lock. No runtime admission yet.
ALTER TABLE workers ADD COLUMN IF NOT EXISTS protocol_version INTEGER;
ALTER TABLE workers ADD COLUMN IF NOT EXISTS instance_id VARCHAR;
ALTER TABLE workers ADD COLUMN IF NOT EXISTS authenticated_heartbeat_at TIMESTAMPTZ;
ALTER TABLE workers ADD COLUMN IF NOT EXISTS inventory JSON;
ALTER TABLE workers ADD COLUMN IF NOT EXISTS execution_mode VARCHAR NOT NULL DEFAULT 'RECONCILING';
ALTER TABLE workers ADD COLUMN IF NOT EXISTS execution_paused BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE workers ADD COLUMN IF NOT EXISTS execution_draining BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE workers ADD COLUMN IF NOT EXISTS execution_reconciling BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE workers ADD COLUMN IF NOT EXISTS heartbeat_sequence INTEGER NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS interactive_runtimes (
 id VARCHAR PRIMARY KEY, workspace_id VARCHAR NOT NULL, owner_user_id VARCHAR NOT NULL,
 revision_id VARCHAR NOT NULL, image_digest_ref VARCHAR NOT NULL, generation INTEGER NOT NULL CHECK(generation > 0),
 profile_version VARCHAR NOT NULL, launch_spec JSON NOT NULL, request_key VARCHAR(128) NOT NULL,
 request_hash VARCHAR(64) NOT NULL, desired_state VARCHAR NOT NULL DEFAULT 'RUNNING' CHECK(desired_state IN ('RUNNING','STOPPED')),
 state VARCHAR NOT NULL DEFAULT 'QUEUED' CHECK(state IN ('QUEUED','ASSIGNED','PULLING','STARTING','CONNECTING','READY','STOPPING','LOST','STOPPED','FAILED')),
 assignment_id VARCHAR, created_at TIMESTAMPTZ NOT NULL, assigned_at TIMESTAMPTZ, ready_at TIMESTAMPTZ,
 stopped_at TIMESTAMPTZ, startup_deadline TIMESTAMPTZ, lifetime_deadline TIMESTAMPTZ, health_at TIMESTAMPTZ,
 health JSON NOT NULL DEFAULT '{}', failure_code VARCHAR, failure_detail VARCHAR(256), resource_id VARCHAR UNIQUE,
 enrollment_started BOOLEAN NOT NULL DEFAULT false, enrollment_id VARCHAR, endpoint_version VARCHAR, management_revoked BOOLEAN NOT NULL DEFAULT false,
 controller_token VARCHAR, controller_until TIMESTAMPTZ, connection_requested_at TIMESTAMPTZ,
 UNIQUE(workspace_id,generation), UNIQUE(workspace_id,request_key), UNIQUE(id,generation),
 FOREIGN KEY(workspace_id,owner_user_id) REFERENCES interactive_workspaces(id,owner_user_id),
 FOREIGN KEY(revision_id,workspace_id) REFERENCES interactive_image_revisions(id,workspace_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_runtime_live_workspace ON interactive_runtimes(workspace_id) WHERE state NOT IN ('STOPPED','FAILED');
CREATE TABLE IF NOT EXISTS worker_assignments (
 id VARCHAR PRIMARY KEY, worker_id VARCHAR NOT NULL REFERENCES workers(worker_id), instance_id VARCHAR NOT NULL,
 kind VARCHAR NOT NULL, job_id VARCHAR REFERENCES jobs(id), runtime_id VARCHAR REFERENCES interactive_runtimes(id),
 generation INTEGER, attempt_token VARCHAR NOT NULL UNIQUE, request_id VARCHAR NOT NULL, request_hash VARCHAR(64) NOT NULL,
 payload JSON NOT NULL, exclusive BOOLEAN NOT NULL DEFAULT false, gpu_uuid VARCHAR,
 state VARCHAR NOT NULL DEFAULT 'CLAIMED' CHECK(state IN ('CLAIMED','ACTIVE','CLEANING','LOST','RELEASED')),
 event_sequence INTEGER NOT NULL DEFAULT 0, event_hash VARCHAR, created_at TIMESTAMPTZ NOT NULL,
 lease_until TIMESTAMPTZ NOT NULL, released_at TIMESTAMPTZ, cleanup_ack BOOLEAN NOT NULL DEFAULT false,
 result JSON, result_hash VARCHAR,
 UNIQUE(worker_id,instance_id,request_id), FOREIGN KEY(runtime_id,generation) REFERENCES interactive_runtimes(id,generation),
 CHECK((kind IN ('vram_estimation','batch_training') AND job_id IS NOT NULL AND runtime_id IS NULL AND generation IS NULL AND NOT exclusive)
    OR (kind='interactive_access' AND job_id IS NULL AND runtime_id IS NOT NULL AND generation IS NOT NULL AND exclusive)),
 CHECK((released_at IS NULL AND state != 'RELEASED') OR (released_at IS NOT NULL AND state='RELEASED' AND cleanup_ack))
);
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conname='fk_runtime_assignment') THEN
  ALTER TABLE interactive_runtimes ADD CONSTRAINT fk_runtime_assignment FOREIGN KEY(assignment_id) REFERENCES worker_assignments(id);
 END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_assignment_live_job ON worker_assignments(job_id) WHERE released_at IS NULL AND job_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_assignment_live_runtime ON worker_assignments(runtime_id) WHERE released_at IS NULL AND runtime_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_assignment_live_interactive_worker ON worker_assignments(worker_id) WHERE released_at IS NULL AND kind='interactive_access';
CREATE INDEX IF NOT EXISTS ix_worker_assignments_worker_id ON worker_assignments(worker_id);
CREATE INDEX IF NOT EXISTS ix_assignment_expiry ON worker_assignments(lease_until) WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_runtime_queue ON interactive_runtimes(created_at,id) WHERE state='QUEUED';
CREATE OR REPLACE FUNCTION dml_runtime_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='interactive_runtimes' THEN
  IF (OLD.workspace_id,OLD.owner_user_id,OLD.revision_id,OLD.image_digest_ref,OLD.generation,OLD.profile_version,OLD.launch_spec::text,OLD.request_key,OLD.request_hash)
   IS DISTINCT FROM (NEW.workspace_id,NEW.owner_user_id,NEW.revision_id,NEW.image_digest_ref,NEW.generation,NEW.profile_version,NEW.launch_spec::text,NEW.request_key,NEW.request_hash)
  THEN RAISE EXCEPTION 'runtime source is immutable'; END IF;
 ELSE
  IF (OLD.worker_id,OLD.instance_id,OLD.kind,OLD.job_id,OLD.runtime_id,OLD.generation,OLD.attempt_token,OLD.request_id,OLD.request_hash,OLD.payload::text,OLD.exclusive,OLD.gpu_uuid)
   IS DISTINCT FROM (NEW.worker_id,NEW.instance_id,NEW.kind,NEW.job_id,NEW.runtime_id,NEW.generation,NEW.attempt_token,NEW.request_id,NEW.request_hash,NEW.payload::text,NEW.exclusive,NEW.gpu_uuid)
   OR OLD.released_at IS NOT NULL AND to_jsonb(OLD) IS DISTINCT FROM to_jsonb(NEW)
  THEN RAISE EXCEPTION 'assignment identity/tombstone is immutable'; END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS runtime_immutable ON interactive_runtimes;
CREATE TRIGGER runtime_immutable BEFORE UPDATE ON interactive_runtimes FOR EACH ROW EXECUTE FUNCTION dml_runtime_immutable();
DROP TRIGGER IF EXISTS assignment_immutable ON worker_assignments;
CREATE TRIGGER assignment_immutable BEFORE UPDATE ON worker_assignments FOR EACH ROW EXECUTE FUNCTION dml_runtime_immutable();
