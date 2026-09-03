"""
Offline tests for the local chat page server: static page, flag API,
status, and reset. No API key and no network beyond 127.0.0.1 — the
/api/ask path is exercised only for its disabled-chat message.
"""

import json
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import chat_server  # noqa: E402


@pytest.fixture(scope="module")
def base_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), chat_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, resp.read()


def _post(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read())


def test_page_serves(base_url):
    status, body = _get(base_url + "/")
    assert status == 200
    assert b"Finance-ML Analyst" in body
    assert b"Rebuild flags" in body


def test_flags_api_matches_export(base_url):
    status, body = _get(base_url + "/api/flags")
    assert status == 200
    data = json.loads(body)
    assert set(data["counts"]) == {"RED", "YELLOW", "GREEN"}
    assert len(data["rows"]) == sum(data["counts"].values()) > 0
    assert {"color", "headline", "recommended_action",
            "source_tab"} <= set(data["rows"][0])


def test_rebuild_flags_roundtrip(base_url):
    before = json.loads(_get(base_url + "/api/flags")[1])
    status, after = _post(base_url + "/api/rebuild-flags", {})
    assert status == 200
    # deterministic engine: a rebuild reproduces the committed feed
    assert after["counts"] == before["counts"]


def test_status_and_disabled_chat_message(base_url):
    # In CI there is no ANTHROPIC_API_KEY, so chat reports itself off
    # (the module-level STATE was never init_chat()'d here anyway).
    status, body = _get(base_url + "/api/status")
    assert status == 200
    data = json.loads(body)
    assert data["chat_ready"] is False
    status, reply = _post(base_url + "/api/ask", {"question": "hi"})
    assert status == 200 and "reply" in reply


def test_reset_and_bad_requests(base_url):
    assert _post(base_url + "/api/reset", {})[1] == {"ok": True}
    try:
        urllib.request.urlopen(base_url + "/api/nope", timeout=10)
        raised = False
    except urllib.error.HTTPError as exc:
        raised = exc.code == 404
    assert raised
