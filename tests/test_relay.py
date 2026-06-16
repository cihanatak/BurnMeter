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


# ---------- device_id whitelist (path-traversal defence) ----------
def test_device_id_whitelist():
    assert R._DEV_RE.match("ok_dev-1") and R._DEV_RE.match("a1B2c3")
    assert not R._DEV_RE.match("..\\evil")     # backslash (Windows separator)
    assert not R._DEV_RE.match("../evil")      # forward slash
    assert not R._DEV_RE.match("a/b")
    assert not R._DEV_RE.match("with.dot")
    assert not R._DEV_RE.match("colon:ads")    # NTFS alternate data stream
    assert not R._DEV_RE.match("")
    assert not R._DEV_RE.match("x" * 65)


# ---------- accounts (identity layer) ----------
def test_accounts_create_and_plan(tmp_path, monkeypatch):
    monkeypatch.delenv("BURNMETER_RELAY_BILLING", raising=False)
    monkeypatch.setenv("BURNMETER_RELAY_ADMIN_TOKEN", "x")   # authoritative accounts mode
    rec = R.create_account(tmp_path, email="a@b.com", plan="pro", device_limit=3)
    assert rec["token"].startswith("bm_")                    # raw token returned ONCE
    assert rec["plan"] == "pro" and rec["status"] == "active" and rec["device_limit"] == 3
    # _plan_for resolves via the accounts store (by token HASH) when storage is given
    p = R._plan_for(rec["token"], tmp_path)
    assert p == {"plan": "pro", "device_limit": 3, "status": "active"}
    # unknown token in AUTHORITATIVE mode = no sync
    assert R._plan_for("bm_stranger", tmp_path)["device_limit"] == 0
    # storage=None keeps the legacy self-host behaviour (back-compat)
    assert R._plan_for("anytoken1234567")["plan"] == "pro"


def test_token_not_persisted_plaintext(tmp_path):
    rec = R.create_account(tmp_path, email="a@b.com", plan="pro")
    raw = R._accounts_path(tmp_path).read_text(encoding="utf-8")
    assert rec["token"] not in raw                 # raw token NEVER written to disk
    assert R._hash_token(rec["token"]) in raw      # only its hash is stored


def test_accounts_additive_without_admin(tmp_path, monkeypatch):
    # accounts exist but operator did NOT opt into hosted/admin mode → unknown tokens
    # fall through to self-host (no accidental fleet lockout).
    monkeypatch.delenv("BURNMETER_RELAY_BILLING", raising=False)
    monkeypatch.delenv("BURNMETER_RELAY_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("BURNMETER_RELAY_ACCOUNTS", raising=False)
    R.create_account(tmp_path, email="a@b.com", plan="pro")
    assert R._plan_for("bm_stranger_token", tmp_path)["plan"] == "pro"   # additive, not denied


def test_accounts_status_gates_sync(tmp_path):
    rec = R.create_account(tmp_path, email="x@y.com", plan="pro")
    assert R.set_account_status(tmp_path, rec["token"], "canceled") is True   # by raw token
    p = R._plan_for(rec["token"], tmp_path)
    assert p["device_limit"] == 0 and p["status"] == "canceled"
    assert R.set_account_status(tmp_path, "bm_nope", "canceled") is False
    # reactivate by id also works
    assert R.set_account_status(tmp_path, rec["id"], "active") is True
    assert R._plan_for(rec["token"], tmp_path)["device_limit"] == R._default_limit("pro")


def test_accounts_bad_device_limit_no_500(tmp_path):
    # garbage device_limit must NOT crash; falls back to the plan default
    rec = R.create_account(tmp_path, email="g@b.com", plan="pro", device_limit="abc")
    assert rec["device_limit"] == R._default_limit("pro")


def test_accounts_list_enriched(tmp_path):
    rec = R.create_account(tmp_path, email="z@z.com", plan="team")
    lst = R.list_accounts(tmp_path)
    assert len(lst) == 1 and lst[0]["email"] == "z@z.com"
    assert lst[0]["device_count"] == 0 and "last_seen" in lst[0]
    # the live secret is NEVER exposed in the listing
    assert "token" not in lst[0] and "token_hash" not in lst[0] and "dir_hash" not in lst[0]
    assert lst[0]["token_prefix"].startswith("bm_") and lst[0]["id"]


def test_concurrent_creates_no_lost_update(tmp_path):
    # 40 concurrent creates under ThreadingHTTPServer-style threading must not drop a
    # paying customer (the write-lock + fresh-reload guards the read-modify-write).
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        recs = list(ex.map(lambda i: R.create_account(tmp_path, email=f"u{i}@x.com"),
                           range(40)))
    assert len({r["id"] for r in recs}) == 40
    assert len(R.list_accounts(tmp_path)) == 40


def test_admin_http(tmp_path, monkeypatch):
    monkeypatch.delenv("BURNMETER_RELAY_BILLING", raising=False)
    monkeypatch.setattr(R, "RATE_CAPACITY", 1000)
    R._buckets.clear()
    monkeypatch.setenv("BURNMETER_RELAY_ADMIN_TOKEN", "admin-secret-xyz")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), R.make_handler(tmp_path))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        assert _req("GET", base + "/admin")[0] == 200             # console served (admin enabled)
        assert _req("GET", base + "/admin/api/accounts")[0] == 401  # API needs the admin token
        code, body = _req("GET", base + "/admin/api/accounts", "admin-secret-xyz")
        assert code == 200 and json.loads(body)["accounts"] == []
        # create an account
        code, body = _req("POST", base + "/admin/api/accounts", "admin-secret-xyz",
                          json.dumps({"email": "new@cust.com", "plan": "pro"}).encode())
        assert code == 201
        rec = json.loads(body)
        assert rec["email"] == "new@cust.com" and rec["token"].startswith("bm_") and rec["id"]
        # listed, and the listing does NOT carry the raw token
        code, body = _req("GET", base + "/admin/api/accounts", "admin-secret-xyz")
        accts = json.loads(body)["accounts"]
        assert len(accts) == 1 and "token" not in accts[0]
        # that token now syncs (authoritative accounts-mode, active)
        assert _req("PUT", base + "/v1/snap/devA", rec["token"], b"ciphertext")[0] == 201
        # revoke BY ID -> sync blocked (402)
        assert _req("POST", base + "/admin/api/account/status", "admin-secret-xyz",
                    json.dumps({"id": rec["id"], "status": "canceled"}).encode())[0] == 200
        assert _req("PUT", base + "/v1/snap/devB", rec["token"], b"ciphertext")[0] == 402
        # malformed create body -> 400, not 500
        assert _req("POST", base + "/admin/api/accounts", "admin-secret-xyz", b"{not json")[0] == 400
    finally:
        httpd.shutdown()


def test_admin_disabled_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("BURNMETER_RELAY_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(R, "RATE_CAPACITY", 1000)
    R._buckets.clear()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), R.make_handler(tmp_path))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        # admin disabled => both the console and the API are hidden (404)
        assert _req("GET", base + "/admin", "whatever")[0] == 404
        assert _req("GET", base + "/admin/api/accounts", "whatever")[0] == 404
    finally:
        httpd.shutdown()
