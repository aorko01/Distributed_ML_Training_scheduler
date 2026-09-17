#!/usr/bin/env bash
set -euo pipefail

repository_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
scheduler_manifest="$repository_dir/Scheduler/docker-compose.yml"
interactive_manifest="$repository_dir/deploy/interactive/compose.yaml"
interactive_env_file="${INTERACTIVE_ENV_FILE:-/etc/distributed-ml/interactive.env}"
require_interactive="${REQUIRE_INTERACTIVE:-0}"
wait_timeout="${RESTART_WAIT_TIMEOUT:-120}"
interactive_enabled=0

for dependency in docker curl flock cksum python3; do
    command -v "$dependency" >/dev/null || { echo "Missing required command: $dependency" >&2; exit 1; }
done
[[ "$require_interactive" == 0 || "$require_interactive" == 1 ]] || { echo 'REQUIRE_INTERACTIVE must be 0 or 1.' >&2; exit 1; }
[[ "$wait_timeout" =~ ^[1-9][0-9]*$ ]] || { echo 'RESTART_WAIT_TIMEOUT must be a positive integer.' >&2; exit 1; }

# Serialize the complete post-push update for this checkout, including preflight.
lock_key="$(printf '%s' "$repository_dir" | cksum)"
lock_key="${lock_key%% *}"
exec 9>"${TMPDIR:-/tmp}/dml-restart-$lock_key.lock"
flock -w "$wait_timeout" 9

scheduler=(docker compose --project-name scheduler --project-directory "$repository_dir/Scheduler" --file "$scheduler_manifest")
interactive=(docker compose --project-name dml-interactive --project-directory "$repository_dir" --env-file "$interactive_env_file" --file "$interactive_manifest")

on_failure() {
    local failure_status=$?
    if [[ "$failure_status" == 0 ]]; then return; fi
    trap - ERR EXIT
    echo 'Deployment failed; services may be partially updated. Persistent state has been retained.' >&2
    "${scheduler[@]}" ps >&2 || true
    if [[ "$interactive_enabled" == 1 ]]; then
        "${interactive[@]}" ps >&2 || true
    fi
    exit "$failure_status"
}
trap on_failure EXIT

docker info >/dev/null
"${scheduler[@]}" config --quiet
# Inspect only storage metadata. Expanded environment is never printed.
scheduler_storage="$("${scheduler[@]}" config --format json | python3 -c 'import json,sys; c=json.load(sys.stdin); print(c["volumes"]["postgres-data"]["name"])')"
docker volume inspect "$scheduler_storage" --format '{{.Name}}' >/dev/null
scheduler_db_id="$("${scheduler[@]}" ps --quiet db)"
if [[ -n "$scheduler_db_id" ]]; then
    current_storage="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' "$scheduler_db_id")"
    [[ "$current_storage" == "$scheduler_storage" ]] || { echo 'Scheduler Postgres storage migration is required before deployment; no application has been changed.' >&2; exit 1; }
fi
if [[ -f "$interactive_manifest" ]]; then
    [[ -r "$interactive_env_file" ]] || { echo "Required interactive environment file is missing or unreadable: $interactive_env_file" >&2; exit 1; }
    interactive_enabled=1
    "${interactive[@]}" config --quiet
    docker network inspect dml-control --format '{{.Name}}' >/dev/null
else
    # Missing configuration is only allowed before interactive services exist.
    existing_interactive="$(docker ps -aq --filter label=com.docker.compose.project=dml-interactive)"
    if [[ "$require_interactive" == 1 || -n "$existing_interactive" ]]; then
        echo "Interactive deployment is required but its manifest is missing: $interactive_manifest" >&2
        exit 1
    fi
    echo 'Interactive services are not implemented/configured yet; updating Scheduler only.'
fi

# Finish all builds before interrupting any currently running application.
echo 'Building Scheduler image...'
"${scheduler[@]}" build api
if [[ "$interactive_enabled" == 1 ]]; then
    echo 'Building management and gateway images...'
    "${interactive[@]}" build management gateway
fi

# Keep database, Redis, and application volumes; never tear down the whole network.
echo 'Updating Scheduler...'
"${scheduler[@]}" up --detach --no-recreate --wait --wait-timeout "$wait_timeout" db redis
"${scheduler[@]}" up --detach --no-deps --force-recreate --wait --wait-timeout "$wait_timeout" api
scheduler_binding="$("${scheduler[@]}" port api 8000)"
scheduler_port="${scheduler_binding##*:}"
[[ "$scheduler_port" =~ ^[0-9]+$ ]] || { echo 'Unable to resolve Scheduler published port.' >&2; exit 1; }
scheduler_deadline=$((SECONDS + wait_timeout))
until curl --fail --silent --max-time 5 "http://127.0.0.1:$scheduler_port/openapi.json" >/dev/null; do
    if (( SECONDS >= scheduler_deadline )); then
        echo 'Scheduler HTTP readiness timed out.' >&2
        exit 1
    fi
    sleep 2
done

if [[ "$interactive_enabled" == 1 ]]; then
    echo 'Updating interactive services, preserving management and Tailscale state...'
    # Brief maintenance window: no old management process may write during migration.
    "${interactive[@]}" stop --timeout 30 gateway management
    "${interactive[@]}" run --rm --no-deps migrate
    "${interactive[@]}" up --detach --no-deps --force-recreate --wait --wait-timeout "$wait_timeout" management
    # Do not force-recreate the sidecar on every push. Changed images/config may
    # recreate it; the gateway is recreated afterward to use its current namespace.
    "${interactive[@]}" up --detach --no-deps --wait --wait-timeout "$wait_timeout" tailscale
    "${interactive[@]}" run --rm --no-deps bootstrap
    "${interactive[@]}" up --detach --no-deps --force-recreate --wait --wait-timeout "$wait_timeout" gateway
    "${interactive[@]}" ps
    echo 'Management and gateway passed readiness checks.'
fi

"${scheduler[@]}" ps
echo 'Scheduler passed HTTP readiness; configured service updates completed.'
