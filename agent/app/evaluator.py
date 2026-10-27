"""Online evaluation: the layer tracing alone doesn't give you.

Tracing tells you WHAT the agent did. Evaluation tells you whether it was RIGHT.
Each check becomes a `gen_ai.evaluation.result` span event (GenAI semconv, development
status) plus Prometheus metrics, so quality regressions live on the same dashboard
as latency and errors.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from opentelemetry import trace

from .telemetry import eval_check_failures, eval_counter, eval_score, tracer

log = logging.getLogger("agent.eval")

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"(\+?\d{1,3}[ .-])?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}")
DISMISSIVE_RE = re.compile(r"\b(looks fine|no action|nothing to worry|within (the )?normal)\b", re.I)
MAX_TOOL_CALLS = 6


@dataclass
class CheckResult:
    name: str
    passed: bool
    explanation: str


@dataclass
class EvalReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def score(self) -> float:
        return round(sum(c.passed for c in self.checks) / len(self.checks), 3) if self.checks else 0.0

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def as_dict(self) -> dict:
        return {"score": self.score, "passed": self.passed,
                "checks": [c.__dict__ for c in self.checks]}


def _sensor_evidence(tool_results: list[dict]) -> dict | None:
    for r in tool_results:
        if r["name"] == "get_sensor_readings":
            try:
                data = json.loads(r["content"])
            except (json.JSONDecodeError, TypeError):
                continue
            if "severity" in data:
                return data
    return None


def evaluate_run(*, outcome: str, answer: str, tool_results: list[dict]) -> EvalReport:
    report = EvalReport()
    report.checks.append(CheckResult("task_completed", outcome == "success", f"run outcome = {outcome}"))

    evidence = _sensor_evidence(tool_results)
    if outcome == "success" and evidence:
        severity = evidence["severity"]
        fm = evidence.get("suspected_failure_mode", "")
        if severity in ("critical", "high"):
            grounded = (fm.lower() in answer.lower() or severity in answer.lower()) and not DISMISSIVE_RE.search(answer)
            report.checks.append(CheckResult(
                "grounded_in_evidence", grounded,
                f"sensors say severity={severity}, failure_mode={fm!r}; answer {'reflects' if grounded else 'CONTRADICTS or ignores'} it"))
            acted = any(r["name"] == "create_service_order" for r in tool_results)
            report.checks.append(CheckResult(
                "required_action_taken", acted,
                "service order created" if acted else f"severity={severity} but no service order was created"))

    leaked = bool(EMAIL_RE.search(answer) or PHONE_RE.search(answer))
    report.checks.append(CheckResult("no_pii_in_output", not leaked,
                                     "PII found in final answer" if leaked else "no email/phone in answer"))

    n_calls = len(tool_results)
    report.checks.append(CheckResult("tool_call_budget", n_calls <= MAX_TOOL_CALLS,
                                     f"{n_calls} tool calls (budget {MAX_TOOL_CALLS})"))
    return report


def record_evaluation(report: EvalReport, *, scenario: str, agent_span: trace.Span) -> None:
    """Emit the evaluation as a child span + events, set summary attributes on the agent span, update metrics."""
    with tracer.start_as_current_span("evaluate maintenance-agent", attributes={
        "gen_ai.operation.name": "evaluate", "eval.score": report.score, "eval.passed": report.passed,
    }) as span:
        for c in report.checks:
            span.add_event("gen_ai.evaluation.result", {
                "gen_ai.evaluation.name": c.name,
                "gen_ai.evaluation.score.value": 1.0 if c.passed else 0.0,
                "gen_ai.evaluation.score.label": "pass" if c.passed else "fail",
                "gen_ai.evaluation.explanation": c.explanation,
            })
            if not c.passed:
                eval_check_failures.add(1, {"check": c.name, "demo.scenario": scenario})
        failed = [c.name for c in report.checks if not c.passed]
        if failed:
            span.set_status(trace.Status(trace.StatusCode.ERROR, "failed: " + ",".join(failed)))
            log.warning("evaluation failed: %s", ", ".join(failed),
                        extra={"eval.score": report.score, "eval.failed_checks": ",".join(failed)})
        else:
            log.info("evaluation passed", extra={"eval.score": report.score})

    agent_span.set_attribute("eval.score", report.score)
    agent_span.set_attribute("eval.passed", report.passed)
    agent_span.set_attribute("eval.failed_checks", [c.name for c in report.checks if not c.passed])
    verdict = "pass" if report.passed else "fail"
    eval_counter.add(1, {"verdict": verdict, "demo.scenario": scenario})
    eval_score.record(report.score, {"demo.scenario": scenario})
