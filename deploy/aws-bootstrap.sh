#!/bin/bash
# First-boot script for an Ubuntu 24.04 EC2 instance (passed as user-data).
# Installs Docker, pulls the repo, builds .env from SSM Parameter Store
# (/agent-observability/*), and starts the production stack.
# Log: /var/log/agent-observability-bootstrap.log
set -euxo pipefail
exec > /var/log/agent-observability-bootstrap.log 2>&1

REGION="us-east-2"
P="/agent-observability"
APP_DIR="/opt/agent-observability-demo"
REPO="https://github.com/mohcinemadkour/agent-observability-demo.git"
DASHBOARD_DOMAIN="dashboard.evaltrust.cloud"
DEMO_DOMAIN="demo.evaltrust.cloud"
OTLP_DOMAIN="otlp.evaltrust.cloud"
ACME_EMAIL="mohcine@evaltrust.cloud"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y -o Dpkg::Options::=--force-confold upgrade
apt-get install -y git unattended-upgrades ca-certificates curl
dpkg-reconfigure -f noninteractive unattended-upgrades

# 2 GB swap: headroom for a 4 GB instance
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Docker (official script) + AWS CLI
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
snap install aws-cli --classic
AWS=/snap/bin/aws

# no command tracing from here on: secrets must not reach the log
set +x

param() { "$AWS" ssm get-parameter --region "$REGION" --with-decryption --name "$P/$1" --query Parameter.Value --output text; }
GRAFANA_ADMIN_PASSWORD="$(param grafana_admin_password)"
OTLP_INGEST_TOKEN="$(param otlp_ingest_token)"
DEMO_PASSWORD="$(param demo_password)"

# Code
if [ ! -d "$APP_DIR/.git" ]; then git clone "$REPO" "$APP_DIR"; else git -C "$APP_DIR" pull; fi
cd "$APP_DIR"

DEMO_PASSWORD_HASH="$(docker run --rm caddy:2.8.4 caddy hash-password --plaintext "$DEMO_PASSWORD")"

umask 077
cat > .env <<EOF
DASHBOARD_DOMAIN=${DASHBOARD_DOMAIN}
DEMO_DOMAIN=${DEMO_DOMAIN}
OTLP_DOMAIN=${OTLP_DOMAIN}
ACME_EMAIL=${ACME_EMAIL}
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD}
DEMO_USER=demo
DEMO_PASSWORD_HASH='${DEMO_PASSWORD_HASH}'
OTLP_INGEST_TOKEN=${OTLP_INGEST_TOKEN}
RETENTION_DAYS=15
LLM_PROVIDER=mock
TRAFFIC=on
TRAFFIC_INTERVAL_S=4
EOF

./deploy/deploy.sh up
echo "BOOTSTRAP_DONE"
