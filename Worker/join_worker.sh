#!/usr/bin/env bash
set -Eeuo pipefail

# Provision this checkout as a production, interactive-capable Worker.  The only
# control-plane operation deliberately left to an operator is adding the
# generated Worker UUID/secret pair to the Scheduler credential map.

umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SOURCE_ENV="$SCRIPT_DIR/.env"
LIVE_ENV="/etc/dml/worker.env"
INSTALL_ROOT="/opt/dml"
UI_SOURCE="$REPO_ROOT/UI/Worker"
UI_INSTALL_ROOT="$INSTALL_ROOT/WorkerUI"
STATE_DEFAULT="/var/lib/dml-worker"
SERVICE_NAME="dml-worker.service"
GUARD_NAME="dml-worker-lease-guard.service"

CHECK_ONLY=0
REGISTRATION_CONFIRMED=0
NO_START=0
SHOW_REGISTRATION=0
SHOW_RUNTIME_ENV=0
EXISTING_JOINED=0

log() { printf '[dml-worker] %s\n' "$*"; }
warn() { printf '[dml-worker] WARNING: %s\n' "$*" >&2; }
die() { printf '[dml-worker] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: bash Worker/join_worker.sh [options]

Install this checkout as an interactive Worker systemd service.

Options:
  --check              Validate this host and Worker/.env without changing it.
  --registered         The existing UUID/secret is already on the Scheduler.
  --no-start           Install the units, but leave them disabled and stopped.
  --show-registration  Print the existing Scheduler credential-map entry.
  --show-runtime-env   Show the non-secret interactive settings seen by systemd.
  -h, --help           Show this help.

On a new host the script generates the Worker UUID and secret, prints the exact
JSON entry to add on the Scheduler, and waits for confirmation before starting.
It never edits or connects to the Scheduler host itself.
EOF
}

while (($#)); do
    case "$1" in
        --check) CHECK_ONLY=1 ;;
        --registered) REGISTRATION_CONFIRMED=1 ;;
        --no-start) NO_START=1 ;;
        --show-registration) SHOW_REGISTRATION=1 ;;
        --show-runtime-env) SHOW_RUNTIME_ENV=1 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1 (use --help)" ;;
    esac
    shift
done

[[ -f "$SOURCE_ENV" ]] || die "Missing $SOURCE_ENV. Place the configured .env in Worker/ first."
[[ -d "$REPO_ROOT/Access_Container" ]] || die "Missing $REPO_ROOT/Access_Container; a Worker-only copy is not runnable."
[[ -f "$REPO_ROOT/deploy/interactive/worker/dml-worker.service" ]] || die "Missing Worker systemd unit files under deploy/interactive/worker/."
[[ -f "$UI_SOURCE/package-lock.json" ]] || die "Missing $UI_SOURCE; the integrated Worker UI is required."

# Read dotenv values without sourcing shell code. This intentionally supports
# the simple KEY=value, quoted-value format accepted by the supplied example.
env_get_from() {
    local file="$1" key="$2"
    python3 - "$file" "$key" <<'PY'
import re
import sys

path, wanted = sys.argv[1:]
value = None
with open(path, encoding="utf-8") as handle:
    for raw in handle:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, candidate = line.split("=", 1)
        if key.strip() != wanted:
            continue
        candidate = candidate.strip()
        if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in "\"'":
            candidate = candidate[1:-1]
        value = candidate
if value is not None:
    if "\x00" in value or "\n" in value or "\r" in value:
        raise SystemExit("invalid multiline environment value")
    print(value, end="")
PY
}

env_get() { env_get_from "$LIVE_ENV" "$1"; }
source_env_get() { env_get_from "$SOURCE_ENV" "$1"; }

