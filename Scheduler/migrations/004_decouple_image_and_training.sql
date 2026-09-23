-- Decouple image building from training (Add Workspace vs. Training page).
--
-- A workspace is now created and built without any entry command: the job
-- stops in the new IMAGE_READY state until training is submitted for it
-- (POST /jobs/{job_id}/training). Jobs that already carry a command keep going
-- straight to VRAM estimation, so this migration is safe for in-flight jobs.
--
-- Additive and idempotent; executed by app/db/database.py::run_migrations on
-- PostgreSQL (already wrapped in a transaction on PG 12+, which allows adding
-- an enum value).

-- The native enum type is created from the JobStatus python enum and is named
-- "jobstatus" by SQLAlchemy.
ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'IMAGE_READY';

-- ``command`` is only known once training is submitted.
ALTER TABLE jobs ALTER COLUMN command DROP NOT NULL;
