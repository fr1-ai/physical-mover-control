#!/bin/bash
# deploy.sh — Pull latest code and restart the mower service.
#
# Usage:
#   cd ~/physical-mover-control
#   ./scripts/deploy.sh

set -euo pipefail

REPO_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
SERVICE_NAME="mower-client"

echo "==> Pulling latest code"
cd "$REPO_DIR"
git pull --ff-only

echo "==> Installing/updating Python deps (if changed)"
pip install --break-system-packages -q -r "$REPO_DIR/pi/requirements.txt"

echo "==> Restarting service"
sudo systemctl restart "$SERVICE_NAME"

sleep 1
sudo systemctl status "$SERVICE_NAME" --no-pager -n 5
echo ""
echo "==> Deploy complete. Watch logs with:  journalctl -u $SERVICE_NAME -f"
