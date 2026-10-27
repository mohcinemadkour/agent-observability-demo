"""Verify the telemetry captured by fake_otlp_receiver.py.

    python tests/check_otlp.py /tmp/otlp

Checks span structure, GenAI attributes, error statuses, evaluation events, metric
names (as Prometheus will see them), log/trace correlation, and that the
Collector's redaction regexes remove PII without touching other attributes.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/otlp")
fail = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global fail
    fail += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  -> {detail}"))


def attrs(obj) -> dict:
    res = {}
    for a in obj.get("attributes", []):
        v = a["value"]
        if "arrayValue" in v:
            res[a["key"]] = [list(x.values())[0] for x in v["arrayValue"].get("values", [])]
        else:
            res[a["key"]] = list(v.values())[0] if v else None
    return res


def lines(name):
    p = out / f"{name}.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


# ------------------------------------------------------------------ traces
spans = []
for req in lines("traces"):
    for rs in req.get("resourceSpans", []):
        res = attrs(rs["resource"])
        for ss in rs["scopeSpans"]:
            for s in ss["spans"]:
                s["_attrs"] = attrs(s)
                s["_res"] = res
                spans.append(s)

by_trace = defaultdict(list)
for s in spans:
    by_trace[s["traceId"]].append(s)

print(f"Traces: {len(by_trace)} traces / {len(spans)} spans")
ids_all = {s["spanId"]: s for s in spans}
roots = [s for s in spans if s["name"] == "invoke_agent maintenance-agent"]
check(bool(roots), "invoke_agent root spans exist")
check(all(not r.get("parentSpanId") or ids_all.get(r["parentSpanId"], {}).get("kind") == "SPAN_KIND_SERVER" for r in roots),
      "invoke_agent is the root, or a child of the HTTP server span")
check(all(s["_res"].get("service.name") == "maintenance-agent" for s in spans), "service.name resource attribute")

ids = ids_all
by_scenario = {}
for r in roots:
    sc = r["_attrs"]["demo.scenario"]
    by_scenario.setdefault(sc, r)

for sc, root in sorted(by_scenario.items()):
    kids = [s for s in by_trace[root["traceId"]] if s is not root]
    orphans = [s["name"] for s in kids if s.get("parentSpanId") and s["parentSpanId"] not in ids]
    names = [s["name"] for s in kids]
    status = root.get("status", {}).get("code", "STATUS_CODE_UNSET")
    print(f"  [{sc}] status={status} outcome={root['_attrs'].get('agent.outcome')} eval={root['_attrs'].get('eval.passed')} children={len(kids)}")
    check(not orphans, f"[{sc}] every span has its parent in the trace", str(orphans))
    check(any(n.startswith("chat ") for n in names), f"[{sc}] has LLM spans")
    check("evaluate maintenance-agent" in names, f"[{sc}] has evaluation span")
    ev = next(s for s in kids if s["name"] == "evaluate maintenance-agent")
    check(any(e["name"] == "gen_ai.evaluation.result" for e in ev.get("events", [])), f"[{sc}] evaluation events emitted")

expect_err = {"tool_error", "loop"}
for sc, root in by_scenario.items():
    is_err = root.get("status", {}).get("code") == "STATUS_CODE_ERROR"
    check(is_err == (sc in expect_err), f"[{sc}] agent span error status = {sc in expect_err}")

chat = next(s for s in spans if s["name"].startswith("chat "))
for k in ["gen_ai.operation.name", "gen_ai.provider.name", "gen_ai.request.model", "gen_ai.usage.input_tokens",
          "gen_ai.usage.output_tokens", "gen_ai.response.finish_reasons", "gen_ai.input.messages"]:
    check(k in chat["_attrs"], f"chat span has {k}")
check(chat.get("kind") == "SPAN_KIND_CLIENT", "chat span kind = CLIENT")
tool = next(s for s in spans if s["name"].startswith("execute_tool"))
for k in ["gen_ai.tool.name", "gen_ai.tool.call.id", "gen_ai.tool.call.arguments", "gen_ai.tool.call.result"]:
    check(k in tool["_attrs"], f"tool span has {k}")
err_tool = [s for s in spans if s["name"] == "execute_tool get_sensor_readings" and s.get("status", {}).get("code") == "STATUS_CODE_ERROR"]
check(bool(err_tool) and any(e["name"] == "exception" for e in err_tool[0].get("events", [])), "failed tool span has exception event")
loop_root = by_scenario.get("loop")
check(loop_root is not None and any(e["name"] == "agent.loop_detected" for e in loop_root.get("events", [])), "loop run has agent.loop_detected event")
bad = by_scenario.get("bad_answer")
check(bad is not None and bad.get("status", {}).get("code") != "STATUS_CODE_ERROR" and bad["_attrs"]["eval.passed"] is False,
      "bad_answer: green trace, failed evaluation (the silent failure)")

# TraceQL used in the dashboard filters on these
check(all("eval.passed" in r["_attrs"] for r in roots), "every agent span carries eval.passed (dashboard TraceQL)")

# ------------------------------------------------------------- redaction
print("Collector redaction regexes (Python equivalent of the OTTL statements)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(\+?\d{1,3}[ .-])?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}")


def redact(v: str) -> str:
    return PHONE.sub("<redacted:phone>", EMAIL.sub("<redacted:email>", v))


pii_spans = by_trace[by_scenario["pii"]["traceId"]]
blob_before = json.dumps([s["_attrs"] for s in pii_spans])
check("john.doe@acme-plant.com" in blob_before, "PII really is present before the Collector")
after = [{k: redact(v) if isinstance(v, str) else v for k, v in s["_attrs"].items()} for s in pii_spans]
blob_after = json.dumps(after)
check("john.doe@acme-plant.com" not in blob_after and "214-555-0199" not in blob_after, "email + phone removed")
false_pos = [(s["name"], k, v[:80]) for s in spans if s["_attrs"].get("demo.scenario") != "pii" and s["traceId"] != by_scenario["pii"]["traceId"]
             for k, v in s["_attrs"].items() if isinstance(v, str) and redact(v) != v]
check(not false_pos, "no false-positive redactions in non-PII traces", str(false_pos[:3]))

# ------------------------------------------------------------------ metrics
print("Metrics (names as the Collector's Prometheus exporter will expose them)")
metrics = {}
for req in lines("metrics"):
    for rm in req.get("resourceMetrics", []):
        for sm in rm["scopeMetrics"]:
            for m in sm["metrics"]:
                metrics[m["name"]] = m


def prom_name(m) -> list[str]:
    base = m["name"].replace(".", "_")
    unit = m.get("unit", "")
    unit = re.sub(r"\{.*?\}", "", unit)
    suffix = {"s": "seconds", "ms": "milliseconds", "By": "bytes"}.get(unit, unit)
    if suffix and not base.endswith(suffix):
        base += "_" + suffix
    if "sum" in m and m["sum"].get("isMonotonic"):
        return [base + "_total"]
    if "histogram" in m:
        return [base + "_bucket", base + "_sum", base + "_count"]
    return [base]


dash = (Path(__file__).resolve().parents[1] / "grafana" / "dashboards" / "agent-observability.json").read_text()
smoke = (Path(__file__).resolve().parents[1] / "scripts" / "smoke_test.py").read_text()
exposed = set()
label_keys = set()
for m in metrics.values():
    names = prom_name(m)
    exposed.update(names)
    pts = (m.get("sum") or m.get("histogram") or {}).get("dataPoints", [])
    for p in pts:
        label_keys.update(a["key"].replace(".", "_") for a in p.get("attributes", []))
    temporality = (m.get("sum") or m.get("histogram") or {}).get("aggregationTemporality")
    print(f"    {m['name']:36} unit={m.get('unit', '') or '-':8} -> {names[0]}  ({temporality})")

used = set(re.findall(r"\b((?:agent|gen_ai_client)_[a-z_]+)\b", dash)) | set(re.findall(r"\"((?:agent|gen_ai_client)_[a-z_]+)\"", smoke))
missing = sorted(u for u in used if u not in exposed)
check(not missing, "every app metric referenced by dashboard/smoke test is exported", str(missing))
for lbl in ["outcome", "demo_scenario", "gen_ai_tool_name", "status", "verdict", "check", "gen_ai_token_type", "gen_ai_request_model"]:
    check(lbl in label_keys, f"label '{lbl}' present")

# ------------------------------------------------------------------ logs
print("Logs")
records = []
for req in lines("logs"):
    for rl in req.get("resourceLogs", []):
        for sl in rl["scopeLogs"]:
            records.extend(sl["logRecords"])
with_trace = [r for r in records if r.get("traceId")]
check(len(records) > 0, f"{len(records)} log records exported")
check(len(with_trace) / max(1, len(records)) > 0.9, f"{len(with_trace)}/{len(records)} logs carry trace_id")
sev = {r.get("severityText") for r in records}
check({"INFO", "WARN", "ERROR"} & sev or {"WARNING"} & sev, f"severity texts present {sorted(x for x in sev if x)}")
tool_logs = [r for r in records if "asset.id" in attrs(r)]
check(bool(tool_logs), "structured log attributes (asset.id) preserved")
log_span_ids = {r.get("spanId") for r in with_trace}
tool_span_ids = {s["spanId"] for s in spans if s["name"].startswith("execute_tool")}
check(bool(log_span_ids & tool_span_ids), "logs emitted inside tools are linked to the tool span")

print()
print("ALL CHECKS PASSED" if not fail else f"{fail} CHECK(S) FAILED")
sys.exit(1 if fail else 0)
