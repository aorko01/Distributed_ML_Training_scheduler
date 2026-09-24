-- Package-only workspace images (no ZIP archive).
--
-- A workspace submitted without a ZIP archive becomes source_kind='PACKAGES_ONLY'
-- with object_key NULL. Existing rows stay 'ARCHIVE'. ARCHIVE rows must keep an
-- object key; PACKAGES_ONLY rows must not have one. WORKSPACE_REVISION remains
-- unconstrained here so the separate durable-workspace flow is unaffected.
--
-- Additive and idempotent; executed by app/db/database.py::run_migrations on
-- PostgreSQL alongside the numbered *.sql migrations.

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_kind VARCHAR NOT NULL DEFAULT 'ARCHIVE';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_workspace_id VARCHAR;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_revision_id VARCHAR;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_image_digest_ref VARCHAR;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS executable_image_digest_ref VARCHAR;
UPDATE jobs SET source_kind = 'ARCHIVE' WHERE source_kind IS NULL OR source_kind = '';
ALTER TABLE jobs ALTER COLUMN object_key DROP NOT NULL;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='jobs_archive_requires_object_key') THEN
    ALTER TABLE jobs ADD CONSTRAINT jobs_archive_requires_object_key CHECK (
      source_kind != 'ARCHIVE' OR object_key IS NOT NULL
    );
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='jobs_packages_only_forbids_object_key') THEN
    ALTER TABLE jobs ADD CONSTRAINT jobs_packages_only_forbids_object_key CHECK (
      source_kind != 'PACKAGES_ONLY' OR object_key IS NULL
    );
  END IF;
END $$;