require_absolute_path() {
    local key="$1" value="$2"
    [[ "$value" == /* ]] || die "$key must be an absolute path (got: ${value:-<empty>})."
}

validate_env_file() {
    local file="$1" get_cmd="$2"
    local scheduler interactive prefixes access_image preflight_image api_host api_port
    scheduler="$($get_cmd SCHEDULER_URL)"
    interactive="$($get_cmd INTERACTIVE_WORKER_ENABLED)"
    prefixes="$($get_cmd INTERACTIVE_REGISTRY_PREFIXES)"
    access_image="$($get_cmd INTERACTIVE_ACCESS_IMAGE)"
    preflight_image="$($get_cmd INTERACTIVE_PREFLIGHT_IMAGE)"
    api_host="$($get_cmd WORKER_API_HOST)"
    api_port="$($get_cmd WORKER_API_PORT)"

    [[ "$scheduler" == https://* ]] || die "SCHEDULER_URL must be a routable https:// URL in $file."
    [[ "$interactive" == "1" ]] || die "INTERACTIVE_WORKER_ENABLED=1 is required for an interactive Worker."
    [[ -z "$api_host" || "$api_host" == "127.0.0.1" || "$api_host" == "localhost" ]] ||
        die "WORKER_API_HOST must stay loopback-only for the integrated desktop UI."
    if [[ -n "$api_port" ]]; then
        [[ "$api_port" =~ ^[0-9]+$ ]] && ((api_port >= 1 && api_port <= 65535)) ||
            die "WORKER_API_PORT must be an integer from 1 to 65535."
    fi
    [[ -n "$prefixes" ]] || die "INTERACTIVE_REGISTRY_PREFIXES is required in $file."
    local require_idle_gpu allow_ssh allow_internet allow_developer ssh_max ssh_capacity
    require_idle_gpu="$($get_cmd INTERACTIVE_REQUIRE_IDLE_GPU)"
    [[ -z "$require_idle_gpu" || "$require_idle_gpu" == "0" || "$require_idle_gpu" == "1" ]] ||
        die "INTERACTIVE_REQUIRE_IDLE_GPU must be 0 or 1 in $file."
    for key in INTERACTIVE_ALLOW_SSH INTERACTIVE_ALLOW_INTERNET INTERACTIVE_ALLOW_DEVELOPER_MODE; do
        value="$($get_cmd "$key")"
        [[ -z "$value" || "$value" == "0" || "$value" == "1" ]] ||
            die "$key must be 0 or 1 in $file."
    done
    ssh_max="$($get_cmd INTERACTIVE_SSH_MAX_DURATION_SECONDS)"
    if [[ -n "$ssh_max" ]]; then
        [[ "$ssh_max" =~ ^(0|[1-9][0-9]*)$ ]] && ((ssh_max >= 0 && ssh_max <= 86400)) ||
            die "INTERACTIVE_SSH_MAX_DURATION_SECONDS must be 0..86400 in $file."
    fi
    ssh_capacity="$($get_cmd INTERACTIVE_SSH_CAPACITY)"
    if [[ -n "$ssh_capacity" ]]; then
        [[ "$ssh_capacity" =~ ^[0-9]+$ ]] && ((ssh_capacity >= 1 && ssh_capacity <= 32)) ||
            die "INTERACTIVE_SSH_CAPACITY must be 1..32 in $file."
    fi
    [[ "$access_image" =~ ^[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}$ ]] ||
        die "INTERACTIVE_ACCESS_IMAGE must be an immutable @sha256 digest in $file."
    [[ "$preflight_image" =~ ^[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}$ ]] ||
        die "INTERACTIVE_PREFLIGHT_IMAGE must be an immutable @sha256 digest in $file."
    [[ "$access_image" != *REPLACE* && "$preflight_image" != *REPLACE* ]] ||
        die "Replace the example interactive image digests in $file."

    local image prefix allowed
    for image in "$access_image" "$preflight_image"; do
        allowed=0
        IFS=',' read -r -a prefix_items <<<"$prefixes"
        for prefix in "${prefix_items[@]}"; do
            prefix="${prefix#${prefix%%[![:space:]]*}}"
            prefix="${prefix%${prefix##*[![:space:]]}}"
            prefix="${prefix%/}"
            if [[ -n "$prefix" && "$image" == "$prefix/"* ]]; then
                allowed=1
                break
            fi
        done
        ((allowed == 1)) || die "Interactive image $image is outside INTERACTIVE_REGISTRY_PREFIXES."
    done

    local key value
    for key in WORKER_SERVICE_CREDENTIAL_FILE WORKER_ID_FILE WORKER_STATE_DIR; do
        value="$($get_cmd "$key")"
        if [[ -n "$value" ]]; then
            require_absolute_path "$key" "$value"
        fi
    done
    for key in WORKER_SCHEDULER_CA_FILE INTERACTIVE_HEADSCALE_CA_FILE INTERACTIVE_REGISTRY_CREDENTIAL_FILE; do
        value="$($get_cmd "$key")"
        if [[ -n "$value" ]]; then
            require_absolute_path "$key" "$value"
        fi
    done

    if grep -Eq '^(DOCKER_HUB_PASSWORD|INTERACTIVE_REGISTRY_PASSWORD)=' "$file"; then
        die "Do not store registry passwords in $file; the installer uses a protected credential file."
    fi
}

host_checks() {
    [[ "$(uname -s)" == "Linux" ]] || die "Production interactive Workers require Linux."
    command -v python3 >/dev/null || die "python3 is not installed."
    command -v docker >/dev/null || die "Docker Engine is not installed. Run without --check to install it."
    docker info >/dev/null 2>&1 || die "Docker Engine is not running or is unavailable."
    command -v nvidia-smi >/dev/null || die "The NVIDIA driver is missing; install a supported host driver and reboot first."
    nvidia-smi -L >/dev/null 2>&1 || die "The NVIDIA driver cannot enumerate a GPU."

    python3 - <<'PY' || die "Docker is missing the configured NVIDIA runtime."
import json
import subprocess
info = json.loads(subprocess.check_output(["docker", "info", "--format", "{{json .}}"], text=True))
raise SystemExit(0 if "nvidia" in info.get("Runtimes", {}) else 1)
PY
}

if ((CHECK_ONLY)); then
    validate_env_file "$SOURCE_ENV" source_env_get
    host_checks
    log "Worker/.env, Docker, the NVIDIA runtime, and the host GPU passed static checks."
    log "A full install also pulls the pinned images and performs the disposable storage-quota preflight."
    exit 0
fi

if ((EUID != 0)); then
    command -v sudo >/dev/null || die "Run this installer as root (sudo is not installed)."
    sudo_args=()
    if ((REGISTRATION_CONFIRMED)); then sudo_args+=(--registered); fi
    if ((NO_START)); then sudo_args+=(--no-start); fi
    if ((SHOW_REGISTRATION)); then sudo_args+=(--show-registration); fi
    exec sudo -- "$BASH_SOURCE" "${sudo_args[@]}"
fi

[[ -r /etc/os-release ]] || die "Cannot identify this operating system."
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || die "The supported production Worker host is Ubuntu (found ${ID:-unknown})."

install_base_packages() {
    log "Installing host packages..."
    # This installer runs with umask 077 to protect Worker credentials. APT,
    # however, deliberately reads repositories as the unprivileged `_apt`
    # user. Repair files left by an interrupted older installer before the
    # first apt-get update, otherwise NodeSource is ignored or its signature
    # cannot be verified.
    [[ ! -e /etc/apt/sources.list.d/nodesource.list ]] ||
        chmod 0644 /etc/apt/sources.list.d/nodesource.list
    [[ ! -e /etc/apt/keyrings/nodesource.gpg ]] ||
        chmod 0644 /etc/apt/keyrings/nodesource.gpg
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg openssl python3 python3-pip python3-venv rsync \
        libnss3 libxss1 libgbm1 libnotify4 libxtst6 libatspi2.0-0 \
        libuuid1 libsecret-1-0 xdg-utils

    if ! dpkg-query -W -f='${db:Status-Status}' libgtk-3-0t64 2>/dev/null | grep -q '^installed$' \
        && ! dpkg-query -W -f='${db:Status-Status}' libgtk-3-0 2>/dev/null | grep -q '^installed$'; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libgtk-3-0t64 \
            || DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libgtk-3-0
    fi

    # Electron needs ALSA even on hosts that do not have speakers. Ubuntu
    # 24.04 renamed the package while 22.04 retains the original name.
    if ! dpkg-query -W -f='${db:Status-Status}' libasound2t64 2>/dev/null | grep -q '^installed$' \
        && ! dpkg-query -W -f='${db:Status-Status}' libasound2 2>/dev/null | grep -q '^installed$'; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libasound2t64 \
            || DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libasound2
    fi
}

install_node_build_toolchain() {
    local major
    major=0
    if command -v node >/dev/null 2>&1; then
        major="$(node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || printf 0)"
    fi
    if [[ "$major" =~ ^[0-9]+$ ]] && ((major >= 22)); then
        command -v npm >/dev/null 2>&1 || die "Node.js is present but npm is missing."
        return
    fi

    log "Installing Node.js 22 build tooling for the Electron UI..."
    install -d -m 0755 /etc/apt/keyrings
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --batch --yes --dearmor -o /etc/apt/keyrings/nodesource.gpg
    chmod 0644 /etc/apt/keyrings/nodesource.gpg
    printf '%s\n' \
        'deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main' \
        > /etc/apt/sources.list.d/nodesource.list
    chmod 0644 /etc/apt/sources.list.d/nodesource.list
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
    major="$(node -p 'Number(process.versions.node.split(".")[0])')"
    ((major >= 22)) || die "Node.js 22 or newer is required to build the Worker UI."
}

install_worker_ui() (
    local build_dir api_port verify_user
    install_node_build_toolchain
    build_dir="$(mktemp -d /tmp/dml-worker-ui-build.XXXXXX)"
    trap 'rm -rf -- "$build_dir"' EXIT

    log "Building the Electron Worker console..."
    rsync -a --delete \
        --exclude='node_modules/' --exclude='out/' --exclude='dist/' \
        "$UI_SOURCE/" "$build_dir/"
    (
        cd -- "$build_dir"
        npm ci --no-audit --no-fund
        # Electron 43 exposes a dedicated install-electron command instead of
        # an npm postinstall lifecycle. `npm ci` therefore installs the JS
        # package but may leave out the ~270 MB runtime archive entirely.
        # Download it explicitly and fail here with a useful error rather than
        # letting the later rsync report a missing dist/ directory.
        if [[ ! -x node_modules/electron/dist/electron ]]; then
            log "Downloading the pinned Electron runtime..."
            node node_modules/electron/install.js
        fi
        [[ -x node_modules/electron/dist/electron ]] ||
            die "Electron runtime download did not produce node_modules/electron/dist/electron."
        npm run build
    )

    install -d -o root -g root -m 0755 "$UI_INSTALL_ROOT"
    rsync -a --delete "$build_dir/out/" "$UI_INSTALL_ROOT/out/"
    rsync -a --delete "$build_dir/resources/" "$UI_INSTALL_ROOT/resources/"
    install -o root -g root -m 0644 "$build_dir/package.json" "$UI_INSTALL_ROOT/package.json"
    rsync -a --delete "$build_dir/node_modules/electron/dist/" "$UI_INSTALL_ROOT/electron/"
    chown -R root:root "$UI_INSTALL_ROOT"
    # The installer-wide umask is intentionally 077 for credentials, but this
    # application tree must be readable/traversable by the desktop user.
    # Preserve execute bits only on directories and files already executable.
    chmod -R u=rwX,go=rX "$UI_INSTALL_ROOT"
    chmod 4755 "$UI_INSTALL_ROOT/electron/chrome-sandbox"

    api_port="$(env_get WORKER_API_PORT)"
    # Keep this non-secret endpoint outside /etc/dml: that directory is 0700
    # because it also contains Worker and registry credentials, so desktop
    # users must never be granted traversal access to it.
    printf 'WORKER_API_URL=http://127.0.0.1:%s\n' "$api_port" > /etc/dml-worker-ui.env
    chown root:root /etc/dml-worker-ui.env
    chmod 0644 /etc/dml-worker-ui.env

    cat > /usr/local/bin/dml-worker-ui <<'EOF'
#!/usr/bin/env sh
if [ "$(id -u)" -eq 0 ]; then
    echo "Run dml-worker-ui as a desktop user, not root." >&2
    exit 1
fi
WORKER_API_URL=http://127.0.0.1:8600
if [ -r /etc/dml-worker-ui.env ]; then
    . /etc/dml-worker-ui.env
fi
export WORKER_API_URL
exec /opt/dml/WorkerUI/electron/electron /opt/dml/WorkerUI "$@"
EOF
    chmod 0755 /usr/local/bin/dml-worker-ui

    # Do not announce success until the actual desktop user can traverse the
    # app, read its entry point/config, and execute Electron. This catches any
    # future restrictive-umask regression during installation.
    verify_user="${SUDO_USER:-nobody}"
    if [[ "$verify_user" == "root" ]] || ! id "$verify_user" >/dev/null 2>&1; then
        verify_user=nobody
    fi
    if command -v runuser >/dev/null 2>&1; then
        runuser -u "$verify_user" -- test -x "$UI_INSTALL_ROOT/electron/electron" ||
            die "Installed Electron binary is not executable by desktop users."
        runuser -u "$verify_user" -- test -r "$UI_INSTALL_ROOT/out/main/index.js" ||
            die "Installed Worker UI bundle is not readable by desktop users."
        runuser -u "$verify_user" -- test -r /etc/dml-worker-ui.env ||
            die "Installed Worker UI endpoint config is not readable by desktop users."
        runuser -u "$verify_user" -- "$UI_INSTALL_ROOT/electron/electron" --version >/dev/null ||
            die "Installed Electron runtime failed its non-root execution check."
    fi

    install -o root -g root -m 0644 "$UI_SOURCE/resources/dml-worker-ui.svg" \
        /usr/share/icons/hicolor/scalable/apps/dml-worker-ui.svg
    cat > /usr/share/applications/dml-worker-ui.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=DML Worker Console
Comment=Monitor the local Distributed ML worker
Exec=/usr/local/bin/dml-worker-ui
Icon=dml-worker-ui
Terminal=false
Categories=System;Monitor;
StartupNotify=true
EOF
    chmod 0644 /usr/share/applications/dml-worker-ui.desktop
    command -v update-desktop-database >/dev/null 2>&1 \
        && update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
    log "Electron console installed. Launch it from the app menu or run: dml-worker-ui"
)

install_docker_if_needed() {
    if command -v docker >/dev/null; then
        systemctl enable --now docker.service
        return
    fi

    log "Installing Docker Engine from Docker's Ubuntu repository..."
    local conflict
    for conflict in docker.io docker-compose docker-compose-v2 podman-docker containerd runc; do
        if dpkg-query -W -f='${db:Status-Status}' "$conflict" 2>/dev/null | grep -q '^installed$'; then
            die "Conflicting package $conflict is installed. Remove/migrate it deliberately, then rerun."
        fi
    done
    install -d -m 0755 /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod 0644 /etc/apt/keyrings/docker.asc
    local architecture codename
    architecture="$(dpkg --print-architecture)"
    codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
    [[ "$architecture" =~ ^[a-z0-9]+$ && "$codename" =~ ^[a-z0-9]+$ ]] ||
        die "Could not determine a safe Ubuntu architecture/codename for Docker."
    cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $codename
Components: stable
Architectures: $architecture
Signed-By: /etc/apt/keyrings/docker.asc
EOF
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker.service
}

install_nvidia_runtime_if_needed() (
    command -v nvidia-smi >/dev/null ||
        die "Install a supported NVIDIA host driver and reboot, then rerun this script."
    nvidia-smi -L >/dev/null 2>&1 ||
        die "The NVIDIA driver cannot enumerate a GPU; repair/reboot the driver before continuing."

    if docker info --format '{{json .Runtimes}}' | grep -q '"nvidia"'; then
        return
    fi

    log "Installing and configuring NVIDIA Container Toolkit..."
    local key_tmp list_tmp
    key_tmp="$(mktemp)"
    list_tmp="$(mktemp)"
    trap 'rm -f -- "$key_tmp" "$list_tmp"' EXIT
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey -o "$key_tmp"
    gpg --batch --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg "$key_tmp"
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list -o "$list_tmp"
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        "$list_tmp" > /etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-container-toolkit
    nvidia-ctk runtime configure --runtime=docker
    systemctl restart docker.service
    docker info --format '{{json .Runtimes}}' | grep -q '"nvidia"' ||
        die "NVIDIA Container Toolkit was installed but Docker does not expose the nvidia runtime."
)

detect_existing_join() {
    local port payload expected
    systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null || return 0
    port=8600
    if [[ -f "$LIVE_ENV" ]]; then
        port="$(env_get_from "$LIVE_ENV" WORKER_API_PORT)"
        [[ -n "$port" ]] || port=8600
    fi
    payload="$(curl --silent --show-error --max-time 3 "http://127.0.0.1:${port}/api/status" 2>/dev/null || true)"
    expected="$(source_env_get SCHEDULER_URL)"
    if python3 -c '
import json, sys
try:
    value = json.load(sys.stdin)
    ok = value.get("connected") is True and value.get("schedulerUrl", "").rstrip("/") == sys.argv[1].rstrip("/")
except Exception:
    ok = False
raise SystemExit(0 if ok else 1)
' "$expected" <<<"$payload"; then
        EXISTING_JOINED=1
    fi
}

env_set() {
    local file="$1" key="$2" value="$3"
    python3 - "$file" "$key" "$value" <<'PY'
from pathlib import Path
import os
import re
import sys
import tempfile

path = Path(sys.argv[1])
key, value = sys.argv[2:]
if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or any(c in value for c in "\x00\r\n"):
    raise SystemExit("unsafe environment update")
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
out = []
replaced = False
for line in lines:
    candidate = line.strip()
    if candidate.startswith("export "):
        candidate = candidate[7:].lstrip()
    if candidate.split("=", 1)[0].strip() == key and "=" in candidate:
        if not replaced:
            out.append(f"{key}={value}")
            replaced = True
    else:
        out.append(line)
if not replaced:
    out.append(f"{key}={value}")
path.parent.mkdir(parents=True, exist_ok=True)
fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(out) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
PY
}

stage_live_environment() {
    install -d -o root -g root -m 0700 /etc/dml
    install -o root -g root -m 0600 "$SOURCE_ENV" "$LIVE_ENV"

    [[ -n "$(env_get WORKER_SERVICE_CREDENTIAL_FILE)" ]] ||
        env_set "$LIVE_ENV" WORKER_SERVICE_CREDENTIAL_FILE /etc/dml/worker-service.secret
    [[ -n "$(env_get WORKER_ID_FILE)" ]] ||
        env_set "$LIVE_ENV" WORKER_ID_FILE "$STATE_DEFAULT/worker-id"
    [[ -n "$(env_get WORKER_STATE_DIR)" ]] ||
        env_set "$LIVE_ENV" WORKER_STATE_DIR "$STATE_DEFAULT"
    [[ -n "$(env_get WORKER_API_HOST)" ]] || env_set "$LIVE_ENV" WORKER_API_HOST 127.0.0.1
    [[ -n "$(env_get WORKER_API_PORT)" ]] || env_set "$LIVE_ENV" WORKER_API_PORT 8600
    [[ -n "$(env_get WORKER_ASSIGNMENT_LEASE_SECONDS)" ]] ||
        env_set "$LIVE_ENV" WORKER_ASSIGNMENT_LEASE_SECONDS 45
    [[ -n "$(env_get MAX_CONCURRENT_JOBS)" ]] || env_set "$LIVE_ENV" MAX_CONCURRENT_JOBS 2
    # VS Code Remote-SSH defaults (setup_worker.md §6.3). Explicit in the live
    # config so /etc/dml/worker.env always carries the gate state; kept
    # disabled (0) until the §6.5 deployment gate passes.
    [[ -n "$(env_get INTERACTIVE_ALLOW_SSH)" ]] || env_set "$LIVE_ENV" INTERACTIVE_ALLOW_SSH 0
    [[ -n "$(env_get INTERACTIVE_SSH_MAX_DURATION_SECONDS)" ]] ||
        env_set "$LIVE_ENV" INTERACTIVE_SSH_MAX_DURATION_SECONDS 14400
    [[ -n "$(env_get INTERACTIVE_SSH_CAPACITY)" ]] || env_set "$LIVE_ENV" INTERACTIVE_SSH_CAPACITY 8

    local docker_root
    docker_root="$(docker info --format '{{.DockerRootDir}}')"
    require_absolute_path DOCKER_DATA_ROOT "$docker_root"
    env_set "$LIVE_ENV" DOCKER_DATA_ROOT "$docker_root"
    chown root:root "$LIVE_ENV"
    chmod 0600 "$LIVE_ENV"
}

secure_parent() {
    local path="$1"
    install -d -o root -g root -m 0700 "$(dirname -- "$path")"
}

ensure_identity() {
    local id_file secret_file state_dir temp
    id_file="$(env_get WORKER_ID_FILE)"
    secret_file="$(env_get WORKER_SERVICE_CREDENTIAL_FILE)"
    state_dir="$(env_get WORKER_STATE_DIR)"
    require_absolute_path WORKER_ID_FILE "$id_file"
    require_absolute_path WORKER_SERVICE_CREDENTIAL_FILE "$secret_file"
    require_absolute_path WORKER_STATE_DIR "$state_dir"

    install -d -o root -g root -m 0700 "$state_dir"
    secure_parent "$id_file"
    secure_parent "$secret_file"

    if [[ -e "$id_file" ]]; then
        [[ ! -L "$id_file" && -f "$id_file" ]] || die "Unsafe Worker ID path: $id_file"
        python3 - "$id_file" <<'PY' || die "The existing Worker ID is not a UUID."
import sys, uuid
raw = open(sys.argv[1], encoding="ascii").read()
value = raw.strip()
raise SystemExit(0 if raw == value and str(uuid.UUID(value)) == value else 1)
PY
    else
        temp="$(mktemp "$(dirname -- "$id_file")/.worker-id.XXXXXX")"
        python3 -c 'import uuid; print(uuid.uuid4(), end="")' > "$temp"
        chmod 0600 "$temp"
        chown root:root "$temp"
        mv -T -- "$temp" "$id_file"
    fi

    if [[ -e "$secret_file" ]]; then
        [[ ! -L "$secret_file" && -f "$secret_file" ]] || die "Unsafe Worker secret path: $secret_file"
        python3 - "$secret_file" <<'PY' || die "The existing Worker secret is invalid."
import os, stat, sys
p = sys.argv[1]
s = os.stat(p, follow_symlinks=False)
raw = open(p, "rb").read()
valid = stat.S_ISREG(s.st_mode) and not (s.st_mode & 0o077) and 32 <= len(raw) <= 256
valid = valid and raw == raw.strip() and len(set(raw)) >= 8
raise SystemExit(0 if valid else 1)
PY
    else
        temp="$(mktemp "$(dirname -- "$secret_file")/.worker-secret.XXXXXX")"
        openssl rand -hex 32 | tr -d '\n' > "$temp"
        chmod 0600 "$temp"
        chown root:root "$temp"
        mv -T -- "$temp" "$secret_file"
    fi
    chown root:root "$id_file" "$secret_file"
    chmod 0600 "$id_file" "$secret_file"
}

registry_server_for() {
    local ref="${1%%@sha256:*}" first="${1%%/*}"
    if [[ "$ref" != */* || "$first" != *.* && "$first" != *:* && "$first" != "localhost" ]]; then
        printf 'docker.io'
    else
        printf '%s' "$first"
    fi
}

