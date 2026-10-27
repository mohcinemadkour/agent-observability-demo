"""Generates grafana/dashboards/agent-observability.json.

Kept as code so the dashboard is reviewable and easy to extend:
    python scripts/build_dashboard.py
"""
from __future__ import annotations

import json
from pathlib import Path

PROM = {"type": "prometheus", "uid": "prometheus"}
TEMPO = {"type": "tempo", "uid": "tempo"}
LOKI = {"type": "loki", "uid": "loki"}

_id = 0
panels: list[dict] = []
y = 0


def nid() -> int:
    global _id
    _id += 1
    return _id


def row(title: str) -> None:
    global y
    panels.append({"type": "row", "title": title, "id": nid(), "collapsed": False,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []})
    y += 1


def prom(expr: str, legend: str = "", exemplar: bool = False, ref: str = "A") -> dict:
    return {"datasource": PROM, "expr": expr, "legendFormat": legend or "__auto", "refId": ref,
            "exemplar": exemplar, "range": True}


def stat(title, expr, x, w, unit="none", decimals=None, steps=None, desc=""):
    p = {
        "type": "stat", "title": title, "id": nid(), "datasource": PROM, "description": desc,
        "gridPos": {"h": 4, "w": w, "x": x, "y": y},
        "targets": [prom(expr)],
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "background", "graphMode": "area", "textMode": "value", "justifyMode": "center"},
        "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "thresholds"},
                                     "thresholds": {"mode": "absolute", "steps": steps or [{"color": "blue", "value": None}]},
                                     "noValue": "0"}, "overrides": []},
    }
    if decimals is not None:
        p["fieldConfig"]["defaults"]["decimals"] = decimals
    panels.append(p)


def timeseries(title, targets, x, w, h=8, unit="none", stack=False, desc="", draw="line", overrides=None, minv=None, maxv=None):
    defaults = {"unit": unit, "color": {"mode": "palette-classic"},
                "custom": {"drawStyle": draw, "lineWidth": 2, "fillOpacity": 25 if stack else 8,
                           "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                           "showPoints": "never", "spanNulls": True, "axisSoftMin": 0}}
    if minv is not None:
        defaults["min"] = minv
    if maxv is not None:
        defaults["max"] = maxv
    panels.append({
        "type": "timeseries", "title": title, "id": nid(), "datasource": PROM, "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y}, "targets": targets,
        "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
        "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
    })


def color_override(name: str, color: str) -> dict:
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}]}


RED_AMBER_GREEN = [{"color": "red", "value": None}, {"color": "orange", "value": 0.8}, {"color": "green", "value": 0.95}]

# ------------------------------------------------------------------ row 1
row("Is it up?  Agent health at a glance")
stat("Agent runs / min", "sum(rate(agent_runs_total[1m])) * 60", 0, 4, decimals=1)
stat("Run success rate (5m)",
     'sum(rate(agent_runs_total{outcome="success"}[5m])) / sum(rate(agent_runs_total[5m]))',
     4, 4, unit="percentunit", decimals=1, steps=RED_AMBER_GREEN,
     desc="Runs that finished without a tool error, loop or crash. NOT the same as correct.")
stat("Eval pass rate (5m)",
     'sum(rate(agent_evaluations_total{verdict="pass"}[5m])) / sum(rate(agent_evaluations_total[5m]))',
     8, 4, unit="percentunit", decimals=1, steps=RED_AMBER_GREEN,
     desc="Runs whose answer passed every online evaluation check.")
stat("p95 run latency (5m)",
     "histogram_quantile(0.95, sum by (le) (rate(agent_run_duration_seconds_bucket[5m])))",
     12, 4, unit="s", decimals=2,
     steps=[{"color": "green", "value": None}, {"color": "orange", "value": 4}, {"color": "red", "value": 8}])
stat("Loops detected (15m)", "sum(increase(agent_loop_detected_total[15m]))", 16, 4, decimals=0,
     steps=[{"color": "green", "value": None}, {"color": "red", "value": 1}])
stat("Tokens / min", "sum(rate(gen_ai_client_token_usage_sum[1m])) * 60", 20, 4, unit="short", decimals=0)
y += 4

# ------------------------------------------------------------------ row 2
row("Green is not the same as correct")
timeseries(
    "Run success vs. evaluation pass rate",
    [prom('sum(rate(agent_runs_total{outcome="success"}[2m])) / sum(rate(agent_runs_total[2m]))', "run success (no errors)", ref="A"),
     prom('sum(rate(agent_evaluations_total{verdict="pass"}[2m])) / sum(rate(agent_evaluations_total[2m]))', "evaluation pass", ref="B")],
    0, 14, unit="percentunit", minv=0, maxv=1,
    desc="The gap between these lines is the silent-failure zone: runs that look healthy to tracing but gave a wrong or unsafe answer.",
    overrides=[color_override("run success (no errors)", "blue"), color_override("evaluation pass", "orange")],
)
panels.append({
    "type": "bargauge", "title": "Failed evaluation checks (15m)", "id": nid(), "datasource": PROM,
    "gridPos": {"h": 8, "w": 10, "x": 14, "y": y},
    "targets": [prom("sum by (check) (increase(agent_eval_check_failures_total[15m]))", "{{check}}")],
    "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
    "fieldConfig": {"defaults": {"unit": "none", "decimals": 0, "min": 0, "color": {"mode": "continuous-YlRd"}}, "overrides": []},
})
y += 8

