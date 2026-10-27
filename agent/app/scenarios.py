"""Failure scenarios you can trigger live during the demo.

Each scenario is a realistic way an agent breaks *without* raising a classic
500 error. The point of the demo: every one of them is visible in Grafana.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    description: str
    question: str
    asset_id: str = "CNC-07"


SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in [
        Scenario(
            "healthy",
            "Healthy run",
            "Agent checks the asset, reads sensors, consults the manual and opens a service order.",
            "Spindle on CNC-07 sounds rough. Check it and open a service order if needed.",
        ),
        Scenario(
            "tool_error",
            "Tool failure",
            "The sensor API returns HTTP 503. The agent run fails mid-workflow.",
            "Check CNC-12 vibration trend and schedule maintenance if required.",
            asset_id="CNC-12",
        ),
        Scenario(
            "slow_tool",
            "Slow dependency",
            "The maintenance manual search takes ~4s. Nothing errors, latency just explodes.",
            "Lathe L-03 is overheating. What should we do?",
            asset_id="L-03",
        ),
        Scenario(
            "loop",
            "Stuck in a loop",
            "Sensor data keeps coming back 'incomplete', so the agent re-calls the same tool until the loop guard trips.",
            "Get the latest readings for CNC-07 and assess bearing health.",
        ),
        Scenario(
            "bad_answer",
            "Silent wrong answer",
            "Every span is green, but the agent ignores the critical vibration reading and says 'no action needed'. Only the evaluator catches it.",
            "Quick sanity check on CNC-07, anything to worry about?",
        ),
        Scenario(
            "pii",
            "PII in the prompt",
            "The user pastes an email and phone number. The Collector redacts them before they reach any backend.",
            "I'm john.doe@acme-plant.com, call me at +1-214-555-0199 about CNC-07 spindle noise.",
        ),
    ]
}

_current: contextvars.ContextVar[str] = contextvars.ContextVar("scenario", default="healthy")


def set_scenario(name: str) -> contextvars.Token:
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}; choose from {sorted(SCENARIOS)}")
    return _current.set(name)


def current() -> str:
    return _current.get()


def reset(token: contextvars.Token) -> None:
    _current.reset(token)