validate_registry_file() {
    local path="$1" expected_server="$2"
    python3 - "$path" "$expected_server" <<'PY'
import json, os, stat, sys
path, expected = sys.argv[1:]
s = os.stat(path, follow_symlinks=False)
with open(path, encoding="utf-8") as handle:
    value = json.load(handle)
valid = stat.S_ISREG(s.st_mode) and not (s.st_mode & 0o077)
valid = valid and set(value) == {"server", "username", "password"}
valid = valid and all(isinstance(v, str) and v for v in value.values())
valid = valid and value.get("server") == expected and ":" not in value.get("username", ":")
raise SystemExit(0 if valid else 1)
PY
}

write_registry_file() {
    local path="$1" server="$2" username="$3" password="$4"
    secure_parent "$path"
    printf '%s' "$password" | python3 -c '
import json, os, sys, tempfile
path, server, username = sys.argv[1:]
password = sys.stdin.read()
if not server or not username or not password or ":" in username:
    raise SystemExit("invalid registry credential")
parent = os.path.dirname(path)
fd, temporary = tempfile.mkstemp(prefix=".registry-pull.", dir=parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"server": server, "username": username, "password": password}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    try: os.unlink(temporary)
    except FileNotFoundError: pass
' "$path" "$server" "$username"
    chown root:root "$path"
    chmod 0600 "$path"
}

