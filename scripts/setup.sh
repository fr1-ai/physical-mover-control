#!/bin/bash
# setup.sh — Run ONCE on the Pi to install deps and the systemd service.
#
# Usage:
#   cd ~/physical-mover-control
#   ./scripts/setup.sh

set -euo pipefail

REPO_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
SERVICE_NAME="mower-client"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "==> Repo dir: $REPO_DIR"

echo "==> Installing Python dependencies"
pip install --break-system-packages -r "$REPO_DIR/pi/requirements.txt"

echo "==> Checking for .env file"
ENV_FILE="$REPO_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "    .env not found — creating template"
  cat > "$ENV_FILE" <<EOF
# Edit these. DO NOT commit this file (.gitignore covers it).
RELAY_URL=wss://your-app.up.railway.app/mower
MOWER_TOKEN=replace-with-secret-from-railway
EOF
  echo "    >>> Edit $ENV_FILE before starting the service. <<<"
fi

echo "==> Installing systemd service"
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Mower Pi Client
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$REPO_DIR
EnvironmentFile=$REPO_DIR/.env
ExecStart=/usr/bin/python3 $REPO_DIR/pi/mower_pi_client.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

echo "==> Reloading systemd"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo ""
echo "==> Setup complete."
echo ""
echo "Next steps:"
echo "  1. Edit $ENV_FILE with your Railway URL and MOWER_TOKEN"
echo "  2. Start the service:   sudo systemctl start $SERVICE_NAME"
echo "  3. Watch logs:          journalctl -u $SERVICE_NAME -f"
echo ""
echo "To deploy code changes later:   ./scripts/deploy.sh"
