#!/bin/bash
# Install Hermes Kanban Bridge from repo to host runtime
# Source: /srv/repo/legion-dashboard/ops/hermes-bridge/
# Runtime: /opt/legion-dashboard/hermes-bridge/

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

BRIDGE_SRC="$SCRIPT_DIR/hermes_kanban_bridge.py"
BRIDGE_DST="/opt/legion-dashboard/hermes-bridge"
SYSTEMD_SRC="$SCRIPT_DIR/../systemd/legion-dashboard-hermes-kanban-bridge.service"
SYSTEMD_DST="/etc/systemd/system/legion-dashboard-hermes-kanban-bridge.service"
CONFIG_DIR="/etc/legion-dashboard"
ENV_EXAMPLE="$SCRIPT_DIR/hermes-bridge.env.example"
ENV_DST="$CONFIG_DIR/hermes-bridge.env"

echo "=== Installing Hermes Kanban Bridge ==="
echo "Source: $BRIDGE_SRC"
echo "Destination: $BRIDGE_DST"

# Verify source exists
if [[ ! -f "$BRIDGE_SRC" ]]; then
    echo "ERROR: Bridge source not found: $BRIDGE_SRC"
    exit 1
fi

if [[ ! -f "$SYSTEMD_SRC" ]]; then
    echo "ERROR: Systemd unit not found: $SYSTEMD_SRC"
    exit 1
fi

# Create runtime directory
echo "Creating runtime directory..."
mkdir -p "$BRIDGE_DST"

# Copy bridge script
echo "Copying bridge script..."
cp "$BRIDGE_SRC" "$BRIDGE_DST/hermes_kanban_bridge.py"
chmod +x "$BRIDGE_DST/hermes_kanban_bridge.py"

# Create config directory
echo "Creating config directory..."
mkdir -p "$CONFIG_DIR"

# Install env file only if not exists
if [[ ! -f "$ENV_DST" ]]; then
    echo "Installing environment file..."
    cp "$ENV_EXAMPLE" "$ENV_DST"
else
    echo "Environment file already exists, skipping..."
fi

# Install systemd service
echo "Installing systemd service..."
cp "$SYSTEMD_SRC" "$SYSTEMD_DST"

# Reload systemd
echo "Reloading systemd daemon..."
systemctl daemon-reload

# Enable and start service
echo "Enabling and starting service..."
systemctl enable legion-dashboard-hermes-kanban-bridge.service
systemctl restart legion-dashboard-hermes-kanban-bridge.service

# Wait for service to start
sleep 2

# Verify service is running
echo "Verifying service..."
if systemctl is-active --quiet legion-dashboard-hermes-kanban-bridge.service; then
    echo "✓ Service is running"
else
    echo "✗ Service failed to start"
    systemctl status legion-dashboard-hermes-kanban-bridge.service --no-pager
    exit 1
fi

# Health check
echo "Running health check..."
if curl -sS http://127.0.0.1:8765/health | grep -q '"status": "ok"'; then
    echo "✓ Health check passed"
else
    echo "✗ Health check failed"
    curl -sS http://127.0.0.1:8765/health
    exit 1
fi

echo ""
echo "=== Installation Complete ==="
echo "Service: legion-dashboard-hermes-kanban-bridge.service"
echo "Runtime: $BRIDGE_DST"
echo "Config: $CONFIG_DIR"
echo "Port: 8765"
echo ""
echo "Commands:"
echo "  systemctl status legion-dashboard-hermes-kanban-bridge.service"
echo "  curl -sS http://127.0.0.1:8765/health"
echo "  journalctl -u legion-dashboard-hermes-kanban-bridge.service -f"
