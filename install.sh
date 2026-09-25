#!/usr/bin/env bash
set -Eeuo pipefail

# One-command Ubuntu installer for the persistent Worker service and its
# separate Electron monitoring console. Deployment-specific, non-secret values
# match the current production environment; secrets are never written here.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/Worker/.env"

log() { printf '[dml-installer] %s\n' "$*"; }
die() { printf '[dml-installer] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Linux" ]] || die "This application supports Linux only."
[[ -f "$SCRIPT_DIR/Worker/join_worker.sh" ]] || die "Run this script from the complete repository checkout."

if [[ ! -f "$ENV_FILE" ]]; then
    log "Creating Worker/.env with the current deployment defaults."
    umask 077
    cat > "$ENV_FILE" <<'EOF'
SCHEDULER_URL=https://scheduler.zulfiker.xyz
OBJECT_STORE_URL=https://object.zulfiker.xyz
OBJECT_OUTPUT_BUCKET=outputs
WORKER_SERVICE_CREDENTIAL_FILE=/etc/dml/worker-service.secret
WORKER_ID_FILE=/var/lib/dml-worker/worker-id
WORKER_STATE_DIR=/var/lib/dml-worker
WORKER_API_HOST=127.0.0.1
WORKER_API_PORT=8600
WORKER_ASSIGNMENT_LEASE_SECONDS=45
WORKER_SCHEDULER_CLAIM_TIMEOUT_SECONDS=30
MAX_CONCURRENT_JOBS=2
INTERACTIVE_WORKER_ENABLED=1
INTERACTIVE_REGISTRY_PREFIXES=docker.io/aorko123
INTERACTIVE_REGISTRY_USERNAME=aorko123
INTERACTIVE_REGISTRY_CREDENTIAL_FILE=/etc/dml/registry-pull.json
INTERACTIVE_ACCESS_IMAGE=docker.io/aorko123/access@sha256:f7884b44f15d2c29daff40b3f74a7df1d3e2da27348ff3f44c52d328f5d23065
INTERACTIVE_PREFLIGHT_IMAGE=docker.io/aorko123/quota-fixture@sha256:b0b2526e7fe571f62b3077ff32cf0371182348f400714bcc0c561bda70dd81c3
INTERACTIVE_HEADSCALE_CA_FILE=
INTERACTIVE_REQUIRE_IDLE_GPU=0
INTERACTIVE_GPU_BASELINE_MB=64
INTERACTIVE_GPU_BUSY_PERCENT=1
INTERACTIVE_PULL_TIMEOUT_SECONDS=1800
INTERACTIVE_CLEANUP_ON_FAILURE=0
INTERACTIVE_MAX_DURATION_SECONDS=6000
INTERACTIVE_ALLOW_DEVELOPER_MODE=1
INTERACTIVE_ALLOW_INTERNET=1
INTERACTIVE_ALLOW_SSH=1
INTERACTIVE_SSH_MAX_DURATION_SECONDS=14400
INTERACTIVE_SSH_CAPACITY=8
CONTAINER_AS_ROOT=0
EOF
    chmod 0600 "$ENV_FILE"
else
    log "Using the existing Worker/.env without overwriting it."
fi

log "Docker's data root, host identity, IP, CPU, RAM, disk, and GPUs are detected automatically."
log "A registry token is requested only when an existing Docker login cannot be imported."
exec bash "$SCRIPT_DIR/Worker/join_worker.sh" "$@"
