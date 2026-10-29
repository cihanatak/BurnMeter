"""Firebase Pro client (burnmeter/firebase_sync.py) — logic tests with mocked HTTP.
No network: _req is monkeypatched to simulate Firebase Auth + Firestore REST responses.
Needs the [sync] extra (cryptography) for the E2E round-trip; skipped cleanly if absent.
"""
import base64
import json

import pytest

pytest.importorskip("cryptography")

import burnmeter.firebase_sync as fb   # noqa: E402
import burnmeter.sync as sync          # noqa: E402

PW = "a-strong-password"
ENC_KEY = sync.derive_enc_key("a@b.com", PW).decode("ascii")   # scrypt: derive once
WRONG =(400, {"error": {"message": "INVALID_LOGIN_CREDENTIALS"}})
DENIED = (403, {"error": {"code": 403, "message": "Missing or insufficient permissions.",
                          "status": "PERMISSION_DENIED"}})


def _mock(monkeypatch, table, calls=None):
    """table: list of (url_substring, method, (status, dict)) or a callable(url, payload)
    in place of the response. calls, if given, collects (url, method, payload)."""
    def fake(url, method="POST", payload=None, token=None, timeout=20):
        if calls is not None:
            calls.append((url, method, payload))
        for sub, m, resp in table:
            if sub in url and (m is None or m == method):
                return resp(url, payload) if callable(resp) else resp
        return (404, {"error": {"message": "no mock for " + url}})
    monkeypatch.setattr(fb, "_req", fake)


def _session(uid="uid1", **kw):
    return {"localId": uid, "idToken": "id-" + uid, "refreshToken": "rt-" + uid,
            "expiresIn": "3600", **kw}


def _signed_in(tmp_path, monkeypatch, **extra):
    monkeypatch.setattr(fb, "CONFIG_PATH", tmp_path / "firebase.json")
    cfg = {"email": "a@b.com", "uid": "uid1", "refresh_token": "rt1", "id_token": "id1",
           "token_expiry": 9e18, "enc_key": ENC_KEY,
           "device_id": "0123456789ab", "label": "PC1", **extra}
    fb.save_config(cfg)
    return fb.load_config()


def _snap(month=4242.0, label="PC1"):
    return {"v": 8, "device_id": "0123456789ab", "label": label,
            "generated_at": "2026-10-29T12:00:00+00:00",
            "sources": {"claude": {"month_so_far": month, "record_count": 9}}}


# ---------- accounts: Firebase never sees the password ----------
def test_signup_sends_a_derived_secret_and_stores_no_password(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "CONFIG_PATH", tmp_path / "firebase.json")
    calls = []
    _mock(monkeypatch, [("accounts:signUp", None, (200, _session())),
                        ("sendOobCode", None, (200, {}))], calls)
    r = fb.signup("A@B.com", PW)
    assert r["ok"] and r["verified"] is False and r["verification_sent"]
    sent = calls[0][2]["password"]
    assert sent == sync.derive_auth_secret("a@b.com", PW) and sent != PW
    cfg = fb.load_config()
    assert cfg["uid"] == "uid1" and cfg["refresh_token"] == "rt-uid1"
    assert cfg["email"] == "a@b.com" and cfg["enc_key"] and cfg["device_id"]
    assert PW not in json.dumps(cfg)   # password NEVER stored
    # the E2E key still comes from the password, so it differs from what Firebase got
    assert cfg["enc_key"] != sent


def test_signup_short_password_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "CONFIG_PATH", tmp_path / "firebase.json")
    assert fb.signup("a@b.com", "short")["ok"] is False


def test_login_with_the_derived_secret_needs_no_migration(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "CONFIG_PATH", tmp_path / "firebase.json")
    calls = []
    secret = sync.derive_auth_secret("a@b.com", PW)
    _mock(monkeypatch, [
        ("signInWithPassword", None,
         lambda url, p: (200, _session()) if p["password"] == secret else WRONG),
        ("accounts:lookup", None, (200, {"users": [{"emailVerified": True}]})),
    ], calls)
    r = fb.login("a@b.com", PW)
    assert r["ok"] and r["verified"] and r["migrated"] is False
    assert [c[2]["password"] for c in calls if "signInWithPassword" in c[0]] == [secret]
    assert not any("accounts:update" in c[0] for c in calls)


