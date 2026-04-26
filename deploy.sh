#!/usr/bin/env bash
set -euo pipefail

# Simple deploy helper for VPS systemd setup.
# Usage:
#   ./deploy.sh            # defaults to main branch
#   ./deploy.sh master     # deploy specific branch

BRANCH="${1:-main}"
SERVICE_NAME="${SERVICE_NAME:-goldbot}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="${PROJECT_DIR}/.venv"

echo "==> Deploying branch: ${BRANCH}"
cd "${PROJECT_DIR}"

echo "==> Fetching latest code"
git fetch origin "${BRANCH}"
git checkout "${BRANCH}"
git pull --ff-only origin "${BRANCH}"

echo "==> Ensuring virtualenv exists"
if [[ ! -d "${VENV_PATH}" ]]; then
  python3 -m venv "${VENV_PATH}"
fi

echo "==> Installing dependencies"
source "${VENV_PATH}/bin/activate"
pip install -r requirements.txt

echo "==> Restarting systemd service: ${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

echo "==> Service status"
sudo systemctl --no-pager --full status "${SERVICE_NAME}" | sed -n '1,20p'

echo "==> Recent logs"
sudo journalctl -u "${SERVICE_NAME}" -n 30 --no-pager

echo "==> Deploy completed"
