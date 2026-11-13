import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import server


def start(monkeypatch, **env):
    srv = server.make_server(server.Config({k: str(v) for k, v in env.items()}), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def get(url):
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_healthy_release(monkeypatch):
    srv, base = start(monkeypatch, VERSION="v1", ERROR_RATE=0)
    code, body = get(base + "/")
    assert code == 200 and json.loads(body)["version"] == "v1"
    assert get(base + "/healthz")[0] == 200
    srv.shutdown()


def test_bad_release_fails_requests_but_passes_probes(monkeypatch):
    """The failure a canary analysis must catch: probes stay green, users get errors."""
    srv, base = start(monkeypatch, VERSION="v2-bad", ERROR_RATE=1.0)
    assert get(base + "/")[0] == 500
    assert get(base + "/readyz")[0] == 200
    srv.shutdown()


def test_metrics_are_labeled_by_version_and_code(monkeypatch):
    srv, base = start(monkeypatch, VERSION="v9", ERROR_RATE=0)
    for _ in range(3):
        get(base + "/")
    get(base + "/nope")
    code, text = get(base + "/metrics")
    assert code == 200
    assert 'demo_http_requests_total{code="200",path="/",version="v9"} 3.0' in text
    assert 'demo_http_requests_total{code="404",path="other",version="v9"} 1.0' in text
    assert "demo_http_request_seconds_bucket" in text
    srv.shutdown()