def test_login_moves_a_pre_036_account_to_the_derived_secret(tmp_path, monkeypatch):
    """An account made before 0.3.6 has the raw password at Firebase: the derived secret
    is refused, the password works, and the account is switched to the derived secret."""
    monkeypatch.setattr(fb, "CONFIG_PATH", tmp_path / "firebase.json")
    calls = []
    secret = sync.derive_auth_secret("a@b.com", PW)
    _mock(monkeypatch, [
        ("signInWithPassword", None,
         lambda url, p: (200, _session()) if p["password"] == PW else WRONG),
        ("accounts:update", None, (200, {"idToken": "id-new", "refreshToken": "rt-new",
                                         "expiresIn": "3600"})),
        ("accounts:lookup", None, (200, {"users": [{"emailVerified": True}]})),
    ], calls)
    r = fb.login("a@b.com", PW)
    assert r["ok"] and r["migrated"] is True and r["uid"] == "uid1"
    upd = [c[2] for c in calls if "accounts:update" in c[0]]
    assert upd == [{"idToken": "id-uid1", "password": secret, "returnSecureToken": True}]
    cfg = fb.load_config()
    # the password change revoked the old tokens; the session keeps the new ones
    assert cfg["refresh_token"] == "rt-new" and cfg["id_token"] == "id-new"
    assert cfg["enc_key"] == ENC_KEY   # same password, same E2E key: old snapshots still open
    assert PW not in json.dumps(cfg)


def test_login_keeps_the_session_when_the_migration_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "CONFIG_PATH", tmp_path / "firebase.json")
    _mock(monkeypatch, [
        ("signInWithPassword", None,
         lambda url, p: (200, _session()) if p["password"] == PW else WRONG),
        ("accounts:update", None, (0, {"error": {"message": "timed out"}})),
        ("accounts:lookup", None, (200, {"users": [{"emailVerified": True}]})),
    ])
    r = fb.login("a@b.com", PW)
    assert r["ok"] and r["migrated"] is False   # moves over on the next sign-in
    assert fb.load_config()["refresh_token"] == "rt-uid1"


def test_login_error_is_friendly(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "CONFIG_PATH", tmp_path / "firebase.json")
    _mock(monkeypatch, [("signInWithPassword", None, WRONG)])
    r = fb.login("a@b.com", "whatever-pw")
    assert r["ok"] is False and "wrong email or password" in r["error"]


def test_a_revoked_session_signs_the_device_out(tmp_path, monkeypatch):
    """Changing the password (e.g. the migration, on another device) revokes every refresh
    token. The device must say "sign in again", not keep using a dead token."""
    cfg = _signed_in(tmp_path, monkeypatch, token_expiry=0)
    _mock(monkeypatch, [("securetoken", None,
                         (400, {"error": {"code": 400, "message": "TOKEN_EXPIRED"}}))])
    assert fb.valid_token(cfg) is None
    assert fb.load_config()["signed_out"] == "TOKEN_EXPIRED"
    assert fb.push_snapshot(cfg, _snap())["kind"] == "auth"


def test_a_transient_refresh_failure_keeps_the_session(tmp_path, monkeypatch):
    cfg = _signed_in(tmp_path, monkeypatch, token_expiry=0)
    _mock(monkeypatch, [("securetoken", None, (0, {"error": {"message": "timed out"}}))])
    assert fb.valid_token(cfg) == "id1"
    assert "signed_out" not in fb.load_config()


# ---------- device docs ----------
def test_push_pull_roundtrip_ciphertext_only(tmp_path, monkeypatch):
    _signed_in(tmp_path, monkeypatch)
    stored = {}

    def fake(url, method="POST", payload=None, token=None, timeout=20):
        assert token == "id1"                       # Firestore call carries the bearer
        if url.endswith("documents:commit"):
            w = payload["writes"][0]
            stored["fields"] = dict(w["update"]["fields"],
                                    updated_at={"timestampValue": "2026-10-29T12:00:00Z"})
            return (200, {"writeResults": [{}]})
        if method == "GET" and url.rstrip("/").endswith("/devices"):
            return (200, {"documents": [{"name": "x/users/uid1/devices/0123456789ab",
                                         "fields": stored["fields"]}]})
        return (200, {})
    monkeypatch.setattr(fb, "_req", fake)

    assert fb.push_snapshot(fb.load_config(), _snap())["ok"]
    # what hits Firestore is CIPHERTEXT — the plaintext value must not appear
    blob = base64.b64decode(stored["fields"]["blob"]["stringValue"])
    assert b"4242" not in blob
    devs = fb.pull(fb.load_config())
    assert len(devs) == 1 and devs[0]["sources"]["claude"]["month_so_far"] == 4242.0
    assert devs[0]["label"] == "PC1"   # the name comes back from inside the ciphertext


