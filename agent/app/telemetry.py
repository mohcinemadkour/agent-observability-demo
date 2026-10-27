"""OpenTelemetry bootstrap: traces, metrics and logs -> OTLP -> Collector.

The agent only knows ONE thing about observability: the OTLP endpoint of the
Collector (OTEL_EXPORTER_OTLP_ENDPOINT). Which backends receive the data
(Tempo, Loki, Prometheus, Jaeger, Splunk, ...) is decided in the Collector.
"""
from __future__ import annotations

import logging
import os

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "maintenance-agent")
_initialized = False


def setup_telemetry() -> None:
    """Idempotent. Exporters read OTEL_EXPORTER_OTLP_ENDPOINT (default http://localhost:4318)."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    resource = Resource.create(
        {
            "service.name": SERVICE_NAME,
            "service.version": os.getenv("SERVICE_VERSION", "0.1.0"),
            "deployment.environment.name": os.getenv("DEPLOY_ENV", "meetup-demo"),
            "gen_ai.agent.name": "maintenance-agent",
        }
    )

    # --- traces
    tp = TracerProvider(resource=resource)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(), schedule_delay_millis=1000))
    trace.set_tracer_provider(tp)

    # --- metrics (5s export so dashboards move quickly on stage)
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(), export_interval_millis=int(os.getenv("METRIC_EXPORT_MS", "5000"))
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))

    # --- logs: bridge std logging -> OTLP. trace_id/span_id are attached automatically,
    # which is what makes "jump from a trace to its logs" work in Grafana.
    lp = LoggerProvider(resource=resource)
    lp.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(), schedule_delay_millis=1000))
    set_logger_provider(lp)

    # Export only the app's own loggers ("agent.*"). Attaching to the root logger would
    # also capture the OTel SDK's internal logs and can deadlock exporter shutdown.
    app_logger = logging.getLogger("agent")
    app_logger.setLevel(logging.INFO)
    app_logger.addHandler(LoggingHandler(level=logging.INFO, logger_provider=lp))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(console)
    for noisy in ("httpx", "httpcore", "urllib3", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def shutdown_telemetry() -> None:
    for provider in (trace.get_tracer_provider(), metrics.get_meter_provider()):
        if hasattr(provider, "shutdown"):
            provider.shutdown()
    from opentelemetry._logs import get_logger_provider

    lp = get_logger_provider()
    if hasattr(lp, "shutdown"):
        lp.shutdown()


tracer = trace.get_tracer("agent-observability-demo")
meter = metrics.get_meter("agent-observability-demo")

# ---------------------------------------------------------------- instruments
# Names follow the OpenTelemetry GenAI semantic conventions where one exists
# (gen_ai.client.*), and an `agent.*` namespace for agent-level signals.
SECONDS_BUCKETS = [0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32, 64]
TOKEN_BUCKETS = [16, 64, 256, 1024, 4096, 16384, 65536]

token_usage = meter.create_histogram(
    "gen_ai.client.token.usage", unit="{token}",
    description="Tokens used per LLM call", explicit_bucket_boundaries_advisory=TOKEN_BUCKETS,
)
llm_duration = meter.create_histogram(
    "gen_ai.client.operation.duration", unit="s",
    description="LLM call latency", explicit_bucket_boundaries_advisory=SECONDS_BUCKETS,
)
run_counter = meter.create_counter("agent.runs", description="Agent runs by outcome")
run_duration = meter.create_histogram(
    "agent.run.duration", unit="s",
    description="End-to-end agent run latency", explicit_bucket_boundaries_advisory=SECONDS_BUCKETS,
)
iterations_hist = meter.create_histogram(
    "agent.iterations", description="Reasoning iterations per run",
    explicit_bucket_boundaries_advisory=[1, 2, 3, 4, 5, 6, 8, 10, 15],
)
tool_calls = meter.create_counter("agent.tool.calls", description="Tool calls by tool and status")
tool_duration = meter.create_histogram(
    "agent.tool.duration", unit="s",
    description="Tool latency", explicit_bucket_boundaries_advisory=SECONDS_BUCKETS,
)
loop_counter = meter.create_counter("agent.loop.detected", description="Runs stopped by the loop guard")
eval_counter = meter.create_counter("agent.evaluations", description="Online evaluations by verdict")
eval_check_failures = meter.create_counter(
    "agent.eval.check.failures", description="Failed evaluation checks by check name"
)
eval_score = meter.create_histogram(
    "agent.eval.score", description="Online evaluation score (0-1)",
    explicit_bucket_boundaries_advisory=[0.25, 0.5, 0.75, 0.9, 1.0],
)
