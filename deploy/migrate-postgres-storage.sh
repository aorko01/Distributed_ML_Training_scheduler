#!/usr/bin/env bash
# One-time, explicitly invoked cutover after CI and a protected backup are ready.
# Retains the original anonymous volume; ordinary restart.sh never migrates it.
set -euo pipefail
umask 077
repository_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
backup_dir="${POSTGRES_BACKUP_DIR:?set a protected absolute backup directory outside version control}"
[[ "$backup_dir" == /* ]] || { echo 'Backup directory must be absolute.' >&2; exit 1; }
target_volume="${POSTGRES_DATA_VOLUME:-scheduler-postgres-data}"
scheduler=(docker compose --project-name scheduler --project-directory "$repository_dir/Scheduler" --file "$repository_dir/Scheduler/docker-compose.yml")
lock_key="$(printf '%s' "$repository_dir" | cksum)"
exec 9>"${TMPDIR:-/tmp}/dml-restart-${lock_key%% *}.lock"
flock -w 120 9
db_id="$("${scheduler[@]}" ps --quiet db)"
[[ -n "$db_id" ]] || { echo 'Existing Postgres container is required for migration.' >&2; exit 1; }
source_volume="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' "$db_id")"
[[ -n "$source_volume" && "$source_volume" != "$target_volume" ]] || { echo 'Storage is already managed or cannot be safely identified.' >&2; exit 1; }
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
backup_file="$backup_dir/postgres-before-storage-migration-$(date -u +%Y%m%dT%H%M%SZ).sql"
docker exec "$db_id" sh -c 'exec pg_dumpall -U "$POSTGRES_USER"' >"$backup_file"
[[ -s "$backup_file" ]] || { echo 'Postgres backup is empty; aborting before stop.' >&2; exit 1; }
docker volume create "$target_volume" >/dev/null
# Require empty target before entering maintenance; never overwrite any data.
docker run --rm --volume "$target_volume:/destination:ro" postgres:15 sh -c 'test -z "$(ls -A /destination)"'
echo 'Backup complete; stopping API and Postgres for consistent storage copy.'
"${scheduler[@]}" stop --timeout 30 api db
docker run --rm --volume "$source_volume:/source:ro" --volume "$target_volume:/destination" postgres:15 sh -c 'test -z "$(ls -A /destination)" && cp -a /source/. /destination/'
"${scheduler[@]}" up --detach --no-deps --force-recreate --wait --wait-timeout 120 db
echo 'Managed Postgres storage passed readiness. Original volume and backup retained; invoke restart.sh to update applications.'
