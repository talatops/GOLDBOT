#!/usr/bin/env bash
set -euo pipefail

# Create/update a systemd service for this bot on EC2 (Amazon Linux/Ubuntu).
# Designed for user: ec2-user
#
# Usage:
#   chmod +x setup_service_ec2.sh
#   ./setup_service_ec2.sh
#
# Optional overrides:
#   SERVICE_NAME=goldbot ./setup_service_ec2.sh
#   PROJECT_DIR=/home/ec2-user/TELEBOT ./setup_service_ec2.sh
#   BOT_USER=ec2-user ./setup_service_ec2.sh

SERVICE_NAME="${SERVICE_NAME:-goldbot}"
BOT_USER="${BOT_USER:-ec2-user}"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
VENV_PATH="${VENV_PATH:-${PROJECT_DIR}/.venv}"
PYTHON_BIN="${PYTHON_BIN:-${VENV_PATH}/bin/python}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "==> Preparing virtualenv at: ${VENV_PATH}"
cd "${PROJECT_DIR}"
if [[ ! -d "${VENV_PATH}" ]]; then
  python3 -m venv "${VENV_PATH}"
fi

source "${VENV_PATH}/bin/activate"
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Writing systemd service: ${SERVICE_FILE}"
sudo tee "${SERVICE_FILE}" >/dev/null <<EOF
[Unit]
Description=Telegram Gold Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${BOT_USER}
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=${PROJECT_DIR}/.env
ExecStart=${PYTHON_BIN} -m src.bot
Restart=always
RestartSec=5
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF

echo "==> Reloading systemd and enabling ${SERVICE_NAME}"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

echo "==> Service status (first lines)"
sudo systemctl --no-pager --full status "${SERVICE_NAME}" | sed -n '1,25p'

echo "==> Recent logs"
sudo journalctl -u "${SERVICE_NAME}" -n 40 --no-pager

echo "==> Done. Service is configured for 24/7 auto-restart + boot start."
