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
from burnmeter.sync import derive_auth_secret   # mirror the real client derivation


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


# ---------- accounts: email + password (zero-knowledge) ----------
def _auth(email, password):
    return derive_auth_secret(email, password)


def test_create_then_activate_then_plan(tmp_path, monkeypatch):
    monkeypatch.delenv("BURNMETER_RELAY_BILLING", raising=False)
    monkeypatch.setenv("BURNMETER_RELAY_ADMIN_TOKEN", "x")     # authoritative accounts mode
    rec = R.create_account(tmp_path, email="A@B.com", plan="pro", device_limit=3)
    assert rec["activation_code"] and rec["activated"] is False
    assert "token" not in rec and "token_hash" not in rec      # no token model anymore
    bearer = _auth("a@b.com", "hunter2")                       # email normalised to lower
    # before activation the password-derived bearer doesn't work (authoritative => 0)
    assert R._plan_for(bearer, tmp_path)["device_limit"] == 0
    res = R.activate_account(tmp_path, "a@b.com", rec["activation_code"], bearer)
    assert res["ok"] and res["plan"] == "pro" and res["device_limit"] == 3
    assert R._plan_for(bearer, tmp_path) == {"plan": "pro", "device_limit": 3, "status": "active"}
    # wrong code rejected (generic message)
    R.create_account(tmp_path, email="c@d.com", plan="pro")
    assert R.activate_account(tmp_path, "c@d.com", "WRONG-CODE", _auth("c@d.com", "p"))["ok"] is False
    # re-activating the SAME device/password is idempotent → ok; a DIFFERENT password on
    # an already-activated account is rejected (generic)
    assert R.activate_account(tmp_path, "a@b.com", "x", bearer)["ok"] is True
    assert R.activate_account(tmp_path, "a@b.com", "x", _auth("a@b.com", "different"))["ok"] is False
    # storage=None keeps legacy self-host (back-compat)
    assert R._plan_for("anytoken1234567")["plan"] == "pro"


def test_recreate_activated_account_preserves_it(tmp_path, monkeypatch):
    # webhook retry / operator re-create for an ALREADY-ACTIVATED email must NOT wipe the
    # account (that would lock the paying customer out + orphan their ciphertext).
    monkeypatch.setenv("BURNMETER_RELAY_ADMIN_TOKEN", "x")
    rec = R.create_account(tmp_path, email="keep@x.com", plan="pro")
    bearer = _auth("keep@x.com", "pw-strong-1")
    R.activate_account(tmp_path, "keep@x.com", rec["activation_code"], bearer)
    again = R.create_account(tmp_path, email="keep@x.com", plan="team")   # e.g. plan change/renewal
    assert again.get("activation_code") is None                          # no new code (still activated)
    p = R._plan_for(bearer, tmp_path)
    assert p["status"] == "active" and p["plan"] == "team"               # works + plan updated in place
    assert len(R.list_accounts(tmp_path)) == 1


