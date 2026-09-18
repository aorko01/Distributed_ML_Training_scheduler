#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Installing User UI dependencies..."
(cd User && npm ci --no-audit --no-fund || npm install --no-audit --no-fund)

echo "==> Building User UI..."
(cd User && npm run build)

echo "==> Installing Admin UI dependencies..."
(cd Admin && npm ci --no-audit --no-fund || npm install --no-audit --no-fund)

echo "==> Building Admin UI..."
(cd Admin && npm run build)

echo "==> Deploying User UI to /var/www/distributeml/..."
sudo rm -rf /var/www/distributeml/*
sudo cp -r User/dist/* /var/www/distributeml/

echo "==> Deploying Admin UI to /var/www/adminui/..."
sudo rm -rf /var/www/adminui/*
sudo cp -r Admin/dist/* /var/www/adminui/

echo "==> Reloading Caddy..."
sudo systemctl reload caddy

echo "==> Done. UIs are live at:"
echo "    https://distributeml.zulfiker.xyz"
echo "    https://admin.zulfiker.xyz"
