#!/usr/bin/env bash
# Verifies the agent's telemetry WITHOUT Docker: starts a fake OTLP receiver,
# runs the agent against it, triggers every scenario, then checks the payloads.
#   make test
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="$(mktemp -d)"
RECV_PORT=14318
APP_PORT=18000

python3 tests/fake_otlp_receiver.py --port "$RECV_PORT" --out "$OUT" &
RECV_PID=$!
(cd agent && exec env OTEL_EXPORTER_OTLP_ENDPOINT="http://127.0.0.1:$RECV_PORT" TRAFFIC=off METRIC_EXPORT_MS=1000 \
  DEMO_SLOW_SECONDS=1 LLM_PROVIDER=mock python3 -m uvicorn app.main:app --port "$APP_PORT" --log-level warning) &
APP_PID=$!
cleanup() {
  local rc=$?; set +e
  kill "$APP_PID" 2>/dev/null; wait "$APP_PID" 2>/dev/null
  kill "$RECV_PID" 2>/dev/null; rm -rf "$OUT"
  exit "$rc"
}
trap cleanup EXIT

for _ in $(seq 1 30); do curl -sf "localhost:$APP_PORT/healthz" >/dev/null && break; sleep 1; done

for s in healthy tool_error slow_tool loop bad_answer pii; do
  curl -sf -X POST "localhost:$APP_PORT/api/run" -H 'Content-Type: application/json' -d "{\"scenario\":\"$s\"}" \
    | python3 -c "import sys,json; r=json.load(sys.stdin); print(f\"  ran {r['scenario']:11} -> {r['outcome']:14} eval={r['evaluation']['score']}\")"
done
sleep 4  # let batch processors flush

python3 tests/check_otlp.py "$OUT"