def test_push_writes_only_ciphertext_and_lets_firestore_stamp_the_time(tmp_path, monkeypatch):
    """firestore.rules accepts blob + updated_at and rate-limits on request.time, so the
    write carries no plaintext label and asks Firestore for REQUEST_TIME."""
    cfg = _signed_in(tmp_path, monkeypatch)
    calls = []
    _mock(monkeypatch, [("documents:commit", "POST", (200, {"writeResults": [{}]}))], calls)
    assert fb.push_snapshot(cfg, _snap(label="Cihan's Studio PC"))["ok"]
    (url, method, payload), = calls
    w = payload["writes"][0]
    assert w["update"]["name"].endswith("/users/uid1/devices/0123456789ab")
    assert set(w["update"]["fields"]) == {"blob"}
    assert w["updateTransforms"] == [{"fieldPath": "updated_at", "setToServerValue": "REQUEST_TIME"}]
    assert "Studio" not in json.dumps(payload)


@pytest.mark.parametrize("resp,kind", [
    ((429, {"error": {"code": 429, "message": "Quota exceeded.", "status": "RESOURCE_EXHAUSTED"}}), "quota"),
    ((0, {"error": {"message": "timed out"}}), "network"),
    ((401, {"error": {"code": 401, "status": "UNAUTHENTICATED"}}), "auth"),
    (DENIED, "denied"),
    ((500, {"error": {"message": "backend"}}), "error"),
])
def test_push_failures_say_what_went_wrong(tmp_path, monkeypatch, resp, kind):
    cfg = _signed_in(tmp_path, monkeypatch)
    _mock(monkeypatch, [("documents:commit", None, resp), ("securetoken", None, (0, {}))])
    r = fb.push_snapshot(cfg, _snap())
    assert r["ok"] is False and r["kind"] == kind and r["error"]


@pytest.mark.parametrize("users_doc,kind", [
    ((404, {"error": {"status": "NOT_FOUND"}}), "plan"),
    ((200, {"fields": {"plan": {"stringValue": "free"}}}), "plan"),
    ((200, {"fields": {"plan": {"stringValue": "pro"}, "status": {"stringValue": "suspended"}}}), "plan"),
    ((200, {"fields": {"plan": {"stringValue": "pro"}}}), "denied"),
])
def test_a_refused_pull_names_the_reason(tmp_path, monkeypatch, users_doc, kind):
    cfg = _signed_in(tmp_path, monkeypatch, _verified_refreshed=True)
    _mock(monkeypatch, [("/devices", "GET", DENIED), ("/users/uid1", "GET", users_doc),
                        ("securetoken", None, (0, {})),
                        ("accounts:lookup", None, (200, {"users": [{"emailVerified": True}]}))])
    with pytest.raises(fb.SyncError) as e:
        fb.pull(cfg)
    assert e.value.kind == kind


def test_account_reports_a_failure_instead_of_free(tmp_path, monkeypatch):
    cfg = _signed_in(tmp_path, monkeypatch)
    _mock(monkeypatch, [("/users/uid1", "GET", (0, {"error": {"message": "offline"}}))])
    with pytest.raises(fb.SyncError) as e:
        fb.account(cfg)
    assert e.value.kind == "network"
    _mock(monkeypatch, [("/users/uid1", "GET", (404, {}))])
    assert fb.account(cfg) == {"plan": "free", "status": "active"}


def test_is_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "CONFIG_PATH", tmp_path / "firebase.json")
    assert fb.is_configured({}) is False
    assert fb.is_configured({"refresh_token": "x", "uid": "u", "enc_key": "k", "email": "e"}) is True


# ---------- when to push ----------
class _Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _pusher(tmp_path, monkeypatch, results=None):
    """An AutoPush on a fake clock whose push_snapshot records snapshots."""
    _signed_in(tmp_path, monkeypatch)
    pushed = []
    results = results if results is not None else []

    def fake_push(cfg, snapshot):
        pushed.append(snapshot)
        return results.pop(0) if results else {"ok": True}
    monkeypatch.setattr(fb, "push_snapshot", fake_push)
    clock = _Clock()
    return fb.AutoPush(clock=clock), clock, pushed


