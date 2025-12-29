#!/usr/bin/env bash
set -euo pipefail

ARCHIVE_NAME="tg-catalog-layered-final.tar"
PROJECT_DIR="/root/tg-catalog-layered-final"
VENV_DIR="$PROJECT_DIR/.venv"
SERVICE_NAME="tg-catalog.service"

cd /root

echo "[deploy] Останавливаю сервис (если есть)..."
systemctl stop "$SERVICE_NAME" || true

echo "[deploy] Бэкап старой папки, если существует..."
if [ -d "$PROJECT_DIR" ]; then
    mv "$PROJECT_DIR" "tg-catalog-backup-$(date +%Y%m%d-%H%M%S)"
fi

echo "[deploy] Распаковываю архив..."
tar xf "$ARCHIVE_NAME"

echo "[deploy] Обновляю виртуальное окружение..."
cd "$PROJECT_DIR"
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "[deploy] Копирую unit файл systemd..."
cp deploy/tg-catalog.service /etc/systemd/system/tg-catalog.service

echo "[deploy] Перечитываю systemd и запускаю сервис..."
systemctl daemon-reload
systemctl enable tg-catalog.service
systemctl restart tg-catalog.service

echo "[deploy] Статус сервиса:"
systemctl status tg-catalog.service --no-pager || true

echo "[deploy] Готово."
