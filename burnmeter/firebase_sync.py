"""Burnmeter Pro — Firebase-backed cross-device sync (client, REST).

Account/identity = Firebase Auth: email + password, with REAL email verification and
password reset, all hosted by Google (zero backend code of ours). Per-device usage
snapshots live in Cloud Firestore as CIPHERTEXT ONLY — the E2E key is derived from the
password ON THIS machine (sync.derive_enc_key) and never leaves it, so Firebase/Google
cannot read your usage. Pro entitlement is gated server-side by the account's `plan`
field (writable only by billing), enforced in Firestore Security Rules.

Talks to Firebase over its REST APIs with stdlib urllib (no SDK) so the free core stays
dependency-free; only the E2E crypto needs `cryptography` (pip install burnmeter[sync]).
"""
from __future__ import annotations

import base64
import json
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .firebase_config import FIREBASE
from . import sync as _sync   # reuse derive_enc_key + Fernet encrypt/decrypt + snapshot builders

CONFIG_PATH = Path.home() / ".config" / "burnmeter" / "firebase.json"
_IDENTITY = "https://identitytoolkit.googleapis.com/v1"
_SECURETOKEN = "https://securetoken.googleapis.com/v1"
_FIRESTORE = "https://firestore.googleapis.com/v1"


# ---------- low-level HTTP ----------
def _req(url: str, method: str = "POST", payload: Optional[dict] = None,
         token: Optional[str] = None, timeout: int = 20):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8")
            return r.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": {"message": f"HTTP {e.code}"}}
    except Exception as e:
        return 0, {"error": {"message": str(e)}}


def _err(resp: dict) -> str:
    msg = (resp.get("error") or {}).get("message") or "unknown error"
    # Firebase returns SCREAMING_SNAKE codes — make the common ones human.
    friendly = {
        "EMAIL_EXISTS": "that email already has an account — sign in instead",
        "EMAIL_NOT_FOUND": "wrong email or password",
        "INVALID_PASSWORD": "wrong email or password",
        "INVALID_LOGIN_CREDENTIALS": "wrong email or password",
        "INVALID_EMAIL": "that doesn't look like a valid email",
        "WEAK_PASSWORD : Password should be at least 6 characters": "password is too weak",
        "TOO_MANY_ATTEMPTS_TRY_LATER": "too many attempts — try again in a bit",
    }
    return friendly.get(msg, msg)


