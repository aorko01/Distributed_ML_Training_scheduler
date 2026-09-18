-- Additive workspace-editor state.  It is deliberately safe to apply twice.
ALTER TABLE interactive_runtimes ADD COLUMN IF NOT EXISTS access_service VARCHAR NOT NULL DEFAULT 'terminal';
ALTER TABLE interactive_runtimes ADD COLUMN IF NOT EXISTS application_protocol VARCHAR NOT NULL DEFAULT 'terminal-stream-v1';
ALTER TABLE interactive_runtimes ADD COLUMN IF NOT EXISTS editor_capable BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE interactive_runtimes ADD COLUMN IF NOT EXISTS workspace_root VARCHAR;
ALTER TABLE interactive_runtimes ADD COLUMN IF NOT EXISTS source_image_metadata JSON;
ALTER TABLE interactive_runtimes ADD COLUMN IF NOT EXISTS workspace_connection_requested_at TIMESTAMPTZ;
ALTER TABLE interactive_workspaces ADD COLUMN IF NOT EXISTS saved_revision_id VARCHAR;
ALTER TABLE interactive_image_revisions ADD COLUMN IF NOT EXISTS parent_revision_id VARCHAR;
ALTER TABLE interactive_image_revisions ADD COLUMN IF NOT EXISTS snapshot_operation_id VARCHAR;
ALTER TABLE interactive_image_revisions ADD COLUMN IF NOT EXISTS source_image_metadata JSON;

CREATE TABLE IF NOT EXISTS workspace_save_operations (
 id VARCHAR PRIMARY KEY, owner_user_id VARCHAR NOT NULL REFERENCES users(user_id), workspace_id VARCHAR NOT NULL,
 runtime_id VARCHAR NOT NULL, generation INTEGER NOT NULL, assignment_id VARCHAR, attempt_token VARCHAR,
 parent_revision_id VARCHAR NOT NULL, purpose VARCHAR NOT NULL CHECK(purpose IN ('SAVE','TRAIN')),
 request_key VARCHAR(128) NOT NULL, request_hash VARCHAR(64) NOT NULL,
 state VARCHAR NOT NULL CHECK(state IN ('REQUESTED','CAPTURING','UPLOADING','PUBLISH_QUEUED','PUBLISHING','SUCCEEDED','FAILED','CANCELLED')),
 capture_attempt_id VARCHAR, artifact_id VARCHAR, artifact_sha256 VARCHAR, artifact_size BIGINT,
 image_id VARCHAR, target_revision_id VARCHAR, failure_code VARCHAR, failure_detail VARCHAR(256),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(runtime_id, generation, request_key), UNIQUE(target_revision_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_active_save ON workspace_save_operations(runtime_id,generation)
 WHERE state IN ('REQUESTED','CAPTURING','UPLOADING','PUBLISH_QUEUED','PUBLISHING');

CREATE TABLE IF NOT EXISTS workspace_snapshot_artifacts (
 id VARCHAR PRIMARY KEY, operation_id VARCHAR NOT NULL UNIQUE REFERENCES workspace_save_operations(id),
 upload_attempt_id VARCHAR NOT NULL UNIQUE, storage_version VARCHAR NOT NULL UNIQUE, sha256 VARCHAR(64) NOT NULL,
 size BIGINT NOT NULL CHECK(size >= 0), platform VARCHAR NOT NULL, image_id VARCHAR NOT NULL,
 state VARCHAR NOT NULL CHECK(state IN ('STAGED','ACCEPTED','ABANDONED')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS workspace_training_submissions (
 id VARCHAR PRIMARY KEY, owner_user_id VARCHAR NOT NULL REFERENCES users(user_id), workspace_id VARCHAR NOT NULL,
 runtime_id VARCHAR NOT NULL, generation INTEGER NOT NULL, save_operation_id VARCHAR NOT NULL UNIQUE REFERENCES workspace_save_operations(id),
 request_key VARCHAR(128) NOT NULL, request_hash VARCHAR(64) NOT NULL, settings JSON NOT NULL,
 state VARCHAR NOT NULL CHECK(state IN ('SAVING','WAITING_FOR_REVISION','STOPPING_RUNTIME','WAITING_FOR_RELEASE','PREPARING_JOB','JOB_CREATED','FAILED')),
 job_id VARCHAR UNIQUE, failure_code VARCHAR, failure_detail VARCHAR(256), created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(runtime_id,generation,request_key)
);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_kind VARCHAR NOT NULL DEFAULT 'ARCHIVE';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_workspace_id VARCHAR;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_revision_id VARCHAR;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_image_digest_ref VARCHAR;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS executable_image_digest_ref VARCHAR;

DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='workspace_runtime_application_check') THEN
  ALTER TABLE interactive_runtimes ADD CONSTRAINT workspace_runtime_application_check CHECK (
   (access_service='terminal' AND application_protocol='terminal-stream-v1' AND NOT editor_capable) OR
   (access_service='workspace' AND application_protocol='workspace-stream-v1' AND editor_capable));
 END IF;
END $$;
