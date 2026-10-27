#!/usr/bin/env python3
"""End-to-end check of the running stack (stdlib only).  Usage:  make smoke

Runs every scenario, then verifies that metrics reached Prometheus, traces reached
Tempo, logs reached Loki, PII was redacted by the Collector, and the dashboard exists.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request

AGENT = "http://localhost:8000"
GRAFANA = "http://localhost:" + os.getenv("GRAFANA_PORT", "3001")
PROM = "http://localhost:9090"

EXPECTED = {
    "healthy": ("success", True),
    "slow_tool": ("success", True),
    "tool_error": ("tool_error", False),
    "loop": ("loop_detected", False),
    "bad_answer": ("success", False),
    "pii": ("success", False),
}
METRICS = [
    "agent_runs_total", "agent_run_duration_seconds_bucket", "agent_tool_calls_total",
    "agent_evaluations_total", "agent_eval_check_failures_total", "agent_loop_detected_total",
    "agent_iterations_bucket", "gen_ai_client_token_usage_bucket", "gen_ai_client_operation_duration_seconds_bucket",
    "traces_span_metrics_calls_total", "traces_span_metrics_duration_milliseconds_bucket",
]

failures = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global failures
    failures += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail and not ok else ""))


def get(url: str, timeout: float = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def post(url: str, body: dict, timeout: float = 60) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def wait_for(url: str, seconds: int = 120) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=3)
            return True
        except Exception:
            time.sleep(2)
    return False


print("1. Services up")
for name, url in [("agent", f"{AGENT}/healthz"), ("grafana", f"{GRAFANA}/api/health"), ("prometheus", f"{PROM}/-/ready")]:
    check(wait_for(url), name)
if failures:
    sys.exit("Stack is not up. Run `make up` and wait ~30s.")

print("2. Scenarios behave as expected")
results = {}
for scenario, (outcome, passed) in EXPECTED.items():
    r = post(f"{AGENT}/api/run", {"scenario": scenario})
    results[scenario] = r
    check(r["outcome"] == outcome and r["evaluation"]["passed"] == passed, scenario,
          f"got outcome={r['outcome']} eval_passed={r['evaluation']['passed']}")

print("3. Waiting 20s for export + scrape ...")
time.sleep(20)

print("4. Metrics in Prometheus")
for m in METRICS:
    data = get(f"{PROM}/api/v1/query?query=" + urllib.parse.quote(f"count({m})"))
    check(bool(data["data"]["result"]), m, "no series")

print("5. Traces in Tempo (via Grafana)")
pii_trace = results["pii"]["trace_id"]
try:
    trace = get(f"{GRAFANA}/api/datasources/proxy/uid/tempo/api/traces/{pii_trace}")
    blob = json.dumps(trace)
    check("invoke_agent maintenance-agent" in blob, "agent trace stored")
    check("execute_tool get_sensor_readings" in blob and "chat " in blob, "LLM + tool spans present")
    check("john.doe@acme-plant.com" not in blob and "<redacted:email>" in blob, "Collector redacted the email")
    check("214-555-0199" not in blob, "Collector redacted the phone number")
except Exception as exc:
    check(False, "fetch trace from Tempo", str(exc))

print("6. Logs in Loki (via Grafana)")
try:
    q = urllib.parse.urlencode({"query": '{service_name="maintenance-agent"}', "limit": 50,
                                "start": str(int((time.time() - 600) * 1e9))})
    logs = get(f"{GRAFANA}/api/datasources/proxy/uid/loki/loki/api/v1/query_range?{q}")
    streams = logs["data"]["result"]
    check(bool(streams), "agent logs stored")
    check("trace_id" in json.dumps(streams) or any("trace_id" in s.get("stream", {}) for s in streams),
          "logs carry trace_id", "query_range did not return structured metadata; check in Grafana Explore")
except Exception as exc:
    check(False, "query Loki", str(exc))

print("7. Dashboard provisioned")
try:
    d = get(f"{GRAFANA}/api/dashboards/uid/agent-observability")
    check(d["dashboard"]["title"] == "AI Agent Observability", "dashboard 'AI Agent Observability'")
except Exception as exc:
    check(False, "dashboard", str(exc))

print()
print("ALL CHECKS PASSED" if not failures else f"{failures} CHECK(S) FAILED")
print(f"Dashboard: {GRAFANA}/d/agent-observability   Control panel: {AGENT}")
sys.exit(1 if failures else 0)
