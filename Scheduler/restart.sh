#!/usr/bin/env bash
set -euo pipefail

# Restart the Scheduler stack (interactive-access branch) cleanly.
# - Stops & removes the running app-* containers
# - Removes the gateway-keys named volume (regenerated on start)
# - Rebuilds and brings the stack back up
# Caddy config at /etc/caddy/Caddyfile is untouched (proxies
# scheduler.zulfiker.xyz -> 127.0.0.1:8000 and headscale -> 127.0.0.1:8080).

cd "$(dirname "$0")"

echo "==> Stopping & removing containers..."
docker compose down

echo "==> Removing gateway-keys volume (regenerated on restart)..."
docker volume rm scheduler_gateway-keys 2>/dev/null || echo "   (volume already gone or not present)"

echo "==> Rebuilding & starting stack..."
docker compose up -d --build

echo "==> Done. Current status:"
docker compose ps
