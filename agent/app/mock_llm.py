"""A deterministic, tool-calling chat model so the demo runs with NO API key and NO Wi-Fi.

It behaves like a real LangChain chat model (BaseChatModel, bind_tools, tool_calls,
usage_metadata), so callbacks and tracing are exercised exactly as with OpenAI or
Anthropic. Its "reasoning" is a small state machine driven by the scenario.
"""
from __future__ import annotations

import json
import random
import re
import time
import uuid
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from . import scenarios

ASSET_RE = re.compile(r"\b([A-Z]{1,4}-\d{2})\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _tok(text: str) -> int:
    return max(1, len(text) // 4)


class ScriptedChatModel(BaseChatModel):
    model_name: str = "mock-gpt-mini"
    min_latency: float = 0.3
    max_latency: float = 0.9

    @property
    def _llm_type(self) -> str:
        return "scripted-mock"

    def _get_ls_params(self, stop=None, **kwargs) -> dict:  # feeds ls_* metadata to callbacks
        return {"ls_provider": "mock", "ls_model_name": self.model_name, "ls_model_type": "chat", "ls_temperature": 0.0}

    def bind_tools(self, tools, **kwargs):  # tools are known statically
        return self

    # ------------------------------------------------------------------ brain
    def _decide(self, messages: list[BaseMessage]) -> AIMessage:
        scenario = scenarios.current()
        question = next((m.content for m in messages if isinstance(m, HumanMessage)), "")
        match = ASSET_RE.search(question)
        asset = match.group(1) if match else "CNC-07"
        tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
        done = {m.name for m in tool_msgs}
        last = tool_msgs[-1] if tool_msgs else None

        def call(name: str, **args) -> AIMessage:
            return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}])

        def answer(text: str) -> AIMessage:
            return AIMessage(content=text)

        if "get_asset_info" not in done:
            return call("get_asset_info", asset_id=asset)

        readings = None
        for m in tool_msgs:
            if m.name == "get_sensor_readings":
                try:
                    readings = json.loads(m.content)
                except (json.JSONDecodeError, TypeError):
                    readings = None
        if readings is None or (last is not None and last.name == "get_sensor_readings" and readings.get("status") == "incomplete"):
            # loop scenario: the tool keeps saying "retry", the model keeps obeying
            return call("get_sensor_readings", asset_id=asset, window_hours=24)

        severity = readings.get("severity", "normal")
        failure_mode = readings.get("suspected_failure_mode", "none")

        if scenario == "bad_answer":
            # The failure no trace will show: a confident, wrong, ungrounded answer.
            return answer(f"{asset} looks fine. Vibration is within the normal operating range and no action is needed right now.")

        if severity == "normal":
            return answer(f"{asset} readings are within normal limits (severity: normal). No service order required.")

        if "search_maintenance_manual" not in done:
            return call("search_maintenance_manual", failure_mode=failure_mode)

        if "create_service_order" not in done:
            priority = "critical" if severity == "critical" else "high"
            return call("create_service_order", asset_id=asset, failure_mode=failure_mode,
                        priority=priority, due_in_days=7 if severity == "critical" else 14)

        order = json.loads(next(m.content for m in tool_msgs if m.name == "create_service_order"))
        greeting = ""
        if scenario == "pii":
            email = EMAIL_RE.search(question)
            greeting = f"Hi {email.group(0)}, " if email else ""
        return answer(
            f"{greeting}{asset} shows {failure_mode} (severity: {severity}). "
            f"I opened service order {order['order_id']} with {order['priority']} priority, due in "
            f"{order['due_in_days']} days, following the manual procedure."
        )

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        time.sleep(random.uniform(self.min_latency, self.max_latency))
        msg = self._decide(messages)
        prompt_text = "".join(str(m.content) for m in messages) + json.dumps([getattr(m, "tool_calls", []) for m in messages], default=str)
        in_tok = 420 + _tok(prompt_text)  # ~420 tokens of system prompt + tool schemas
        out_tok = _tok(msg.content) + 24 * len(msg.tool_calls)
        msg.usage_metadata = {"input_tokens": in_tok, "output_tokens": out_tok, "total_tokens": in_tok + out_tok}
        msg.response_metadata = {"model_name": self.model_name, "finish_reason": "tool_calls" if msg.tool_calls else "stop"}
        return ChatResult(generations=[ChatGeneration(message=msg)])
