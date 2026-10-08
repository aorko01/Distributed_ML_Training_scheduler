#!/usr/bin/env bash
set -euo pipefail

repository_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
scheduler_manifest="$repository_dir/Scheduler/docker-compose.yml"
scheduler_runtime_manifest="$repository_dir/Scheduler/compose.runtime.yaml"
scheduler_interactive_manifest="$repository_dir/Scheduler/compose.interactive.yaml"
object_store_manifest="$repository_dir/Object_store/docker-compose.yml"
user_ui_dir="$repository_dir/UI/User"
admin_ui_dir="$repository_dir/UI/Admin"
user_www_dir="${USER_WWW_DIR:-/var/www/distributeml}"
admin_www_dir="${ADMIN_WWW_DIR:-/var/www/adminui}"
interactive_manifest="$repository_dir/deploy/interactive/compose.yaml"
interactive_env_file="${INTERACTIVE_ENV_FILE:-/etc/distributed-ml/interactive.env}"
require_interactive="${REQUIRE_INTERACTIVE:-0}"
wait_timeout="${RESTART_WAIT_TIMEOUT:-120}"
# This host serves Scheduler + Object Store + UI, so all three are rebuilt
# by default. Set any SKIP_*=1 to leave that part untouched.
skip_git_sync="${SKIP_GIT_SYNC:-0}"
skip_scheduler="${SKIP_SCHEDULER:-0}"
skip_object_store="${SKIP_OBJECT_STORE:-0}"
skip_ui="${SKIP_UI:-0}"
git_remote="${GIT_REMOTE:-origin}"
git_branch="${GIT_BRANCH:-main}"
# UI toolchains (Vite 8) require Node >= 20 but this host ships Node 18, so the
# UIs are built inside a pinned Node container instead of the host toolchain.
# The container runs as the invoking user so checkout files stay owned by them
# even when this script itself runs under sudo.
ui_node_image="${UI_NODE_IMAGE:-node:22-bookworm-slim}"
build_uid="${SUDO_UID:-$(id -u)}"
build_gid="${SUDO_GID:-$(id -g)}"
object_store_enabled=0
ui_enabled=0
interactive_enabled=0

# UI builds run inside a container (host Node 18 < required 20).
build_ui() {
    local ui_dir="$1" ui_label="$2"
    # Earlier sudo runs may have left root-owned node_modules/dist behind,
    # which makes the container build fail with EACCES. Repair while root.
    if [[ "$EUID" -eq 0 ]]; then
        for generated in "$ui_dir/node_modules" "$ui_dir/dist"; do
            [[ -e "$generated" ]] || continue
            chown -R "$build_uid:$build_gid" "$generated"
        done
    fi
    echo "Building $ui_label (container $ui_node_image)..."
    docker image inspect "$ui_node_image" >/dev/null 2>&1 || docker pull "$ui_node_image"
    docker run --rm --user "$build_uid:$build_gid" \
        --volume "$ui_dir:/app" --workdir /app \
        --env HOME=/tmp --env npm_config_cache=/tmp/npm-cache \
        "$ui_node_image" \
        sh -c '(npm ci --no-audit --no-fund || npm install --no-audit --no-fund) && npm run build'
}

for dependency in docker curl flock cksum python3 git sudo; do
    command -v "$dependency" >/dev/null || { echo "Missing required command: $dependency" >&2; exit 1; }
done
[[ "$require_interactive" == 0 || "$require_interactive" == 1 ]] || { echo 'REQUIRE_INTERACTIVE must be 0 or 1.' >&2; exit 1; }
[[ "$wait_timeout" =~ ^[1-9][0-9]*$ ]] || { echo 'RESTART_WAIT_TIMEOUT must be a positive integer.' >&2; exit 1; }
for toggle in skip_git_sync skip_scheduler skip_object_store skip_ui; do
    [[ "${!toggle}" == 0 || "${!toggle}" == 1 ]] || { echo "${toggle} must be 0 or 1." >&2; exit 1; }
done

# Scheduler runtime overlays (worker auth + interactive wiring) are layered onto
# the base Compose file when present in this checkout. Defaults match the
# Scheduler VM layout; override via environment for a non-standard host. Each
# referenced host path must already be a regular file: Docker would otherwise
# create a directory at a missing bind source and the API would fail to start.
export WORKER_CREDENTIALS_HOST_FILE="${WORKER_CREDENTIALS_HOST_FILE:-/etc/dml/worker-credentials.json}"
export CONTROLLER_SECRET_HOST_FILE="${CONTROLLER_SECRET_HOST_FILE:-/etc/dml/controller.secret}"
export MANAGEMENT_CA_HOST_FILE="${MANAGEMENT_CA_HOST_FILE:-/etc/ssl/certs/ca-certificates.crt}"
export INTERACTIVE_BUILDER_SECRET_HOST_FILE="${INTERACTIVE_BUILDER_SECRET_HOST_FILE:-/etc/dml/secrets/interactive-builder}"