import_docker_credential() {
    local source="$1" target="$2" server="$3" wanted_user="$4"
    [[ -f "$source" ]] || return 1
    python3 - "$source" "$target" "$server" "$wanted_user" <<'PY'
import base64, json, os, sys, tempfile
source, target, server, wanted_user = sys.argv[1:]
try:
    config = json.load(open(source, encoding="utf-8"))
    auths = config.get("auths", {})
    candidates = [server]
    if server == "docker.io":
        candidates += ["https://index.docker.io/v1/", "index.docker.io", "registry-1.docker.io"]
    raw = next((auths[k].get("auth") for k in candidates if auths.get(k, {}).get("auth")), None)
    if not raw:
        raise SystemExit(1)
    username, password = base64.b64decode(raw).decode().split(":", 1)
    if wanted_user and username != wanted_user:
        raise SystemExit(1)
    fd, temporary = tempfile.mkstemp(prefix=".registry-pull.", dir=os.path.dirname(target))
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"server": server, "username": username, "password": password}, handle)
        handle.flush(); os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
except Exception:
    raise SystemExit(1)
PY
}

ensure_registry_credentials() {
    local path access_image server username invoker_home password
    path="$(env_get INTERACTIVE_REGISTRY_CREDENTIAL_FILE)"
    [[ -n "$path" ]] || return 0
    require_absolute_path INTERACTIVE_REGISTRY_CREDENTIAL_FILE "$path"
    access_image="$(env_get INTERACTIVE_ACCESS_IMAGE)"
    server="$(registry_server_for "$access_image")"

    if [[ -e "$path" ]]; then
        [[ ! -L "$path" ]] || die "Registry credential must not be a symlink: $path"
        validate_registry_file "$path" "$server" || die "Invalid registry credential file: $path"
        return
    fi

    secure_parent "$path"
    username="$(env_get INTERACTIVE_REGISTRY_USERNAME)"
    [[ -n "$username" ]] || username="$(env_get DOCKER_HUB_USERNAME)"

    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER:-root}" != "root" ]]; then
        invoker_home="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
        if import_docker_credential "$invoker_home/.docker/config.json" "$path" "$server" "$username"; then
            chown root:root "$path"
            chmod 0600 "$path"
            log "Imported the existing $server login into the protected pull-only credential file."
            return
        fi
    fi

    [[ -n "$username" ]] || die "Set DOCKER_HUB_USERNAME (or INTERACTIVE_REGISTRY_USERNAME) in Worker/.env."
    [[ -t 0 ]] || die "Missing $path. Rerun in a terminal so the registry token can be entered securely."
    printf 'Registry token/password for %s on %s: ' "$username" "$server" >&2
    IFS= read -r -s password
    printf '\n' >&2
    [[ -n "$password" ]] || die "Registry password was empty."
    write_registry_file "$path" "$server" "$username" "$password"
    unset password
}