# ------------------------------------------------------------------ row 3
row("What is failing?")
timeseries("Runs by outcome (per min)", [prom("sum by (outcome) (rate(agent_runs_total[1m])) * 60", "{{outcome}}")],
           0, 12, stack=True, draw="bars",
           overrides=[color_override("success", "green"), color_override("tool_error", "red"),
                      color_override("loop_detected", "purple"), color_override("llm_error", "dark-red"),
                      color_override("max_iterations", "orange")])
timeseries("Tool error rate by tool",
           [prom('sum by (gen_ai_tool_name) (rate(agent_tool_calls_total{status="error"}[2m])) / '
                 'sum by (gen_ai_tool_name) (rate(agent_tool_calls_total[2m]))', "{{gen_ai_tool_name}}")],
           12, 12, unit="percentunit", minv=0)
y += 8

# ------------------------------------------------------------------ row 4
row("Where does the time go?")
timeseries("p95 latency per step (from spanmetrics) - click a dot to open the trace",
           [prom("histogram_quantile(0.95, sum by (le, span_name) "
                 "(rate(traces_span_metrics_duration_milliseconds_bucket[2m])))", "{{span_name}}", exemplar=True)],
           0, 16, unit="ms",
           desc="Derived by the Collector's spanmetrics connector from traces alone; no metric code in the agent.")
timeseries("Reasoning iterations per run (p95)",
           [prom("histogram_quantile(0.95, sum by (le, demo_scenario) (rate(agent_iterations_bucket[5m])))", "{{demo_scenario}}")],
           16, 8, desc="A climbing iteration count is the early warning sign of a loop.")
y += 8

# ------------------------------------------------------------------ row 5
row("LLM cost and latency")
timeseries("Tokens per minute by type and model",
           [prom("sum by (gen_ai_token_type, gen_ai_request_model) (rate(gen_ai_client_token_usage_sum[1m])) * 60",
                 "{{gen_ai_request_model}} {{gen_ai_token_type}}")], 0, 12, unit="short", stack=True)
timeseries("LLM call latency p50 / p95",
           [prom("histogram_quantile(0.5, sum by (le, gen_ai_request_model) (rate(gen_ai_client_operation_duration_seconds_bucket[2m])))",
                 "p50 {{gen_ai_request_model}}", ref="A"),
            prom("histogram_quantile(0.95, sum by (le, gen_ai_request_model) (rate(gen_ai_client_operation_duration_seconds_bucket[2m])))",
                 "p95 {{gen_ai_request_model}}", ref="B")], 12, 12, unit="s")
y += 8

# ------------------------------------------------------------------ row 6
row("Drill down: traces and logs")
panels.append({
    "type": "table", "title": "Failed or wrong runs (click a trace ID)", "id": nid(), "datasource": TEMPO,
    "gridPos": {"h": 11, "w": 12, "x": 0, "y": y},
    "targets": [{"datasource": TEMPO, "refId": "A", "queryType": "traceql", "limit": 20, "tableType": "traces",
                 "query": '{ name = "invoke_agent maintenance-agent" && (status = error || span.eval.passed = false) }'}],
    "options": {"showHeader": True, "cellHeight": "sm"},
    "fieldConfig": {"defaults": {}, "overrides": []},
})
panels.append({
    "type": "logs", "title": "Agent logs (trace_id links to Tempo)", "id": nid(), "datasource": LOKI,
    "gridPos": {"h": 11, "w": 12, "x": 12, "y": y},
    "targets": [{"datasource": LOKI, "refId": "A", "expr": '{service_name="maintenance-agent"}', "queryType": "range"}],
    "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True,
                "prettifyLogMessage": False, "dedupStrategy": "none", "showCommonLabels": False, "showLabels": False},
})
y += 11

dashboard = {
    "uid": "agent-observability",
    "title": "AI Agent Observability",
    "description": "LangChain agent -> OpenTelemetry -> Collector -> Prometheus / Tempo / Loki",
    "tags": ["ai-agents", "opentelemetry", "langchain"],
    "timezone": "browser",
    "editable": True,
    "graphTooltip": 1,
    "refresh": "5s",
    "time": {"from": "now-15m", "to": "now"},
    "schemaVersion": 39,
    "version": 1,
    "templating": {"list": []},
    "annotations": {"list": []},
    "links": [],
    "panels": panels,
}

out = Path(__file__).resolve().parents[1] / "grafana" / "dashboards" / "agent-observability.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(dashboard, indent=2) + "\n")
print(f"wrote {out} ({len(panels)} panels)")