def _key() -> str:
    return FIREBASE["apiKey"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------- local config (no password stored; idToken refreshable) ----------
def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass


def is_configured(cfg: Optional[dict] = None) -> bool:
    c = cfg if cfg is not None else load_config()
    return bool(c.get("refresh_token") and c.get("uid") and c.get("enc_key") and c.get("email"))


# ---------- auth (Identity Toolkit REST) ----------
def _store_session(email: str, password: str, r: dict) -> dict:
    cfg = load_config()
    cfg.update({
        "email": email,
        "uid": r["localId"],
        "id_token": r["idToken"],
        "refresh_token": r["refreshToken"],
        "token_expiry": time.time() + int(r.get("expiresIn", 3600)) - 60,
        # E2E key derived locally from the password; the password itself is NEVER stored.
        "enc_key": _sync.derive_enc_key(email, password).decode("ascii"),
    })
    _sync.ensure_device_id(cfg)
    save_config(cfg)
    return cfg


def signup(email: str, password: str) -> dict:
    """Create a Firebase account, send the verification email. Stores the session."""
    if len((password or "")) < 10:
        return {"ok": False, "error": "password must be at least 10 characters "
                                      "(it's the only thing protecting your encrypted data)"}
    email = (email or "").strip().lower()
    st, r = _req(f"{_IDENTITY}/accounts:signUp?key={_key()}",
                 payload={"email": email, "password": password, "returnSecureToken": True})
    if "idToken" not in r:
        return {"ok": False, "error": _err(r)}
    _req(f"{_IDENTITY}/accounts:sendOobCode?key={_key()}",
         payload={"requestType": "VERIFY_EMAIL", "idToken": r["idToken"]})
    _store_session(email, password, r)
    return {"ok": True, "uid": r["localId"], "verified": False, "verification_sent": True}


def login(email: str, password: str) -> dict:
    """Sign in to an existing account. Stores the session."""
    email = (email or "").strip().lower()
    st, r = _req(f"{_IDENTITY}/accounts:signInWithPassword?key={_key()}",
                 payload={"email": email, "password": password, "returnSecureToken": True})
    if "idToken" not in r:
        return {"ok": False, "error": _err(r)}
    cfg = _store_session(email, password, r)
    return {"ok": True, "uid": r["localId"], "verified": _is_verified_token(r["idToken"])}


def resend_verification(cfg: Optional[dict] = None) -> dict:
    cfg = cfg if cfg is not None else load_config()
    tok = valid_token(cfg)
    if not tok:
        return {"ok": False, "error": "not signed in"}
    st, r = _req(f"{_IDENTITY}/accounts:sendOobCode?key={_key()}",
                 payload={"requestType": "VERIFY_EMAIL", "idToken": tok})
    return {"ok": "error" not in r, "error": (None if "error" not in r else _err(r))}


def _is_verified_token(id_token: str) -> bool:
    st, r = _req(f"{_IDENTITY}/accounts:lookup?key={_key()}", payload={"idToken": id_token})
    users = r.get("users") or [{}]
    return bool(users[0].get("emailVerified"))


def is_verified(cfg: Optional[dict] = None) -> bool:
    cfg = cfg if cfg is not None else load_config()
    tok = valid_token(cfg)
    return _is_verified_token(tok) if tok else False


def valid_token(cfg: dict) -> Optional[str]:
    """A fresh idToken, refreshing via the refresh_token when expired (persisted)."""
    if not cfg.get("refresh_token"):
        return None
    if cfg.get("id_token") and time.time() < cfg.get("token_expiry", 0):
        return cfg["id_token"]
    st, r = _req(f"{_SECURETOKEN}/token?key={_key()}",
                 payload={"grant_type": "refresh_token", "refresh_token": cfg["refresh_token"]})
    id_token = r.get("id_token") or r.get("access_token")
    if not id_token:
        return None
    cfg["id_token"] = id_token
    cfg["token_expiry"] = time.time() + int(r.get("expires_in", 3600)) - 60
    if r.get("refresh_token"):
        cfg["refresh_token"] = r["refresh_token"]
    save_config(cfg)
    return id_token


def logout() -> None:
    try:
        CONFIG_PATH.unlink()
    except FileNotFoundError:
        pass


# ---------- Firestore REST (per-uid encrypted device blobs) ----------
def _devices_collection(uid: str) -> str:
    return (f"projects/{FIREBASE['projectId']}/databases/(default)/documents"
            f"/users/{uid}/devices")


def account(cfg: dict) -> dict:
    """Plan/status from /users/{uid} (may not exist yet → free). Read-only for the client."""
    tok = valid_token(cfg)
    if not tok:
        return {"plan": "free"}
    url = (f"{_FIRESTORE}/projects/{FIREBASE['projectId']}/databases/(default)"
           f"/documents/users/{cfg['uid']}")
    st, r = _req(url, method="GET", token=tok)
    fields = (r or {}).get("fields") or {}
    plan = (fields.get("plan") or {}).get("stringValue", "free")
    status = (fields.get("status") or {}).get("stringValue", "active")
    return {"plan": plan, "status": status}


def push_snapshot(cfg: dict, snapshot: dict) -> dict:
    """Encrypt the snapshot locally and upsert it at /users/{uid}/devices/{device_id}."""
    tok = valid_token(cfg)
    if not tok:
        return {"ok": False, "error": "not signed in"}
    blob = _sync.encrypt_snapshot(snapshot, cfg)   # uses cfg["enc_key"]
    doc = {"fields": {
        "blob": {"stringValue": base64.b64encode(blob).decode("ascii")},
        "label": {"stringValue": cfg.get("label") or socket.gethostname()},
        "updated_at": {"timestampValue": _now_iso()},
    }}
    url = f"{_FIRESTORE}/{_devices_collection(cfg['uid'])}/{cfg['device_id']}"
    st, r = _req(url, method="PATCH", payload=doc, token=tok)
    if st in (200, 201):
        return {"ok": True, "device_id": cfg["device_id"]}
    return {"ok": False, "error": _err(r) if isinstance(r, dict) else f"HTTP {st}",
            "status": st}


def pull(cfg: dict) -> list:
    """List + decrypt every device snapshot for this account."""
    tok = valid_token(cfg)
    if not tok:
        raise RuntimeError("not signed in")
    url = f"{_FIRESTORE}/{_devices_collection(cfg['uid'])}"
    st, r = _req(url, method="GET", token=tok)
    if st != 200:
        raise RuntimeError(f"firestore list → HTTP {st}: {_err(r) if isinstance(r, dict) else ''}")
    out = []
    for doc in (r.get("documents") or []):
        f = doc.get("fields") or {}
        dev_id = doc.get("name", "").rsplit("/", 1)[-1]
        b64 = (f.get("blob") or {}).get("stringValue")
        updated = (f.get("updated_at") or {}).get("timestampValue")
        if not b64:
            continue
        try:
            snap = _sync.decrypt_snapshot(base64.b64decode(b64), cfg)
            snap["_updated_at"] = updated
            out.append(snap)
        except Exception:
            out.append({"device_id": dev_id, "_undecryptable": True, "_updated_at": updated})
    return out
