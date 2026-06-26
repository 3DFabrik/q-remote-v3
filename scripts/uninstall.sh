#!/usr/bin/env bash
# Remove Q-Remote V3 systemd service (does not delete the repo or data)
set -euo pipefail

SERVICE_NAME="q-remote"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERROR: Run with sudo" >&2
    exit 1
fi

if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    systemctl stop "$SERVICE_NAME"
fi
if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
    systemctl disable "$SERVICE_NAME"
fi

if [[ -f "$SERVICE_FILE" ]]; then
    rm -f "$SERVICE_FILE"
    systemctl daemon-reload
    echo "Removed $SERVICE_FILE"
else
    echo "Service file not found — nothing to do"
fi
