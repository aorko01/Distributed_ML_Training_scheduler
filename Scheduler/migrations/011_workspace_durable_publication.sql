-- Additive durable save, SSH-safe publication, and direct training lineage.
ALTER TABLE interactive_image_revisions ADD COLUMN IF NOT EXISTS source_runtime_revision_id VARCHAR;
ALTER TABLE workspace_save_operations ADD COLUMN IF NOT EXISTS expected_saved_revision_id VARCHAR;
ALTER TABLE workspace_save_operations ADD COLUMN IF NOT EXISTS storage_version VARCHAR;
ALTER TABLE workspace_save_operations ADD COLUMN IF NOT EXISTS head_advanced BOOLEAN;
ALTER TABLE workspace_save_operations ADD COLUMN IF NOT EXISTS stop_after_save BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE workspace_training_submissions ADD COLUMN IF NOT EXISTS revision_id VARCHAR;
ALTER TABLE workspace_training_submissions ALTER COLUMN runtime_id DROP NOT NULL;
ALTER TABLE workspace_training_submissions ALTER COLUMN generation DROP NOT NULL;
ALTER TABLE workspace_training_submissions ALTER COLUMN save_operation_id DROP NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_direct_submission
 ON workspace_training_submissions(owner_user_id, workspace_id, revision_id, request_key)
 WHERE revision_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_workspace_save_dispatch
 ON workspace_save_operations(assignment_id, state, created_at);
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_workspace_saved_revision') THEN
  ALTER TABLE interactive_workspaces ADD CONSTRAINT fk_workspace_saved_revision
   FOREIGN KEY (saved_revision_id, id) REFERENCES interactive_image_revisions(id, workspace_id);
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_submission_revision') THEN
  ALTER TABLE workspace_training_submissions ADD CONSTRAINT fk_submission_revision
   FOREIGN KEY (revision_id) REFERENCES interactive_image_revisions(id);
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_save_expected_revision') THEN
  ALTER TABLE workspace_save_operations ADD CONSTRAINT fk_save_expected_revision
   FOREIGN KEY (expected_saved_revision_id, workspace_id) REFERENCES interactive_image_revisions(id, workspace_id);
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_snapshot_source_runtime_revision') THEN
  ALTER TABLE interactive_image_revisions ADD CONSTRAINT fk_snapshot_source_runtime_revision
   FOREIGN KEY (source_runtime_revision_id) REFERENCES interactive_image_revisions(id);
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_job_source_workspace') THEN
  ALTER TABLE jobs ADD CONSTRAINT fk_job_source_workspace
   FOREIGN KEY (source_workspace_id) REFERENCES interactive_workspaces(id);
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_job_source_revision') THEN
  ALTER TABLE jobs ADD CONSTRAINT fk_job_source_revision
   FOREIGN KEY (source_revision_id) REFERENCES interactive_image_revisions(id);
 END IF;
END $$;
