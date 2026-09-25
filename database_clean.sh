#!/usr/bin/env bash
# Clear all job-related state so the application can start fresh.
#
# CLEARED (Postgres, app-db):
#   jobs, resource_requests, worker_assignments (ALL kinds:
#   batch_training/vram_estimation/interactive_access),
#   interactive_workspaces, interactive_image_revisions, interactive_runtimes,
#   workspace_save_operations, workspace_snapshot_artifacts,
#   workspace_training_submissions.
# CLEARED (Redis, app-redis):
#   logs:*, build_logs:*, job_worker:*,
#   image_build_attempt_heartbeat:*, image_builder_heartbeat:*.
#
# KEPT (Postgres): users, workers, worker_credentials, cli_refresh_tokens.
# Gateway/management state (enrollments/endpoints/grants/sessions) lives in its
# own sqlite store and is untouched. Worker liveness keys in Redis are kept.
#
# FK-safe order: break the runtime<->assignment cycle first, then delete
# dependents (assignments, training submissions, artifacts, saves, runtimes),
# then null workspace->revision pointers, then revisions, workspaces, and
# finally jobs and resource_requests.
set -euo pipefail

POSTGRES_USER="${POSTGRES_USER:-admin}"
POSTGRES_DB="${POSTGRES_DB:-app}"
CONTAINER="${DB_CONTAINER:-app-db}"
REDIS_CONTAINER="${REDIS_CONTAINER:-app-redis}"

docker exec "$CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "
BEGIN;
UPDATE interactive_runtimes SET assignment_id=NULL;
DELETE FROM worker_assignments;
DELETE FROM workspace_training_submissions;
DELETE FROM workspace_snapshot_artifacts;
DELETE FROM workspace_save_operations;
DELETE FROM interactive_runtimes;
UPDATE interactive_workspaces SET current_revision_id=NULL, saved_revision_id=NULL;
DELETE FROM interactive_image_revisions;
DELETE FROM interactive_workspaces;
DELETE FROM jobs;
DELETE FROM resource_requests;
COMMIT;"

# Clear stale job/build log streams and scheduling leases in Redis.
# Worker liveness keys do not match these patterns and are kept.
for pattern in 'logs:*' 'build_logs:*' 'job_worker:*' 'image_build_attempt_heartbeat:*' 'image_builder_heartbeat:*'; do
  docker exec "$REDIS_CONTAINER" redis-cli --scan --pattern "$pattern" \
    | xargs -r docker exec "$REDIS_CONTAINER" redis-cli DEL > /dev/null
done

docker exec "$CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
"SELECT (SELECT count(*) FROM jobs) AS jobs, (SELECT count(*) FROM resource_requests) AS resource_requests, (SELECT count(*) FROM interactive_workspaces) AS workspaces, (SELECT count(*) FROM interactive_image_revisions) AS revisions, (SELECT count(*) FROM interactive_runtimes) AS runtimes, (SELECT count(*) FROM worker_assignments) AS assignments, (SELECT count(*) FROM workspace_save_operations) AS saves, (SELECT count(*) FROM workspace_snapshot_artifacts) AS artifacts, (SELECT count(*) FROM workspace_training_submissions) AS submissions;"