def test_an_unchanged_snapshot_is_not_pushed_again(tmp_path, monkeypatch):
    ap, clock, pushed = _pusher(tmp_path, monkeypatch)
    cfg = fb.load_config()
    assert ap.due(cfg) and ap.push(cfg, _snap())["ok"]
    clock.t += fb.CHECK_EVERY_S
    cfg = fb.load_config()
    assert ap.due(cfg)
    # generated_at differs on every build; nothing another device would see changed
    assert ap.push(cfg, dict(_snap(), generated_at="later"))["skipped"] == "unchanged"
    clock.t += fb.CHECK_EVERY_S
    assert ap.push(fb.load_config(), _snap(month=4300.0))["ok"]
    assert len(pushed) == 2


def test_checks_are_10_minutes_apart(tmp_path, monkeypatch):
    ap, clock, pushed = _pusher(tmp_path, monkeypatch)
    ap.push(fb.load_config(), _snap())
    clock.t += fb.CHECK_EVERY_S - 30
    assert not ap.due(fb.load_config())
    clock.t += 30
    assert ap.due(fb.load_config())


def test_an_unchanged_device_still_pushes_a_heartbeat(tmp_path, monkeypatch):
    ap, clock, pushed = _pusher(tmp_path, monkeypatch)
    ap.push(fb.load_config(), _snap())
    clock.t += fb.HEARTBEAT_S
    assert ap.push(fb.load_config(), _snap())["ok"] and len(pushed) == 2


def test_push_now_within_5_minutes_is_queued_not_sent(tmp_path, monkeypatch):
    """The rules refuse a second write within 5 min; the client waits instead of spending
    a refused request, and pushes as soon as the gap is over."""
    ap, clock, pushed = _pusher(tmp_path, monkeypatch)
    ap.push(fb.load_config(), _snap())
    clock.t += 120
    r = ap.push(fb.load_config(), _snap(label="New name"), force=True)
    assert r["kind"] == "wait" and 180 <= r["retry_in"] <= 200 and "next sync" in r["error"]
    assert len(pushed) == 1 and ap.pending
    # the dashboard's "next in": when the gap allows, not at the 10-min check
    assert ap.state(fb.load_config())["next_in"] == fb.MIN_GAP_S - 120
    assert not ap.due(fb.load_config())
    clock.t += fb.MIN_GAP_S - 120
    assert ap.due(fb.load_config())   # pending: before the 10-min check
    assert ap.push(fb.load_config(), _snap(label="New name"))["ok"] and not ap.pending
    assert pushed[-1]["label"] == "New name"


def test_a_restart_does_not_push_again(tmp_path, monkeypatch):
    ap, clock, pushed = _pusher(tmp_path, monkeypatch)
    ap.push(fb.load_config(), _snap())
    later = fb.AutoPush(clock=clock)        # a new server process
    clock.t += 60
    assert not later.due(fb.load_config())
    clock.t += fb.MIN_GAP_S
    assert later.push(fb.load_config(), _snap())["skipped"] == "unchanged"


def test_a_plan_refusal_backs_off_and_is_shown(tmp_path, monkeypatch):
    ap, clock, pushed = _pusher(tmp_path, monkeypatch,
                                results=[{"ok": False, "kind": "denied", "error": "x"}])
    monkeypatch.setattr(fb, "explain_denial", lambda cfg: ("plan", "no Pro plan"))
    r = ap.push(fb.load_config(), _snap())
    assert r["kind"] == "plan"
    st = ap.state(fb.load_config())
    assert st["error"]["kind"] == "plan" and st["error"]["message"] == "no Pro plan"
    clock.t += fb.CHECK_EVERY_S
    assert not ap.due(fb.load_config())      # an hour, not every check
    ap.clear_backoff("plan")                 # the dashboard saw the plan become Pro
    assert ap.due(fb.load_config())
    assert ap.push(fb.load_config(), _snap())["ok"] and ap.state(fb.load_config())["error"] is None


def test_a_quota_refusal_waits_an_hour(tmp_path, monkeypatch):
    ap, clock, pushed = _pusher(tmp_path, monkeypatch,
                                results=[{"ok": False, "kind": "quota", "error": "quota"}])
    ap.push(fb.load_config(), _snap())
    clock.t += fb.BACKOFF_S["quota"] - 1
    assert not ap.due(fb.load_config())
    clock.t += 1
    assert ap.due(fb.load_config())
