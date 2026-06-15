"""Tests for the hosted sync relay hardening (burnmeter/sync_relay.py):
token validation, per-key rate limiting, the self-host vs hosted billing plan
lookup, and a live HTTP round-trip (PUT ciphertext, GET it back, auth errors).
"""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import burnmeter.sync_relay as R


# ---------- pure helpers ----------
def test_valid_token():
    assert R._valid_token("abcdef0123456789") is True
    assert R._valid_token("") is False
    assert R._valid_token("has space") is False
    assert R._valid_token("tab\there") is False
    assert R._valid_token("x" * (R.MAX_TOKEN + 1)) is False


def test_rate_bucket(monkeypatch):
    monkeypatch.setattr(R, "RATE_CAPACITY", 3)
    R._buckets.clear()
    results = [R._rate_ok("k") for _ in range(5)]
    assert results == [True, True, True, False, False]


def test_plan_self_host(monkeypatch):
    monkeypatch.delenv("BURNMETER_RELAY_BILLING", raising=False)
    assert R._plan_for("anytoken1234567")["plan"] == "pro"


def test_plan_hosted(monkeypatch, tmp_path):
    plans = tmp_path / "plans.json"
    plans.write_text(json.dumps({"goodtoken1234567": {"plan": "pro", "device_limit": 5}}))
    monkeypatch.setenv("BURNMETER_RELAY_BILLING", "1")
    monkeypatch.setenv("BURNMETER_RELAY_PLANS", str(plans))
    R._plans_cache.update({"path": None, "mtime": -1.0, "data": {}})  # bust cache
    assert R._plan_for("goodtoken1234567") == {"plan": "pro", "device_limit": 5}
    assert R._plan_for("stranger12345678") == {"plan": "free", "device_limit": 0}


# ---------- live HTTP round-trip ----------
def _req(method, url, token=None, body=None):
    req = urllib.request.Request(url, data=body, method=method)
    if token is not None:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("BURNMETER_RELAY_BILLING", raising=False)  # self-host: everyone pro
    monkeypatch.setattr(R, "RATE_CAPACITY", 1000)                 # don't rate-limit the test
    R._buckets.clear()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), R.make_handler(tmp_path))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        tok = "goodtoken1234567"
        assert _req("GET", base + "/health")[0] == 200
        assert _req("PUT", base + "/v1/snap/dev1", tok, b"ciphertext")[0] == 201
        code, body = _req("GET", base + "/v1/snaps", tok)
        assert code == 200 and "dev1" in json.loads(body)
        code, body = _req("GET", base + "/v1/account", tok)
        assert json.loads(body)["plan"] == "pro"
        assert _req("GET", base + "/v1/account", "bad token")[0] == 400   # malformed
        assert _req("GET", base + "/v1/account")[0] == 401                # missing
        assert _req("DELETE", base + "/v1/snap/dev1", tok)[0] == 200
    finally:
        httpd.shutdown()
