#!/usr/bin/env bash
set -euo pipefail

# Restart the Scheduler stack (interactive-access branch) cleanly.
# - Stops & removes the running app-* containers
# - Rebuilds and brings the stack back up
# The gateway-keys volume is PRESERVED across restarts so the gateway's SSH
# host key stays stable (users are not prompted with a "host key changed"
# MITM warning on every restart). Per-session keys inside that volume are
# cleaned up individually when each interactive session is stopped.
# Caddy config at /etc/caddy/Caddyfile is untouched (proxies
# scheduler.zulfiker.xyz -> 127.0.0.1:8000 and headscale -> 127.0.0.1:8080).

cd "$(dirname "$0")"

echo "==> Stopping & removing containers..."
docker compose down

echo "==> Rebuilding & starting stack (gateway-keys volume preserved)..."
docker compose up -d --build

echo "==> Done. Current status:"
docker compose ps
