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


def _mock(monkeypatch, table):
    """table: list of (url_substring, method, (status, dict))."""
    def fake(url, method="POST", payload=None, token=None, timeout=20):
        for sub, m, resp in table:
            if sub in url and (m is None or m == method):
                return resp
        return (404, {"error": {"message": "no mock for " + url}})
    monkeypatch.setattr(fb, "_req", fake)


def test_signup_sends_verification_and_stores_no_password(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "CONFIG_PATH", tmp_path / "firebase.json")
    _mock(monkeypatch, [
        ("accounts:signUp", None, (200, {"localId": "uid1", "idToken": "id1",
                                         "refreshToken": "rt1", "expiresIn": "3600"})),
        ("sendOobCode", None, (200, {})),
    ])
    r = fb.signup("A@B.com", "a-strong-password")
    assert r["ok"] and r["verified"] is False and r["verification_sent"]
    cfg = fb.load_config()
    assert cfg["uid"] == "uid1" and cfg["refresh_token"] == "rt1"
    assert cfg["email"] == "a@b.com" and cfg["enc_key"] and cfg["device_id"]
    assert "a-strong-password" not in json.dumps(cfg)   # password NEVER stored


def test_signup_short_password_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "CONFIG_PATH", tmp_path / "firebase.json")
    assert fb.signup("a@b.com", "short")["ok"] is False


def test_login_error_is_friendly(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "CONFIG_PATH", tmp_path / "firebase.json")
    _mock(monkeypatch, [("signInWithPassword", None,
                         (400, {"error": {"message": "INVALID_LOGIN_CREDENTIALS"}}))])
    r = fb.login("a@b.com", "whatever-pw")
    assert r["ok"] is False and "wrong email or password" in r["error"]


def test_push_pull_roundtrip_ciphertext_only(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "CONFIG_PATH", tmp_path / "firebase.json")
    cfg = {"email": "a@b.com", "uid": "uid1", "refresh_token": "rt1", "id_token": "id1",
           "token_expiry": 9e18,
           "enc_key": sync.derive_enc_key("a@b.com", "a-strong-password").decode("ascii"),
           "device_id": "dev1", "label": "PC1"}
    fb.save_config(cfg)
    stored = {}

    def fake(url, method="POST", payload=None, token=None, timeout=20):
        assert token == "id1"                       # Firestore call carries the bearer
        if method == "PATCH":
            stored["fields"] = payload["fields"]; return (200, {})
        if method == "GET" and url.rstrip("/").endswith("/devices"):
            return (200, {"documents": [{"name": "x/users/uid1/devices/dev1",
                                         "fields": stored["fields"]}]})
        return (200, {})
    monkeypatch.setattr(fb, "_req", fake)

    snap = {"v": 2, "device_id": "dev1", "label": "PC1",
            "sources": {"claude": {"month_so_far": 4242.0, "record_count": 9}}}
    assert fb.push_snapshot(fb.load_config(), snap)["ok"]
    # what hits Firestore is CIPHERTEXT — the plaintext value must not appear
    blob = base64.b64decode(stored["fields"]["blob"]["stringValue"])
    assert b"4242" not in blob
    devs = fb.pull(fb.load_config())
    assert len(devs) == 1 and devs[0]["sources"]["claude"]["month_so_far"] == 4242.0


def test_is_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "CONFIG_PATH", tmp_path / "firebase.json")
    assert fb.is_configured({}) is False
    assert fb.is_configured({"refresh_token": "x", "uid": "u", "enc_key": "k", "email": "e"}) is True
