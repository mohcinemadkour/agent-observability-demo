#!/usr/bin/env bash
# Create a read-only (Viewer) Grafana account for a client.
#   ./deploy/add_client_viewer.sh "Jane Doe" jane@client.com
# Prints a generated password to send to the client securely.
set -euo pipefail
cd "$(dirname "$0")/.."
[ $# -eq 2 ] || { echo 'usage: add_client_viewer.sh "Full Name" email@client.com'; exit 1; }

set -a; . ./.env; set +a
NAME="$1"; EMAIL="$2"
PASS="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)"

BODY=$(printf '{"name":"%s","email":"%s","login":"%s","password":"%s"}' "$NAME" "$EMAIL" "$EMAIL" "$PASS")
curl -fsS -u "${GRAFANA_ADMIN_USER:-admin}:${GRAFANA_ADMIN_PASSWORD}" \
  -H "Content-Type: application/json" -X POST \
  "https://${DASHBOARD_DOMAIN}/api/admin/users" -d "$BODY" >/dev/null

echo "Created Viewer account"
echo "  URL:      https://${DASHBOARD_DOMAIN}/d/agent-observability"
echo "  Login:    ${EMAIL}"
echo "  Password: ${PASS}   (send it over a separate channel; they can change it in their profile)"
