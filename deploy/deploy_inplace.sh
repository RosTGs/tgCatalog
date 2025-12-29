#!/usr/bin/env bash
set -euo pipefail

SERVICE="tg-catalog"
INSTALL_DIR="/root/tg-catalog-layered-final"

cd "$INSTALL_DIR"

echo "[deploy] stop old service (if any)..."
systemctl stop "$SERVICE" 2>/dev/null || true

echo "[deploy] create venv..."
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

echo "[deploy] copy .env if missing and old exists..."
if [ ! -f .env ] && [ -f /root/tg-catalog/.env ]; then
  cp /root/tg-catalog/.env .env
fi

echo "[deploy] install systemd unit..."
install -m 0644 deploy/tg-catalog.service /etc/systemd/system/tg-catalog.service
systemctl daemon-reload
systemctl enable "$SERVICE"

echo "[deploy] restart service..."
systemctl restart "$SERVICE"
systemctl status "$SERVICE" -n 50 --no-pager