# Host secrets are root-owned (0600 under drwx------); a non-root caller sees
# them as missing. Fail with a sudo hint instead of a bare "missing" message.
require_host_file() {
    local secret_path="$1"
    if [[ -f "$secret_path" && -r "$secret_path" ]]; then
        return 0
    fi
    if [[ "$EUID" -ne 0 ]]; then
        echo "Required Scheduler host secret is missing or not a regular file: $secret_path (not readable as uid=$EUID; secrets are root-owned — re-run with: sudo -n env REQUIRE_INTERACTIVE=1 bash \"$repository_dir/restart.sh\")" >&2
    else
        echo "Required Scheduler host secret is missing or not a regular file: $secret_path" >&2
    fi
    exit 1
}

scheduler=(docker compose --project-name scheduler --project-directory "$repository_dir/Scheduler" --file "$scheduler_manifest")
if [[ -f "$scheduler_runtime_manifest" ]]; then
    scheduler+=(--file "$scheduler_runtime_manifest")
    for secret_path in "$WORKER_CREDENTIALS_HOST_FILE" "$CONTROLLER_SECRET_HOST_FILE" "$MANAGEMENT_CA_HOST_FILE"; do
        require_host_file "$secret_path"
    done
fi
if [[ -f "$scheduler_interactive_manifest" ]]; then
    scheduler+=(--file "$scheduler_interactive_manifest")
    require_host_file "$INTERACTIVE_BUILDER_SECRET_HOST_FILE"
fi

object_store=(docker compose --project-name object_store --project-directory "$repository_dir/Object_store" --file "$object_store_manifest")

# Serialize the complete post-push update for this checkout, including source sync.
lock_key="$(printf '%s' "$repository_dir" | cksum)"
lock_key="${lock_key%% *}"
exec 9>"${TMPDIR:-/tmp}/dml-restart-$lock_key.lock"
flock -w "$wait_timeout" 9

interactive=(docker compose --project-name dml-interactive --project-directory "$repository_dir" --env-file "$interactive_env_file" --file "$interactive_manifest")

on_failure() {
    local failure_status=$?
    if [[ "$failure_status" == 0 ]]; then return; fi
    trap - ERR EXIT
    echo 'Deployment failed; services may be partially updated. Persistent state has been retained.' >&2
    if [[ "$skip_scheduler" != 1 ]]; then
        "${scheduler[@]}" ps >&2 || true
    fi
    if [[ "$object_store_enabled" == 1 ]]; then
        "${object_store[@]}" ps >&2 || true
    fi
    if [[ "$interactive_enabled" == 1 ]]; then
        "${interactive[@]}" ps >&2 || true
    fi
    exit "$failure_status"
}
trap on_failure EXIT

# ---- Source sync: fast-forward to the requested branch, falling back to the
# latest remote HEAD when a plain pull cannot proceed (e.g. local drift from
# hotfixes or a diverged checkout). Local .env / secret files are git-ignored
# and therefore untouched by either path.
if [[ "$skip_git_sync" == 1 ]]; then
    echo 'Skipping source sync (SKIP_GIT_SYNC=1).'
else
    echo "Syncing source from $git_remote/$git_branch..."
    git -C "$repository_dir" fetch "$git_remote" "$git_branch"
    if git -C "$repository_dir" pull --ff-only "$git_remote" "$git_branch"; then
        echo "Source fast-forwarded to: $(git -C "$repository_dir" rev-parse --short HEAD) $(git -C "$repository_dir" log -1 --format=%s)"
    else
        echo "git pull failed; resetting working tree to latest $git_remote/$git_branch HEAD..." >&2
        git -C "$repository_dir" reset --hard "$git_remote/$git_branch"
        echo "Source now at: $(git -C "$repository_dir" rev-parse --short HEAD) $(git -C "$repository_dir" log -1 --format=%s)"
    fi
fi

docker info >/dev/null
if [[ "$skip_scheduler" != 1 ]]; then
    "${scheduler[@]}" config --quiet
    # Inspect only storage metadata. Expanded environment is never printed.
    scheduler_storage="$("${scheduler[@]}" config --format json | python3 -c 'import json,sys; c=json.load(sys.stdin); print(c["volumes"]["postgres-data"]["name"])')"
    docker volume inspect "$scheduler_storage" --format '{{.Name}}' >/dev/null
    scheduler_db_id="$("${scheduler[@]}" ps --quiet db)"
    if [[ -n "$scheduler_db_id" ]]; then
        current_storage="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' "$scheduler_db_id")"
        [[ "$current_storage" == "$scheduler_storage" ]] || { echo 'Scheduler Postgres storage migration is required before deployment; no application has been changed.' >&2; exit 1; }
    fi
