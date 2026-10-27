#!/usr/bin/env bash
# Start or update the production stack on the server.
#   ./deploy/deploy.sh          # build + start / update
#   ./deploy/deploy.sh down     # stop (data is kept)
#   ./deploy/deploy.sh logs     # follow logs
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE=(docker compose -f docker-compose.yaml -f docker-compose.prod.yaml)

[ -f .env ] || { echo "No .env found. Run: cp deploy/.env.prod.example .env  and fill it in."; exit 1; }
grep -q "change-me" .env && { echo "Replace every 'change-me' value in .env first."; exit 1; }
grep -q "replace.with.the.output" .env && { echo "Set DEMO_PASSWORD_HASH in .env (see deploy/DEPLOY.md)."; exit 1; }

case "${1:-up}" in
  up)
    "${COMPOSE[@]}" config -q
    "${COMPOSE[@]}" up -d --build --remove-orphans
    "${COMPOSE[@]}" ps
    set -a; . ./.env; set +a
    echo
    echo "Dashboard:     https://${DASHBOARD_DOMAIN}   (admin user: ${GRAFANA_ADMIN_USER:-admin})"
    echo "Control panel: https://${DEMO_DOMAIN}"
    echo "OTLP ingest:   https://${OTLP_DOMAIN}/v1/{traces,metrics,logs}"
    echo "First start: certificates can take a minute. Check with: ./deploy/deploy.sh logs caddy"
    ;;
  down) "${COMPOSE[@]}" down ;;
  logs) shift; "${COMPOSE[@]}" logs -f --tail 100 "$@" ;;
  ps)   "${COMPOSE[@]}" ps ;;
  *) echo "usage: $0 [up|down|logs [service]|ps]"; exit 1 ;;
esac
