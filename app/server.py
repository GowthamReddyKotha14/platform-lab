"""Demo workload for the platform: a tiny web service with Prometheus metrics.

Two "fault switches", set by environment variable, make a bad release
reproducible as a real configuration change pushed through Git:
  ERROR_RATE        fraction of requests answered with HTTP 500 (0.0 - 1.0)
  EXTRA_LATENCY_MS  delay added to every request
VERSION labels every metric, so dashboards and the canary analysis can tell
the stable and canary versions apart.
"""
from __future__ import annotations

import json
import os
import random
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

HOST = socket.gethostname()


class Config:
    """Read once per server start (in Kubernetes: once per pod)."""
    def __init__(self, env=os.environ):
        self.version = env.get("VERSION", "v1")
        self.error_rate = float(env.get("ERROR_RATE", "0"))
        self.extra_latency_ms = float(env.get("EXTRA_LATENCY_MS", "0"))

REQUESTS = Counter("demo_http_requests_total", "HTTP requests", ["path", "code", "version"])
LATENCY = Histogram("demo_http_request_seconds", "Request latency", ["path", "version"],
                    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5))


class Handler(BaseHTTPRequestHandler):
    server_version = "platform-lab-demo"
    cfg: Config = Config()

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/metrics":
            return self._send(200, generate_latest(), CONTENT_TYPE_LATEST)
        if path in ("/healthz", "/readyz"):          # probes stay healthy: the bug is in the app logic
            return self._send(200, b'{"ok":true}')
        c = self.cfg
        t0 = time.perf_counter()
        if c.extra_latency_ms:
            time.sleep(c.extra_latency_ms / 1000)
        if path == "/":
            code = 500 if random.random() < c.error_rate else 200
            body = {"version": c.version, "pod": HOST} if code == 200 else {"error": "simulated failure", "version": c.version}
        else:
            code, body = 404, {"error": "not found"}
        self._send(code, json.dumps(body).encode())
        REQUESTS.labels(path if code != 404 else "other", str(code), c.version).inc()
        LATENCY.labels(path if code != 404 else "other", c.version).observe(time.perf_counter() - t0)

    def log_message(self, *args):   # keep pod logs quiet
        pass


def make_server(cfg: Config, host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    handler = type("ConfiguredHandler", (Handler,), {"cfg": cfg})
    return ThreadingHTTPServer((host, port), handler)


if __name__ == "__main__":
    cfg = Config()
    print(f"demo-app {cfg.version} on :8080 (error_rate={cfg.error_rate}, extra_latency_ms={cfg.extra_latency_ms})", flush=True)
    make_server(cfg).serve_forever()
