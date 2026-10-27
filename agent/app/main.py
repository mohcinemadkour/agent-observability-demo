"""HTTP entrypoint: control panel UI, /run API, and a background traffic generator."""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .telemetry import setup_telemetry, shutdown_telemetry

setup_telemetry()

from . import scenarios  # noqa: E402  (after telemetry so loggers get the OTLP handler)
from .agent import run_agent  # noqa: E402

log = logging.getLogger("agent.api")

TRAFFIC_WEIGHTS = {"healthy": 55, "slow_tool": 10, "tool_error": 10, "loop": 8, "bad_answer": 10, "pii": 7}
TRAFFIC_INTERVAL = float(os.getenv("TRAFFIC_INTERVAL_S", "2"))
TRAFFIC_WORKERS = int(os.getenv("TRAFFIC_WORKERS", "2"))


class Traffic:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.runs = 0

    @property
    def running(self) -> bool:
        return not self._stop.is_set() and any(t.is_alive() for t in self._threads)

    def start(self) -> None:
        if self.running:
            return
        for t in self._threads:  # let workers from a previous stop() exit first
            t.join(timeout=15)
        self._stop.clear()
        self._threads = [threading.Thread(target=self._loop, daemon=True, name=f"traffic-{i}") for i in range(TRAFFIC_WORKERS)]
        for t in self._threads:
            t.start()
        log.info("traffic generator started", extra={"workers": TRAFFIC_WORKERS})

    def stop(self) -> None:
        self._stop.set()
        log.info("traffic generator stopped")

    def _loop(self) -> None:
        names, weights = zip(*TRAFFIC_WEIGHTS.items())
        while not self._stop.is_set():
            try:
                run_agent(scenario=random.choices(names, weights)[0])
                self.runs += 1
            except Exception:  # keep generating even if a run blows up
                log.exception("traffic run crashed")
            self._stop.wait(random.uniform(0.5, 1.5) * TRAFFIC_INTERVAL)


traffic = Traffic()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if os.getenv("TRAFFIC", "on").lower() == "on":
        traffic.start()
    yield
    traffic.stop()
    shutdown_telemetry()


_QUIET_PATHS = {"/", "/healthz", "/api/traffic", "/api/scenarios", "/api/config"}  # UI polling would flood Tempo

# FastAPI emits OpenTelemetry HTTP spans/metrics natively via the global providers,
# so a UI-triggered run gets a `POST /api/run` server span as its root.
app = FastAPI(
    title="Agent Observability Demo",
    lifespan=lifespan,
    telemetry={"operation_spans": False, "exclude": lambda scope: scope.get("path") in _QUIET_PATHS},
)


class RunRequest(BaseModel):
    scenario: str = "healthy"
    question: str | None = None


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (Path(__file__).parent / "static" / "index.html").read_text()


@app.get("/api/config")
def config() -> dict:
    from .agent import GRAFANA_URL

    return {"grafana_url": GRAFANA_URL}


@app.get("/api/scenarios")
def list_scenarios() -> list[dict]:
    return [s.__dict__ for s in scenarios.SCENARIOS.values()]


@app.post("/api/run")
def run(req: RunRequest) -> dict:
    if req.scenario not in scenarios.SCENARIOS:
        raise HTTPException(400, f"unknown scenario; choose one of {sorted(scenarios.SCENARIOS)}")
    return run_agent(question=req.question, scenario=req.scenario)


@app.get("/api/traffic")
def traffic_status() -> dict:
    return {"running": traffic.running, "runs": traffic.runs, "weights": TRAFFIC_WEIGHTS}


@app.post("/api/traffic/{action}")
def traffic_control(action: str) -> dict:
    if action == "start":
        traffic.start()
    elif action == "stop":
        traffic.stop()
        time.sleep(0.1)
    else:
        raise HTTPException(400, "action must be start or stop")
    return traffic_status()


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