validate_optional_files() {
    local key path
    for key in WORKER_SCHEDULER_CA_FILE INTERACTIVE_HEADSCALE_CA_FILE; do
        path="$(env_get "$key")"
        [[ -n "$path" ]] || continue
        require_absolute_path "$key" "$path"
        [[ -f "$path" && ! -L "$path" ]] || die "$key points to a missing/unsafe file: $path"
        chmod go-w "$path"
    done
}

validate_scheduler_connectivity() {
    local scheduler ca curl_args http_code
    scheduler="$(env_get SCHEDULER_URL)"
    ca="$(env_get WORKER_SCHEDULER_CA_FILE)"
    curl_args=(--silent --show-error --connect-timeout 10 --max-time 20 --output /dev/null --write-out '%{http_code}')
    if [[ -n "$ca" ]]; then
        curl_args+=(--cacert "$ca")
    fi
    http_code="$(curl "${curl_args[@]}" "$scheduler/")" ||
        die "Cannot reach the Scheduler with trusted TLS at $scheduler."
    [[ "$http_code" =~ ^[1-5][0-9]{2}$ ]] || die "Scheduler connectivity returned an invalid HTTP status."
    log "Scheduler HTTPS connectivity passed (HTTP $http_code)."
}

deploy_worker_code() {
    if docker ps --filter label=dml.assignment --format '{{.ID}}' | grep -q .; then
        die "This host has an active DML assignment; stop/drain it before updating the Worker."
    fi

    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl stop "$GUARD_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" "$GUARD_NAME" 2>/dev/null || true

    install -d -o root -g root -m 0755 "$INSTALL_ROOT/Worker" "$INSTALL_ROOT/Access_Container"
    rsync -a --delete --delete-excluded \
        --exclude='.env' --exclude='venv/' --exclude='__pycache__/' \
        --exclude='.pytest_cache/' --exclude='output/' --exclude='worker.log' \
        "$REPO_ROOT/Worker/" "$INSTALL_ROOT/Worker/"
    rsync -a --delete --exclude='__pycache__/' "$REPO_ROOT/Access_Container/" "$INSTALL_ROOT/Access_Container/"
    chown -R root:root "$INSTALL_ROOT/Worker" "$INSTALL_ROOT/Access_Container"

    if [[ ! -x "$INSTALL_ROOT/venv/bin/python" ]]; then
        rm -rf -- "$INSTALL_ROOT/venv"
        python3 -m venv "$INSTALL_ROOT/venv"
    fi
    "$INSTALL_ROOT/venv/bin/python" -m pip install --disable-pip-version-check \
        -r "$INSTALL_ROOT/Worker/requirements.txt"

    install -o root -g root -m 0644 \
        "$REPO_ROOT/deploy/interactive/worker/dml-worker.service" \
        "$REPO_ROOT/deploy/interactive/worker/dml-worker-lease-guard.service" \
        /etc/systemd/system/
    systemctl daemon-reload
}

