"""Pick the chat model from env. Default: the offline mock (safe for flaky meetup Wi-Fi)."""
from __future__ import annotations

import logging
import os

log = logging.getLogger("agent.llm")


def get_chat_model():
    provider = os.getenv("LLM_PROVIDER", "mock").lower()
    if provider == "openai" and os.getenv("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=os.getenv("LLM_MODEL", "gpt-4o-mini"), temperature=0, timeout=60)
    if provider == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=os.getenv("LLM_MODEL", "claude-haiku-4-5"), temperature=0, timeout=60)
    if provider != "mock":
        log.warning("LLM_PROVIDER=%s but no API key found; falling back to the offline mock model", provider)
    from .mock_llm import ScriptedChatModel

    return ScriptedChatModel()
