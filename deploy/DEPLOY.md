# Deploying the dashboard online, securely

This puts the stack on a public server so clients can log in and see the live dashboard over HTTPS. Only one container, the Caddy web server, is exposed to the internet; everything else stays on a private Docker network.

```
                Internet (HTTPS only, ports 80/443)
                              │
                       ┌──────┴──────┐  automatic Let's Encrypt certificates
                       │    Caddy    │  security headers, password for the control panel
                       └──┬────┬───┬─┘
   dashboard.<domain> ────┘    │   └──── otlp.<domain>  (bearer token required)
   Grafana: login required     │         → Collector :4319 → same pipeline
   clients = read-only Viewers │
                    demo.<domain>: control panel (password)
   ─────────────── private Docker network (not reachable from outside) ───────────────
   agent · otel-collector · prometheus · tempo · loki · grafana
```

| Exposed | Protected by |
|---|---|
| `https://dashboard.<domain>` | Grafana login. You are admin; clients get Viewer accounts (read-only). |
| `https://demo.<domain>` | Username and password (Caddy basic auth). |
| `https://otlp.<domain>/v1/*` | Bearer token checked by the Collector. Optional, for clients' own agents. |
| Everything else | Not exposed. Prometheus, Tempo, Loki, the Collector's internal ports and the agent API have no public port. |

## 1. Get a server

Any Linux VPS with Docker works (Hetzner, DigitalOcean, OVH, AWS Lightsail, an Azure VM...). For this demo: **2 vCPU, 4 GB RAM, 40 GB disk**, Ubuntu 24.04. Check current prices on the provider's site.

Install Docker (Compose v2.24.4 or newer is required):
```bash
curl -fsSL https://get.docker.com | sh
docker compose version
```

Open only SSH, HTTP and HTTPS:
```bash
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw allow 443/udp
sudo ufw enable
```
Note: Docker publishes ports by editing iptables directly, which can bypass ufw. That is why the production overlay removes every published port except Caddy's 80/443. Also apply the same rule in your provider's cloud firewall if it has one.

## 2. Point DNS at the server

At your DNS provider, create three **A records** pointing to the server's public IP:

| Name | Example |
|---|---|
| dashboard | `dashboard.evaltrust.cloud` |
| demo | `demo.evaltrust.cloud` |
| otlp | `otlp.evaltrust.cloud` |

Wait until `nslookup dashboard.evaltrust.cloud` returns the server IP. Caddy can only get certificates after that.

## 3. Copy the code and configure

```bash
git clone https://github.com/<you>/agent-observability-demo.git   # or scp the zip and unzip it
cd agent-observability-demo
cp deploy/.env.prod.example .env
```

Fill in `.env`:
```bash
openssl rand -base64 24     # → GRAFANA_ADMIN_PASSWORD
openssl rand -hex 32        # → OTLP_INGEST_TOKEN
docker run --rm caddy:2.8.4 caddy hash-password --plaintext 'pick-a-demo-password'   # → DEMO_PASSWORD_HASH
```
Paste the hash **inside single quotes**: `DEMO_PASSWORD_HASH='$2a$14$...'`. Without the quotes, the `$` signs get mangled.

## 4. Deploy

```bash
./deploy/deploy.sh          # validates .env, builds, starts, prints the URLs
./deploy/deploy.sh logs caddy   # watch certificates being issued (first start only)
```

Open `https://dashboard.<domain>` and log in as admin. The dashboard is the home page.

Updating later: `git pull && ./deploy/deploy.sh`. Stopping: `./deploy/deploy.sh down` (data is kept in Docker volumes).

## 5. Give clients access

```bash
./deploy/add_client_viewer.sh "Jane Doe" jane@client.com
```
It prints a generated password. Send the URL and login by email and the password by a different channel (Signal, phone).

What a Viewer can do: see every dashboard, use Explore to open traces and query logs, change time ranges. They cannot save changes, add data sources or manage users. To revoke access: Grafana → Administration → Users → delete.

To show the live failure scenarios during a call, share `https://demo.<domain>` with the demo password, or keep it to yourself and click the scenarios while screen-sharing.

## 6. Optional: receive telemetry from a client's own agent

Any OpenTelemetry SDK can send to the protected endpoint with three environment variables:
```bash
OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp.evaltrust.cloud
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer%20<OTLP_INGEST_TOKEN>
OTEL_RESOURCE_ATTRIBUTES=service.name=client-agent,client.id=acme
```

Check the endpoint from anywhere:
```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://otlp.evaltrust.cloud/v1/traces \
  -H "Content-Type: application/json" -d '{}'                                   # 401: token required
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://otlp.evaltrust.cloud/v1/traces \
  -H "Content-Type: application/json" -H "Authorization: Bearer <token>" -d '{}'  # 200
```

What shows up for a client's agent:
- **Immediately:** traces in Tempo, logs in Loki, and the per-step latency and error metrics the Collector derives from spans (spanmetrics).
- **The agent-specific panels** (runs, outcomes, loops, evaluations) need the same instrumentation as the demo agent: `otel_langchain.py` plus the run and evaluation metrics in `agent.py` and `evaluator.py`.

One token is shared by everyone who sends data. For several clients, give each a separate deployment, or at minimum a separate `client.id` and its own dashboard filtered on it. Data from different clients should not share a Grafana organization they can all see.

## 7. Security checklist

- [ ] Every `change-me` replaced; admin password is long and random.
- [ ] `docker compose -f docker-compose.yaml -f docker-compose.prod.yaml ps` shows published ports only on `caddy`.
- [ ] `https://dashboard.<domain>` asks for a login in a private browser window.
- [ ] `https://demo.<domain>` asks for a password.
- [ ] Port 9090, 3100, 3200, 4317, 4318 and 8889 are **not** reachable from your laptop (`curl http://<server-ip>:9090` times out).
- [ ] Prompt and answer capture: set `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false` on the agent when it handles real client data. PII redaction in the Collector stays on regardless.
- [ ] Backups: the volumes `grafana-data`, `prometheus-data`, `loki-data` and `tempo-data` hold all state. Snapshot the server or back up `/var/lib/docker/volumes`.
- [ ] OS updates: `sudo apt update && sudo apt upgrade` monthly; `unattended-upgrades` for security patches.

## Alternative: deploy with Coolify

If you run Coolify on a server, create a **Docker Compose** resource from the GitHub repo and set the compose files to `docker-compose.yaml` + `docker-compose.prod.yaml`, minus the `caddy` service: Coolify's own proxy (Traefik) then handles HTTPS. In Coolify, assign the domains to the `grafana` service (port 3000) and the `agent` service (port 8000), add basic auth on the agent route, and paste the same environment variables. The Grafana login and Viewer-account setup are identical.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Browser says certificate invalid | DNS not pointing at the server yet, or port 80/443 blocked. Check `./deploy/deploy.sh logs caddy`. |
| `deploy.sh` stops with “set X in .env” | A required value is missing. |
| `!reset` error | Docker Compose is older than v2.24.4. Upgrade Docker. |
| Grafana login fails right after changing the admin password in `.env` | The password is only applied when the `grafana-data` volume is first created. Change it in Grafana (Profile → Change password) or run `docker compose exec grafana grafana cli admin reset-admin-password <new>`. |
| Collector restarting | `./deploy/deploy.sh logs otel-collector`; usually an empty `OTLP_INGEST_TOKEN`. |
