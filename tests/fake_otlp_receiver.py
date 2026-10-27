"""Minimal OTLP/HTTP receiver for tests: decodes protobuf payloads and appends them as JSON lines.

    python tests/fake_otlp_receiver.py --port 4318 --out /tmp/otlp
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

KINDS = {"/v1/traces": ("traces", ExportTraceServiceRequest),
         "/v1/metrics": ("metrics", ExportMetricsServiceRequest),
         "/v1/logs": ("logs", ExportLogsServiceRequest)}


def make_handler(out: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            kind = KINDS.get(self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if kind is None:
                self.send_response(404)
                self.end_headers()
                return
            name, cls = kind
            msg = cls()
            msg.ParseFromString(body)
            with (out / f"{name}.jsonl").open("a") as f:
                f.write(json.dumps(MessageToDict(msg)) + "\n")
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

        def log_message(self, *args):
            pass

    return Handler


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4318)
    ap.add_argument("--out", default="/tmp/otlp")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(out)).serve_forever()
