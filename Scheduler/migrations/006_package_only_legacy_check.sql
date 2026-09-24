-- Allow PACKAGES_ONLY workspaces under the legacy source check.
--
-- Deployments that ran the durable-workspace flow carry a
-- `ck_jobs_source_archive` CHECK constraint which only permits ARCHIVE and
-- WORKSPACE_REVISION rows, so no-archive (PACKAGES_ONLY) inserts fail with a
-- CheckViolation even after 005 made `jobs.object_key` nullable. Replace it
-- with an equivalent check that additionally permits PACKAGES_ONLY rows
-- without an object key. The WORKSPACE_REVISION requirements are preserved.
--
-- Additive and idempotent; executed by app/db/database.py::run_migrations on
-- PostgreSQL alongside the numbered *.sql migrations.

ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_source_archive;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_jobs_source_archive') THEN
    ALTER TABLE jobs ADD CONSTRAINT ck_jobs_source_archive CHECK (
      (source_kind = 'ARCHIVE' AND object_key IS NOT NULL)
      OR (source_kind = 'PACKAGES_ONLY' AND object_key IS NULL)
      OR (source_kind = 'WORKSPACE_REVISION' AND source_revision_id IS NOT NULL AND source_image_digest_ref IS NOT NULL)
    );
  END IF;
END $$;