def test_selfhost_login_bootstrap(tmp_path, monkeypatch):
    # no accounts + non-strict relay → login bootstraps Pro (the free self-host path)
    monkeypatch.delenv("BURNMETER_RELAY_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("BURNMETER_RELAY_ACCOUNTS", raising=False)
    monkeypatch.delenv("BURNMETER_RELAY_BILLING", raising=False)
    res = R.login_account(tmp_path, "me@self.host", _auth("me@self.host", "whatever-pw"))
    assert res["ok"] is True and res.get("selfhost") is True


def test_pepper_protects_verifier(tmp_path, monkeypatch):
    # with a pepper, the stored verifier is an HMAC (not the plain hash) → a file-only
    # leak of accounts.json can't be brute-forced without the env pepper.
    monkeypatch.setenv("BURNMETER_RELAY_ADMIN_TOKEN", "x")
    monkeypatch.setenv("BURNMETER_RELAY_PEPPER", "super-secret-pepper")
    rec = R.create_account(tmp_path, email="p@x.com", plan="pro")
    bearer = _auth("p@x.com", "pw-strong-2")
    R.activate_account(tmp_path, "p@x.com", rec["activation_code"], bearer)
    raw = R._accounts_path(tmp_path).read_text(encoding="utf-8")
    assert "hmac:" in raw and R._hash_token(bearer) not in raw          # peppered, not plain
    assert R.login_account(tmp_path, "p@x.com", bearer)["ok"] is True   # login still works


def test_login_verifies_password(tmp_path, monkeypatch):
    monkeypatch.setenv("BURNMETER_RELAY_ADMIN_TOKEN", "x")
    rec = R.create_account(tmp_path, email="u@x.com", plan="pro")
    good = _auth("u@x.com", "correct horse")
    R.activate_account(tmp_path, "u@x.com", rec["activation_code"], good)
    assert R.login_account(tmp_path, "u@x.com", good)["ok"] is True
    assert R.login_account(tmp_path, "u@x.com", _auth("u@x.com", "wrong"))["ok"] is False
    assert R.login_account(tmp_path, "nobody@x.com", good)["ok"] is False
    R.create_account(tmp_path, email="fresh@x.com", plan="pro")   # not activated yet
    assert R.login_account(tmp_path, "fresh@x.com", _auth("fresh@x.com", "p"))["ok"] is False


def test_password_not_persisted(tmp_path, monkeypatch):
    monkeypatch.delenv("BURNMETER_RELAY_PEPPER", raising=False)   # this test asserts the plain-hash fallback
    rec = R.create_account(tmp_path, email="a@b.com", plan="pro")
    bearer = _auth("a@b.com", "s3cret-pw")
    R.activate_account(tmp_path, "a@b.com", rec["activation_code"], bearer)
    raw = R._accounts_path(tmp_path).read_text(encoding="utf-8")
    assert "s3cret-pw" not in raw            # password never on disk
    assert bearer not in raw                  # nor the raw auth_secret
    assert R._hash_token(bearer) in raw       # only its hash


def test_status_gates_sync(tmp_path, monkeypatch):
    monkeypatch.setenv("BURNMETER_RELAY_ADMIN_TOKEN", "x")
    rec = R.create_account(tmp_path, email="x@y.com", plan="pro")
    bearer = _auth("x@y.com", "pw")
    R.activate_account(tmp_path, "x@y.com", rec["activation_code"], bearer)
    assert R.set_account_status(tmp_path, rec["id"], "canceled") is True
    assert R._plan_for(bearer, tmp_path)["device_limit"] == 0
    assert R.set_account_status(tmp_path, "no-such-id", "canceled") is False
    assert R.set_account_status(tmp_path, "x@y.com", "active") is True     # by email
    assert R._plan_for(bearer, tmp_path)["device_limit"] == R._default_limit("pro")


def test_accounts_additive_without_admin(tmp_path, monkeypatch):
    monkeypatch.delenv("BURNMETER_RELAY_BILLING", raising=False)
    monkeypatch.delenv("BURNMETER_RELAY_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("BURNMETER_RELAY_ACCOUNTS", raising=False)
    R.create_account(tmp_path, email="a@b.com", plan="pro")
    # unknown bearer, additive mode → falls through to self-host (not denied)
    assert R._plan_for(_auth("stranger@x.com", "p"), tmp_path)["plan"] == "pro"


def test_bad_device_limit_no_500(tmp_path):
    rec = R.create_account(tmp_path, email="g@b.com", plan="pro", device_limit="abc")
    assert rec["device_limit"] == R._default_limit("pro")


def test_one_account_per_email(tmp_path):
    R.create_account(tmp_path, email="dup@x.com", plan="pro")
    R.create_account(tmp_path, email="DUP@x.com", plan="team")   # same email (case-insensitive)
    lst = R.list_accounts(tmp_path)
    assert len(lst) == 1 and lst[0]["plan"] == "team"            # re-create refreshes


def test_list_enriched_no_secrets(tmp_path):
    R.create_account(tmp_path, email="z@z.com", plan="team")
    lst = R.list_accounts(tmp_path)
    assert len(lst) == 1 and lst[0]["email"] == "z@z.com"
    assert lst[0]["activated"] is False and lst[0]["device_count"] == 0 and "last_seen" in lst[0]
    for secret in ("token", "token_hash", "dir_hash", "activation_code"):
        assert secret not in lst[0]


def test_concurrent_creates_no_lost_update(tmp_path):
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        recs = list(ex.map(lambda i: R.create_account(tmp_path, email=f"u{i}@x.com"), range(40)))
    assert len({r["id"] for r in recs}) == 40
    assert len(R.list_accounts(tmp_path)) == 40


def test_admin_and_auth_http(tmp_path, monkeypatch):
    monkeypatch.delenv("BURNMETER_RELAY_BILLING", raising=False)
    monkeypatch.setattr(R, "RATE_CAPACITY", 1000)
    R._buckets.clear()
    monkeypatch.setenv("BURNMETER_RELAY_ADMIN_TOKEN", "admin-secret-xyz")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), R.make_handler(tmp_path))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        assert _req("GET", base + "/admin")[0] == 200
        assert _req("GET", base + "/admin/api/accounts")[0] == 401
        # operator creates the account → gets an activation code (NOT a token)
        code, body = _req("POST", base + "/admin/api/accounts", "admin-secret-xyz",
                          json.dumps({"email": "new@cust.com", "plan": "pro"}).encode())
        assert code == 201
        rec = json.loads(body)
        acode = rec["activation_code"]
        assert acode and "token" not in rec
        accts = json.loads(_req("GET", base + "/admin/api/accounts", "admin-secret-xyz")[1])["accounts"]
        assert len(accts) == 1 and accts[0]["activated"] is False and "token" not in accts[0]
        # customer activates (email + code + password-derived auth_secret)
        bearer = _auth("new@cust.com", "my-strong-pw")
        st, ab = _req("POST", base + "/v1/activate", None,
                      json.dumps({"email": "new@cust.com", "code": acode, "auth_secret": bearer}).encode())
        assert st == 200 and json.loads(ab)["ok"] is True
        # sync now works with the password-derived bearer
        assert _req("PUT", base + "/v1/snap/devA", bearer, b"ciphertext")[0] == 201
        # second device: login with email+password (no code)
        st, lb = _req("POST", base + "/v1/login", None,
                      json.dumps({"email": "new@cust.com", "auth_secret": bearer}).encode())
        assert st == 200 and json.loads(lb)["ok"] is True
        # wrong password → 401
        st, _b = _req("POST", base + "/v1/login", None,
                      json.dumps({"email": "new@cust.com", "auth_secret": _auth("new@cust.com", "WRONG")}).encode())
        assert st == 401
        # admin revoke by id → sync blocked
        assert _req("POST", base + "/admin/api/account/status", "admin-secret-xyz",
                    json.dumps({"id": rec["id"], "status": "canceled"}).encode())[0] == 200
        assert _req("PUT", base + "/v1/snap/devB", bearer, b"ciphertext")[0] == 402
        # malformed admin create → 400 not 500
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