fi
if [[ "$skip_object_store" != 1 ]]; then
    [[ -f "$object_store_manifest" ]] || { echo "Object Store manifest is missing: $object_store_manifest" >&2; exit 1; }
    [[ -f "$repository_dir/Object_store/.env" ]] || { echo 'Missing Object_store/.env (host-local, never in git)' >&2; exit 1; }
    object_store_enabled=1
    "${object_store[@]}" config --quiet
fi
if [[ "$skip_ui" != 1 ]]; then
    [[ -f "$user_ui_dir/package.json" ]] || { echo "User UI is missing: $user_ui_dir/package.json" >&2; exit 1; }
    [[ -f "$admin_ui_dir/package.json" ]] || { echo "Admin UI is missing: $admin_ui_dir/package.json" >&2; exit 1; }
    ui_enabled=1
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
    echo 'Interactive services are not implemented/configured yet; updating Scheduler/Object Store/UI only.'
fi

# Finish all builds before interrupting any currently running application.
if [[ "$skip_scheduler" != 1 ]]; then
    echo 'Building Scheduler image...'
    "${scheduler[@]}" build api
fi
if [[ "$object_store_enabled" == 1 ]]; then
    echo 'Building Object Store image...'
    "${object_store[@]}" build
fi
if [[ "$interactive_enabled" == 1 ]]; then
    echo 'Building management and gateway images...'
    "${interactive[@]}" build management gateway
fi

# Keep database, Redis, and application volumes; never tear down the whole network.
if [[ "$skip_scheduler" != 1 ]]; then
    echo 'Updating Scheduler...'
    "${scheduler[@]}" up --detach --no-recreate --wait --wait-timeout "$wait_timeout" db redis
    # Retained containers may predate the newly configured Docker healthchecks.
    # Probe the actual services before replacing API, including that first cutover.
    infrastructure_deadline=$((SECONDS + wait_timeout))
    # Expand Postgres variables inside the container, not in the deployment shell.
    # shellcheck disable=SC2016
    until "${scheduler[@]}" exec -T db sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null &&
          "${scheduler[@]}" exec -T redis redis-cli ping >/dev/null; do
        if (( SECONDS >= infrastructure_deadline )); then
            echo 'Scheduler database/Redis readiness timed out.' >&2
            exit 1
        fi
        sleep 2
    done
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
fi

if [[ "$object_store_enabled" == 1 ]]; then
    echo 'Updating Object Store (preserving object_data volume)...'
    "${object_store[@]}" up --detach --wait --wait-timeout "$wait_timeout"
    object_deadline=$((SECONDS + wait_timeout))
    until curl --fail --silent --max-time 5 'http://127.0.0.1:8010/health' >/dev/null; do
        if (( SECONDS >= object_deadline )); then
            echo 'Object Store HTTP readiness timed out.' >&2
            exit 1
        fi
        sleep 2
    done
    echo 'Object Store passed health check.'
fi

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

if [[ "$ui_enabled" == 1 ]]; then
    build_ui "$user_ui_dir" 'User UI'
    build_ui "$admin_ui_dir" 'Admin UI'
    echo "Deploying User UI to $user_www_dir/..."
    sudo rm -rf "${user_www_dir:?}/"*
    sudo mkdir -p "$user_www_dir"
    sudo cp -r "$user_ui_dir/dist/." "$user_www_dir/"
    echo "Deploying Admin UI to $admin_www_dir/..."
    sudo rm -rf "${admin_www_dir:?}/"*
    sudo mkdir -p "$admin_www_dir"
    sudo cp -r "$admin_ui_dir/dist/." "$admin_www_dir/"
    echo 'Reloading Caddy...'
    # reload is graceful; if Caddy is stopped or the notify unit is stale, a plain
    # reload can fail (or be a silent no-op on a dead unit), leaving the freshly
    # copied bundle unreachable. Fall back to a full restart so the new bundle is
    # actually live, then confirm the unit is active.
    sudo systemctl reload caddy || sudo systemctl restart caddy
    sudo systemctl is-active --quiet caddy
    echo 'UIs are live.'
fi

if [[ "$skip_scheduler" != 1 ]]; then
    "${scheduler[@]}" ps
    echo 'Scheduler passed HTTP readiness; configured service updates completed.'
fi
if [[ "$object_store_enabled" == 1 ]]; then
    "${object_store[@]}" ps
fi
echo 'Restart completed.'
