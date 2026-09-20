#!/usr/bin/env bash
# Clear all interactive state (workspaces/revisions/runtimes/assignments/saves).
# Batch jobs (jobs table), workers and resource_requests are left untouched.
# FK-safe order: null runtime->assignment first, then assignments, runtimes,
# then null workspace->revision, then revisions, workspaces.
set -euo pipefail

POSTGRES_USER="${POSTGRES_USER:-admin}"
POSTGRES_DB="${POSTGRES_DB:-app}"
CONTAINER="${DB_CONTAINER:-app-db}"

docker exec "$CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "
BEGIN;
UPDATE interactive_runtimes SET assignment_id=NULL;
DELETE FROM worker_assignments WHERE kind='interactive_access';
DELETE FROM workspace_training_submissions;
DELETE FROM workspace_save_operations;
DELETE FROM workspace_snapshot_artifacts;
DELETE FROM interactive_runtimes;
UPDATE interactive_workspaces SET current_revision_id=NULL, saved_revision_id=NULL;
DELETE FROM interactive_image_revisions;
DELETE FROM interactive_workspaces;
COMMIT;"

docker exec "$CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
"SELECT (SELECT count(*) FROM interactive_workspaces) AS workspaces, (SELECT count(*) FROM interactive_image_revisions) AS revisions, (SELECT count(*) FROM interactive_runtimes) AS runtimes, (SELECT count(*) FROM worker_assignments WHERE kind='interactive_access') AS assignments;"
