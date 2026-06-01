#!/bin/bash
# Uninstall Hermes Kanban Bridge
# Removes runtime files, systemd service, and config

set -e

BRIDGE_DST="/opt/legion-dashboard/hermes-bridge"
SYSTEMD_DST="/etc/systemd/system/legion-dashboard-hermes-kanban-bridge.service"
CONFIG_DIR="/etc/legion-dashboard"

echo "=== Uninstalling Hermes Kanban Bridge ==="

# Stop and disable service
echo "Stopping service..."
systemctl stop legion-dashboard-hermes-kanban-bridge.service || true
systemctl disable legion-dashboard-hermes-kanban-bridge.service || true

# Reload systemd
echo "Reloading systemd daemon..."
systemctl daemon-reload

# Remove runtime directory
echo "Removing runtime directory..."
rm -rf "$BRIDGE_DST"

# Remove systemd unit
echo "Removing systemd unit..."
rm -f "$SYSTEMD_DST"

# Remove config directory (only if empty or only contains our files)
if [[ -d "$CONFIG_DIR" ]]; then
    if [[ -f "$CONFIG_DIR/hermes-bridge.env" ]]; then
        echo "Removing config file..."
        rm -f "$CONFIG_DIR/hermes-bridge.env"
    fi
    # Remove dir only if empty
    rmdir "$CONFIG_DIR" 2>/dev/null || echo "Config directory has other files, keeping..."
fi

echo ""
echo "=== Uninstallation Complete ==="
echo "The following were removed:"
echo "  - $BRIDGE_DST"
echo "  - $SYSTEMD_DST"
echo "  - $CONFIG_DIR/hermes-bridge.env (if present)"
echo ""
echo "Source code remains in repo: /srv/repo/legion-dashboard/ops/hermes-bridge/"
