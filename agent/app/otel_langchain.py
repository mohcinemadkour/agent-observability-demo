"""A LangChain callback handler that turns LLM and tool activity into OpenTelemetry spans + metrics.

Drop it into ANY LangChain runnable:  model.invoke(msgs, config={"callbacks": [OTelCallbackHandler()]})

Attributes follow the OpenTelemetry GenAI semantic conventions (gen_ai.*), so any
OTel-aware backend understands them. It's ~150 lines on purpose: on stage you can
show exactly what "instrumenting an agent" means. For production you can swap in
opentelemetry-instrumentation-langchain (OpenLLMetry) or OpenInference with no
change to the Collector or dashboards beyond attribute names.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from .telemetry import llm_duration, token_usage, tool_calls, tool_duration, tracer

CAPTURE_CONTENT = os.getenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true").lower() == "true"
MAX_CONTENT = 2000


def _truncate(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= MAX_CONTENT else text[:MAX_CONTENT] + "...<truncated>"


def _messages_to_json(messages) -> str:
    out = []
    for m in messages:
        item = {"role": getattr(m, "type", "unknown"), "content": m.content}
        if getattr(m, "tool_calls", None):
            item["tool_calls"] = [{"name": t["name"], "args": t["args"]} for t in m.tool_calls]
        out.append(item)
    return _truncate(out)


class OTelCallbackHandler(BaseCallbackHandler):
    """One span per LLM call (`chat <model>`) and per tool call (`execute_tool <name>`)."""

    raise_error = False  # never let telemetry break the agent

    def __init__(self) -> None:
        self._spans: dict[UUID, Any] = {}
        self._starts: dict[UUID, float] = {}
        self._tokens: dict[UUID, object] = {}
        self._meta: dict[UUID, dict] = {}

    # ------------------------------------------------------------------ helpers
    def _parent_ctx(self, parent_run_id: UUID | None):
        if parent_run_id and parent_run_id in self._spans:
            return trace.set_span_in_context(self._spans[parent_run_id])
        return otel_context.get_current()

    def _end(self, run_id: UUID, error: BaseException | None = None):
        span = self._spans.pop(run_id, None)
        start = self._starts.pop(run_id, time.perf_counter())
        token = self._tokens.pop(run_id, None)
        if token is not None:
            try:
                otel_context.detach(token)
            except Exception:  # pragma: no cover - out-of-order callbacks
                pass
        if span is not None and error is not None:
            span.record_exception(error)
            span.set_attribute("error.type", type(error).__name__)
            span.set_status(Status(StatusCode.ERROR, str(error)[:200]))
        if span is not None:
            span.end()
        return time.perf_counter() - start

    # --------------------------------------------------------------- LLM calls
    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, metadata=None, **kwargs):
        metadata = metadata or {}
        model = metadata.get("ls_model_name") or (serialized or {}).get("kwargs", {}).get("model", "unknown")
        provider = metadata.get("ls_provider", "unknown")
        span = tracer.start_span(
            f"chat {model}", kind=SpanKind.CLIENT, context=self._parent_ctx(parent_run_id),
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": provider,
                "gen_ai.request.model": model,
            },
        )
        if metadata.get("ls_temperature") is not None:
            span.set_attribute("gen_ai.request.temperature", float(metadata["ls_temperature"]))
        if CAPTURE_CONTENT and messages:
            span.set_attribute("gen_ai.input.messages", _messages_to_json(messages[0]))
        self._spans[run_id] = span
        self._starts[run_id] = time.perf_counter()
        self._meta[run_id] = {"model": model, "provider": provider}

    def on_llm_end(self, response: LLMResult, *, run_id, **kwargs):
        span = self._spans.get(run_id)
        meta = self._meta.pop(run_id, {})
        attrs = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": meta.get("provider", "unknown"),
            "gen_ai.request.model": meta.get("model", "unknown"),
        }
        if span is not None and response.generations and response.generations[0]:
            gen = response.generations[0][0]
            msg = getattr(gen, "message", None)
            usage = getattr(msg, "usage_metadata", None) or {}
            in_tok, out_tok = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
            span.set_attribute("gen_ai.usage.input_tokens", in_tok)
            span.set_attribute("gen_ai.usage.output_tokens", out_tok)
            token_usage.record(in_tok, {**attrs, "gen_ai.token.type": "input"})
            token_usage.record(out_tok, {**attrs, "gen_ai.token.type": "output"})
            rmeta = getattr(msg, "response_metadata", {}) or {}
            if rmeta.get("model_name"):
                span.set_attribute("gen_ai.response.model", rmeta["model_name"])
            finish = rmeta.get("finish_reason") or rmeta.get("stop_reason") or (
                "tool_calls" if getattr(msg, "tool_calls", None) else "stop"
            )
            span.set_attribute("gen_ai.response.finish_reasons", [str(finish)])
            if getattr(msg, "tool_calls", None):
                span.set_attribute("gen_ai.response.tool_calls", [t["name"] for t in msg.tool_calls])
            if CAPTURE_CONTENT and msg is not None:
                span.set_attribute("gen_ai.output.messages", _messages_to_json([msg]))
        llm_duration.record(self._end(run_id), attrs)

    def on_llm_error(self, error, *, run_id, **kwargs):
        meta = self._meta.pop(run_id, {})
        llm_duration.record(
            self._end(run_id, error),
            {"gen_ai.operation.name": "chat", "gen_ai.request.model": meta.get("model", "unknown"),
             "gen_ai.provider.name": meta.get("provider", "unknown"), "error.type": type(error).__name__},
        )

    # -------------------------------------------------------------- tool calls
    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, metadata=None, inputs=None, **kwargs):
        name = (serialized or {}).get("name", "unknown_tool")
        span = tracer.start_span(
            f"execute_tool {name}", kind=SpanKind.INTERNAL, context=self._parent_ctx(parent_run_id),
            attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name, "gen_ai.tool.type": "function"},
        )
        call_id = (metadata or {}).get("tool_call_id") or kwargs.get("tool_call_id")
        if call_id:
            span.set_attribute("gen_ai.tool.call.id", str(call_id))
        if CAPTURE_CONTENT:
            span.set_attribute("gen_ai.tool.call.arguments", _truncate(inputs if inputs is not None else input_str))
        self._spans[run_id] = span
        self._starts[run_id] = time.perf_counter()
        self._meta[run_id] = {"tool": name}
        # make the tool span current so logs emitted inside the tool carry its span_id
        self._tokens[run_id] = otel_context.attach(trace.set_span_in_context(span))

    def on_tool_end(self, output, *, run_id, **kwargs):
        span = self._spans.get(run_id)
        name = self._meta.pop(run_id, {}).get("tool", "unknown_tool")
        if span is not None and CAPTURE_CONTENT:
            span.set_attribute("gen_ai.tool.call.result", _truncate(getattr(output, "content", output)))
        elapsed = self._end(run_id)
        tool_calls.add(1, {"gen_ai.tool.name": name, "status": "ok"})
        tool_duration.record(elapsed, {"gen_ai.tool.name": name, "status": "ok"})

    def on_tool_error(self, error, *, run_id, **kwargs):
        name = self._meta.pop(run_id, {}).get("tool", "unknown_tool")
        elapsed = self._end(run_id, error)
        attrs = {"gen_ai.tool.name": name, "status": "error", "error.type": type(error).__name__}
        tool_calls.add(1, attrs)
        tool_duration.record(elapsed, attrs)
