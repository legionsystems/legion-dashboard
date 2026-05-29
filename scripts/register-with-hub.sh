#!/usr/bin/env bash
# Register this app with the LEGION hub.
#
# Usage: ./scripts/register-with-hub.sh
#
# Env:
#   HUB_URL       (default: http://127.0.0.1:8000)
#   APP_ID        (default: legion-dashboard)
#   APP_NAME      (default: LEGION Dashboard)
#   APP_VERSION   (default: 0.1.0)
#   APP_BASE_URL  (default: http://127.0.0.1:${APP_HOST_PORT:-8720})

set -euo pipefail

HUB_URL="${HUB_URL:-http://127.0.0.1:8000}"
APP_ID="${APP_ID:-legion-dashboard}"
APP_NAME="${APP_NAME:-LEGION Dashboard}"
APP_VERSION="${APP_VERSION:-0.1.0}"
APP_HOST_PORT="${APP_HOST_PORT:-8720}"
APP_BASE_URL="${APP_BASE_URL:-http://127.0.0.1:${APP_HOST_PORT}}"

payload=$(cat <<EOF
{
  "app_id": "${APP_ID}",
  "app_name": "${APP_NAME}",
  "app_version": "${APP_VERSION}",
  "base_url": "${APP_BASE_URL}",
  "manifest_path": "/federation/manifest",
  "health_path": "/health"
}
EOF
)

echo "Registering ${APP_ID} (${APP_BASE_URL}) with hub at ${HUB_URL}..."
curl -fsS -X POST "${HUB_URL}/api/apps/register" \
  -H "Content-Type: application/json" \
  -d "${payload}"
echo
echo "Done."
