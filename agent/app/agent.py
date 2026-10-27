"""The agent loop: LangChain chat model + tools, wrapped in an `invoke_agent` span.

The loop is explicit (no framework magic) so every place an agent can go wrong
is visible: LLM call, tool call, tool error, loop, iteration cap, evaluation.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections import Counter
from urllib.parse import quote

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from . import scenarios
from .evaluator import evaluate_run, record_evaluation
from .llm import get_chat_model
from .otel_langchain import OTelCallbackHandler
from .telemetry import iterations_hist, loop_counter, run_counter, run_duration, tracer
from .tools import TOOLS, TOOLS_BY_NAME

log = logging.getLogger("agent.loop")

MAX_ITERATIONS = int(os.getenv("AGENT_MAX_ITERATIONS", "10"))
LOOP_LIMIT = int(os.getenv("AGENT_LOOP_LIMIT", "3"))  # identical tool calls allowed before we call it a loop
GRAFANA_URL = os.getenv("GRAFANA_PUBLIC_URL", "http://localhost:3001")

SYSTEM_PROMPT = (
    "You are a predictive-maintenance assistant for a machining plant. Use the tools to look up the asset, "
    "read its sensors, find the repair procedure and, when severity is high or critical, open a proactive "
    "service order. Base every statement on tool output. Be concise."
)


class LoopDetected(RuntimeError):
    pass


class ToolFailure(RuntimeError):
    pass


def grafana_trace_url(trace_id: str) -> str:
    panes = {"a": {"datasource": "tempo", "queries": [{"refId": "A", "datasource": {"type": "tempo", "uid": "tempo"},
                                                        "queryType": "traceql", "query": trace_id}],
                   "range": {"from": "now-1h", "to": "now"}}}
    return f"{GRAFANA_URL}/explore?schemaVersion=1&orgId=1&panes={quote(json.dumps(panes, separators=(',', ':')))}"


def run_agent(question: str | None = None, scenario: str = "healthy") -> dict:
    spec = scenarios.SCENARIOS[scenario]
    question = question or spec.question
    token = scenarios.set_scenario(scenario)
    handler = OTelCallbackHandler()
    start = time.perf_counter()
    outcome, answer, error = "success", "", None
    tool_results: list[dict] = []
    iterations = 0

    try:
        with tracer.start_as_current_span(
            "invoke_agent maintenance-agent", kind=SpanKind.INTERNAL,
            attributes={
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": "maintenance-agent",
                "gen_ai.conversation.id": uuid.uuid4().hex,
                "demo.scenario": scenario,
                "agent.max_iterations": MAX_ITERATIONS,
            },
            record_exception=False, set_status_on_exception=False,
        ) as span:
            trace_id = format(span.get_span_context().trace_id, "032x")
            span.set_attribute("agent.input", question)
            log.info("agent run started", extra={"demo.scenario": scenario})

            model = get_chat_model().bind_tools(TOOLS)
            messages = [SystemMessage(SYSTEM_PROMPT), HumanMessage(question)]
            seen: Counter[str] = Counter()
            try:
                for iterations in range(1, MAX_ITERATIONS + 1):
                    ai = model.invoke(messages, config={"callbacks": [handler]})
                    messages.append(ai)
                    if not ai.tool_calls:
                        answer = str(ai.content)
                        break
                    for tc in ai.tool_calls:
                        sig = tc["name"] + json.dumps(tc["args"], sort_keys=True)
                        seen[sig] += 1
                        if seen[sig] > LOOP_LIMIT:
                            raise LoopDetected(f"{tc['name']} called {seen[sig]}x with identical arguments")
                        tool = TOOLS_BY_NAME.get(tc["name"])
                        if tool is None:
                            raise ToolFailure(f"model requested unknown tool {tc['name']!r}")
                        try:
                            result = tool.invoke(tc["args"], config={"callbacks": [handler],
                                                                     "metadata": {"tool_call_id": tc["id"]}})
                        except Exception as exc:  # surface as an agent-level failure
                            raise ToolFailure(f"{tc['name']} failed: {exc}") from exc
                        content = getattr(result, "content", result)
                        tool_results.append({"name": tc["name"], "content": str(content)})
                        messages.append(ToolMessage(content=str(content), tool_call_id=tc["id"], name=tc["name"]))
                else:
                    outcome, error = "max_iterations", f"no final answer after {MAX_ITERATIONS} iterations"
            except LoopDetected as exc:
                outcome, error = "loop_detected", str(exc)
                loop_counter.add(1, {"demo.scenario": scenario})
                span.add_event("agent.loop_detected", {"reason": str(exc)})
            except ToolFailure as exc:
                outcome, error = "tool_error", str(exc)
                span.record_exception(exc)
            except Exception as exc:  # LLM/provider errors, auth, rate limits...
                outcome, error = "llm_error", f"{type(exc).__name__}: {exc}"
                span.record_exception(exc)

            span.set_attribute("agent.iterations", iterations)
            span.set_attribute("agent.tool_call_count", len(tool_results))
            span.set_attribute("agent.outcome", outcome)
            if answer:
                span.set_attribute("agent.output", answer[:2000])
            if outcome != "success":
                span.set_attribute("error.type", outcome)
                span.set_status(Status(StatusCode.ERROR, error))
                log.error("agent run failed: %s", error, extra={"agent.outcome": outcome, "demo.scenario": scenario})

            report = evaluate_run(outcome=outcome, answer=answer, tool_results=tool_results)
            record_evaluation(report, scenario=scenario, agent_span=span)
            log.info("agent run finished", extra={"agent.outcome": outcome, "eval.score": report.score,
                                                   "demo.scenario": scenario})
    finally:
        scenarios.reset(token)

    elapsed = time.perf_counter() - start
    attrs = {"outcome": outcome, "demo.scenario": scenario}
    run_counter.add(1, attrs)
    run_duration.record(elapsed, attrs)
    iterations_hist.record(iterations, {"demo.scenario": scenario})

    return {
        "scenario": scenario, "question": question, "outcome": outcome, "answer": answer, "error": error,
        "iterations": iterations, "tool_calls": [r["name"] for r in tool_results],
        "duration_s": round(elapsed, 2), "evaluation": report.as_dict(),
        "trace_id": trace_id, "grafana_trace_url": grafana_trace_url(trace_id),
    }
