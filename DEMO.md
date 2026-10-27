# Live Demo Runbook: "Green Is Not Correct"

A 10-minute demo (with a 5-minute cut) for the Prompt Engineers AI meetup on agent observability. It complements the talk's stack (LangChain + OpenTelemetry + Collector + Prometheus/Tempo/Loki/Grafana) and adds one idea the talk doesn't cover: **tracing tells you what the agent did; evaluation tells you whether it was right.**

## Before you leave home (the night before)

- [ ] `make up && make smoke` → **ALL CHECKS PASSED**. Venue Wi-Fi is not where you want to pull 1.5 GB of images.
- [ ] `docker compose --profile jaeger pull` (so the Jaeger step works offline).
- [ ] Keep `LLM_PROVIDER=mock`. It is deterministic and offline. Mention that a real model is one env var away.
- [ ] Browser tabs, in this order: (1) control panel `localhost:8000`, (2) Grafana dashboard, (3) `collector/config.yaml` in your editor, (4) `agent/app/otel_langchain.py`.
- [ ] Grafana: set the browser zoom to 125% for the projector and the time range to *Last 15 minutes*.
- [ ] Start traffic 10+ minutes before you present so every panel has history.

**Fallback:** if Docker misbehaves on stage, `make test` runs Docker-free and prints the span tree checks. Keep a screen recording of the full demo as a last resort.

## The 10-minute flow

### 1. The setup (1 min), dashboard tab
> "A maintenance agent for a machining plant: it reads CNC sensor data and opens proactive service orders. Instrumented with OpenTelemetry through LangChain callbacks, everything goes through one Collector, and the agent only knows one URL: the Collector's."

Point at the top row: runs/min, success rate, eval pass rate, p95 latency, loops, tokens.

### 2. A failing tool (1.5 min), control panel → **Tool failure**
> "Sensor gateway returns 503."

Click **Open trace in Grafana**: the red `execute_tool get_sensor_readings` span has the exception event. Click the span's logs link: the ERROR log has the same `trace_id`. *"Metric → trace → log, one click each, because the Collector and Grafana are wired for correlation."*

### 3. The slow dependency (1 min), **Slow dependency**
Dashboard → *p95 latency per step*: `search_maintenance_manual` spikes. Click an exemplar dot to land on the exact slow trace. *"I didn't write a single latency metric for this. The Collector's spanmetrics connector derived it from traces."*

### 4. The loop (1.5 min), **Stuck in a loop**
> "The tool says 'incomplete, retry.' The agent obeys, forever, which in production means burning tokens on a loop that never alerts."

Show the trace: same tool called 3×, then the `agent.loop_detected` event. Dashboard: loop counter and iterations panel. *"A loop is not an exception. You have to decide what a loop is and emit it."*

### 5. The punchline (2.5 min), **Silent wrong answer**
> "Now the scary one."

Open the trace. **Every span is green.** The agent read the sensors (vibration 11.8 mm/s, ISO zone D, critical) and answered: *"looks fine, no action needed."*
> "No error, no timeout, no loop. Tracing says this run was perfect. It wasn't. A spindle bearing is about to fail and nobody opened a work order."

Show the `evaluate maintenance-agent` span: `grounded_in_evidence` ❌ and `required_action_taken` ❌. Then go to the dashboard panel **Run success vs. evaluation pass rate**:
> "The gap between these two lines is your blind spot. If you only watch the blue line, you're shipping wrong answers with a 100% success rate."

### 6. Vendor neutral + governance (1.5 min), editor → `collector/config.yaml`
Click **PII in the prompt**. Open the trace: the email and phone appear as `<redacted:email>` and `<redacted:phone>`. Show the `transform/redact` block:
> "Redaction happens in the Collector. Every app behind it gets it for free, and no raw PII ever reaches a backend. That matters in healthcare and finance."

Run `make backend-jaeger`, open Jaeger at `localhost:16686`: same traces, second backend.
> "I changed nothing in the agent. Swapping Tempo for Splunk, Honeycomb or Datadog is an overlay file."

### 7. Close (30 s)
> "Observability answers *what happened*. Evaluation answers *was it right*. Put both on the same pipeline and you see the failures that never throw an error. The repo is open: clone it, `make up`, break it."

## 5-minute cut
Steps 1 → 2 (trace + log link only) → 5 (full) → 6 (redaction only) → 7.

## Likely questions

- **Why a custom callback instead of OpenLLMetry/OpenInference?** It's ~150 lines, so you can see exactly what instrumentation means. Those libraries emit similar `gen_ai.*` attributes, and the Collector and dashboards don't care which one you use.
- **Isn't capturing prompts risky?** Yes. Content capture is off with one env var (`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false`), and the Collector redacts anything that slips through.
- **How do evals scale beyond regex checks?** The same `gen_ai.evaluation.result` events can carry LLM-as-judge or human scores. You can sample a percentage of traces for expensive judges and keep the cheap deterministic checks on every run.
- **Cost?** The tokens panel comes from `gen_ai.client.token.usage`; multiply by price per model in a recording rule.
