.PHONY: test up down reset logs smoke open scenario traffic-on traffic-off backend-default backend-jaeger backend-vendor dashboard run-local

up:            ## start everything (offline mock LLM by default)
	@test -f .env || cp .env.example .env
	docker compose up -d --build
	@echo "Control panel: http://localhost:8000   Grafana: http://localhost:$${GRAFANA_PORT:-3001}/d/agent-observability"

down:          ## stop everything
	docker compose --profile jaeger down

reset:         ## stop and wipe stored traces/metrics
	docker compose --profile jaeger down -v

logs:
	docker compose logs -f agent otel-collector

smoke:         ## end-to-end check of the running stack
	python3 scripts/smoke_test.py

test:          ## check the agent's telemetry without Docker (needs agent/requirements.txt installed)
	bash tests/run_telemetry_check.sh

scenario:      ## make scenario S=loop   (healthy|tool_error|slow_tool|loop|bad_answer|pii)
	@curl -s -X POST localhost:8000/api/run -H 'Content-Type: application/json' -d '{"scenario":"$(or $(S),healthy)"}' | python3 -m json.tool

traffic-on:
	@curl -s -X POST localhost:8000/api/traffic/start; echo
traffic-off:
	@curl -s -X POST localhost:8000/api/traffic/stop; echo

# --- vendor-neutral demo: change backends at the Collector, never in the agent
backend-default:
	COLLECTOR_OVERLAY=none docker compose up -d otel-collector
	docker compose --profile jaeger stop jaeger

backend-jaeger:  ## add Jaeger as a second trace backend
	COLLECTOR_OVERLAY=jaeger docker compose --profile jaeger up -d jaeger otel-collector
	@echo "Jaeger UI: http://localhost:16686  (agent container was not touched)"

backend-vendor:  ## also ship traces to the SaaS configured in .env
	COLLECTOR_OVERLAY=vendor docker compose up -d otel-collector

dashboard:     ## regenerate the Grafana dashboard JSON from code
	python3 scripts/build_dashboard.py

run-local:     ## run the agent on your machine (stack still in Docker)
	cd agent && pip install -r requirements.txt && \
	OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uvicorn app.main:app --port 8001 --reload
