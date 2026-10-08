#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

command -v docker >/dev/null || { echo "Missing required command: docker" >&2; exit 1; }

# UI toolchains (Vite 8) require Node >= 20 but this host ships Node 18, so the
# UIs are built inside a pinned Node container instead of the host toolchain.
# The container runs as the invoking user so checkout files stay owned by them
# even when this script itself runs under sudo.
ui_node_image="${UI_NODE_IMAGE:-node:22-bookworm-slim}"
build_uid="${SUDO_UID:-$(id -u)}"
build_gid="${SUDO_GID:-$(id -g)}"

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
    echo "==> Building $ui_label (container $ui_node_image)..."
    docker image inspect "$ui_node_image" >/dev/null 2>&1 || docker pull "$ui_node_image"
    docker run --rm --user "$build_uid:$build_gid" \
        --volume "$ui_dir:/app" --workdir /app \
        --env HOME=/tmp --env npm_config_cache=/tmp/npm-cache \
        "$ui_node_image" \
        sh -c '(npm ci --no-audit --no-fund || npm install --no-audit --no-fund) && npm run build'
}

build_ui "$PWD/User" "User UI"
build_ui "$PWD/Admin" "Admin UI"

echo "==> Deploying User UI to /var/www/distributeml/..."
sudo rm -rf /var/www/distributeml/*
sudo cp -r User/dist/* /var/www/distributeml/

echo "==> Deploying Admin UI to /var/www/adminui/..."
sudo rm -rf /var/www/adminui/*
sudo cp -r Admin/dist/* /var/www/adminui/

echo "==> Reloading Caddy..."
# reload is graceful; if Caddy is stopped or the notify unit is stale, a plain
# reload can fail (or be a silent no-op on a dead unit), leaving the freshly
# copied bundle unreachable. Fall back to a full restart so the new bundle is
# actually live, then confirm the unit is active.
sudo systemctl reload caddy || sudo systemctl restart caddy
sudo systemctl is-active --quiet caddy

echo "==> Done. UIs are live at:"
echo "    https://distributeml.zulfiker.xyz"
echo "    https://admin.zulfiker.xyz"
