#!/bin/bash
# bootstrap.sh — one-shot Pi setup: install deps, configure .env, start service.
#
# Usage:
#   git clone https://github.com/fr1-ai/physical-mover-control.git ~/physical-mover-control
#   cd ~/physical-mover-control
#   RELAY_URL=wss://your-app.up.railway.app/mower \
#   MOWER_TOKEN=<secret from Railway> \
#     bash scripts/bootstrap.sh

set -euo pipefail

REPO_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
SERVICE_NAME="mower-client"

if [ -z "${RELAY_URL:-}" ] || [ -z "${MOWER_TOKEN:-}" ]; then
  cat >&2 <<USAGE
ERROR: RELAY_URL and MOWER_TOKEN environment variables are required.

Run as:
  RELAY_URL=wss://your-app.up.railway.app/mower \\
  MOWER_TOKEN=<secret> \\
    bash scripts/bootstrap.sh
USAGE
  exit 1
fi

echo "==> Running setup.sh (deps + systemd unit)"
bash "$REPO_DIR/scripts/setup.sh"

echo "==> Writing .env from environment (chmod 600)"
umask 077
cat > "$REPO_DIR/.env" <<EOF
RELAY_URL=$RELAY_URL
MOWER_TOKEN=$MOWER_TOKEN
EOF
chmod 600 "$REPO_DIR/.env"

echo "==> Starting service"
sudo systemctl restart "$SERVICE_NAME"

sleep 2
echo ""
echo "==> Service status:"
sudo systemctl status "$SERVICE_NAME" --no-pager -n 8 || true

echo ""
echo "==> Bootstrap complete."
echo "Tail logs with:   journalctl -u $SERVICE_NAME -f"
echo "Restart later:    sudo systemctl restart $SERVICE_NAME"
echo "Update code:      ./scripts/deploy.sh"