docker_auth_dir() {
    local credential="$1" directory="$2"
    python3 - "$credential" "$directory/config.json" <<'PY'
import base64, json, os, sys
credential, output = sys.argv[1:]
value = json.load(open(credential, encoding="utf-8"))
auth = base64.b64encode((value["username"] + ":" + value["password"]).encode()).decode()
with open(output, "w", encoding="utf-8") as handle:
    json.dump({"auths": {value["server"]: {"auth": auth}}}, handle)
os.chmod(output, 0o600)
PY
}

pull_runtime_images() (
    local auth_dir credential access_image preflight_image tailscale_image
    auth_dir="$(mktemp -d /run/dml-worker-install-auth.XXXXXX)"
    chmod 0700 "$auth_dir"
    trap 'rm -rf -- "$auth_dir"' EXIT
    credential="$(env_get INTERACTIVE_REGISTRY_CREDENTIAL_FILE)"
    if [[ -n "$credential" ]]; then
        docker_auth_dir "$credential" "$auth_dir"
    else
        printf '{"auths":{}}\n' > "$auth_dir/config.json"
        chmod 0600 "$auth_dir/config.json"
    fi

    access_image="$(env_get INTERACTIVE_ACCESS_IMAGE)"
    preflight_image="$(env_get INTERACTIVE_PREFLIGHT_IMAGE)"
    tailscale_image="$(python3 - "$INSTALL_ROOT/Worker/interactive/docker_ops.py" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r'^TAILSCALE\s*=\s*"([^"]+@sha256:[0-9a-f]{64})"', text, re.M)
if not match: raise SystemExit("Could not find pinned Tailscale image")
print(match.group(1), end="")
PY
)"
    log "Pulling pinned interactive service images..."
    docker --config "$auth_dir" pull "$access_image"
    docker --config "$auth_dir" pull "$preflight_image"
    docker pull "$tailscale_image"
)

interactive_preflight() (
    local image container_name
    image="$(env_get INTERACTIVE_PREFLIGHT_IMAGE)"
    container_name="dml-quota-preflight-install-$$"
    trap 'docker rm -f "$container_name" >/dev/null 2>&1 || true' EXIT

    log "Checking NVIDIA container access..."
    docker run --rm --network none --gpus all --entrypoint /bin/sh "$image" \
        -c 'test -r /proc/driver/nvidia/version' >/dev/null

    log "Checking Docker writable-layer quota support..."
    docker create --name "$container_name" --network none --storage-opt size=1G \
        --entrypoint /bin/sh \
        --label dml.component=quota-preflight-install "$image" -c 'exit 0' >/dev/null
    docker start --attach "$container_name" >/dev/null
    [[ "$(docker inspect "$container_name" --format '{{.State.ExitCode}}')" == "0" ]] ||
        die "The disposable storage quota preflight container failed."
    docker rm "$container_name" >/dev/null
)

print_registration() {
    local id_file secret_file
    id_file="$(env_get WORKER_ID_FILE)"
    secret_file="$(env_get WORKER_SERVICE_CREDENTIAL_FILE)"
    printf '\nAdd this entry to /etc/dml/worker-credentials.json on the Scheduler VM:\n\n'
    python3 - "$id_file" "$secret_file" <<'PY'
import json, sys
worker_id = open(sys.argv[1], encoding="ascii").read()
secret = open(sys.argv[2], encoding="ascii").read()
print(json.dumps({worker_id: [secret]}))
PY
    printf '\nThen recreate/restart the Scheduler API so it reads the updated map.\n'
}

show_runtime_env() {
    local pid configured actual
    [[ -f "$LIVE_ENV" ]] || die "No installed Worker environment at $LIVE_ENV. Run the installer first."
    [[ "${EUID}" == 0 ]] || die "Run --show-runtime-env with sudo so it can inspect the systemd Worker process."

    # Show only values that are safe to display.  In particular, never print
    # the service secret or the registry pull token held in its protected file.
    printf 'Configured in %s:\n' "$LIVE_ENV"
    for key in INTERACTIVE_WORKER_ENABLED INTERACTIVE_REGISTRY_PREFIXES \
        INTERACTIVE_REGISTRY_CREDENTIAL_FILE INTERACTIVE_ACCESS_IMAGE \
        INTERACTIVE_PREFLIGHT_IMAGE INTERACTIVE_ALLOW_SSH \
        INTERACTIVE_SSH_MAX_DURATION_SECONDS INTERACTIVE_SSH_CAPACITY \
        INTERACTIVE_ALLOW_INTERNET INTERACTIVE_ALLOW_DEVELOPER_MODE; do
        configured="$(env_get "$key")"
        printf '  %s=%s\n' "$key" "${configured:-<unset>}"
    done

    if ! systemctl is-active --quiet "$SERVICE_NAME"; then
        printf '\n%s is not active, so there is no process environment to inspect.\n' "$SERVICE_NAME"
        return 0
    fi
    pid="$(systemctl show --value --property=MainPID "$SERVICE_NAME")"
    [[ "$pid" =~ ^[1-9][0-9]*$ && -r "/proc/$pid/environ" ]] ||
        die "Could not read the active systemd Worker process environment."
    proc_env_get() {
        tr '\0' '\n' < "/proc/$pid/environ" | sed -n "s/^$1=//p" | tail -n 1
    }
    printf '\nRunning %s (PID %s):\n' "$SERVICE_NAME" "$pid"
    for key in INTERACTIVE_REGISTRY_PREFIXES INTERACTIVE_ACCESS_IMAGE INTERACTIVE_ALLOW_SSH; do
        actual="$(proc_env_get "$key")"
        configured="$(env_get "$key")"
        printf '  %s=%s\n' "$key" "${actual:-<unset>}"
        [[ -n "$actual" ]] || die "The active service did not receive $key. Rerun the installer and restart the service."
        [[ "$actual" == "$configured" ]] ||
            die "The active service has a stale $key value. Run: sudo systemctl restart $SERVICE_NAME"
    done
    log "The active systemd Worker received the configured interactive registry allowlist, access digest, and SSH gate."
}

wait_for_worker() {
    local port deadline payload
    port="$(env_get WORKER_API_PORT)"
    deadline=$((SECONDS + 45))
    while ((SECONDS < deadline)); do
        payload="$(curl --silent --show-error --max-time 2 "http://127.0.0.1:${port}/api/status" 2>/dev/null || true)"
        if python3 -c 'import json,sys; raise SystemExit(0 if json.load(sys.stdin).get("connected") is True else 1)' \
            <<<"$payload" 2>/dev/null; then
            log "Worker joined the Scheduler and is sending heartbeats."
            return 0
        fi
        sleep 2
    done
    warn "Worker did not report a connected heartbeat within 45 seconds."
    journalctl -u "$SERVICE_NAME" --since '-2 minutes' --no-pager -n 30 >&2 || true
    return 1
}

if ((SHOW_REGISTRATION)); then
    [[ -f "$LIVE_ENV" ]] || die "No installed Worker identity; run the installer first."
    command -v python3 >/dev/null || die "python3 is required to read the installed identity."
    print_registration
    exit 0
fi

if ((SHOW_RUNTIME_ENV)); then
    show_runtime_env
    exit 0
fi
install_base_packages
validate_env_file "$SOURCE_ENV" source_env_get
install_docker_if_needed
install_nvidia_runtime_if_needed
detect_existing_join
stage_live_environment
validate_env_file "$LIVE_ENV" env_get
ensure_identity

ensure_registry_credentials
validate_optional_files
validate_scheduler_connectivity
install_worker_ui
deploy_worker_code
pull_runtime_images
interactive_preflight

if ((NO_START)); then
    print_registration
    log "Installation completed; services are disabled and stopped (--no-start)."
    exit 0
fi

if ((!REGISTRATION_CONFIRMED && !EXISTING_JOINED)); then
    print_registration
    if [[ ! -t 0 ]]; then
        log "Installation completed, but the services remain stopped until the Scheduler entry is added."
        log "After registering it, rerun: sudo bash Worker/join_worker.sh --registered"
        exit 2
    fi
    printf 'After updating the Scheduler, type REGISTERED to start this Worker: ' >&2
    IFS= read -r confirmation
    [[ "$confirmation" == "REGISTERED" ]] || {
        log "Services remain stopped. Rerun with --registered after updating the Scheduler."
        exit 2
    }
fi

systemctl enable --now "$GUARD_NAME" "$SERVICE_NAME"
wait_for_worker || {
    print_registration
    die "Join verification failed. Confirm the Scheduler map entry, TLS, and service journal."
}

systemctl is-active --quiet "$GUARD_NAME" || die "The independent lease guard is not active."
log "Interactive Worker installation completed successfully."
