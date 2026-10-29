"""Burnmeter Pro — Firebase-backed cross-device sync (client, REST).

Account/identity = Firebase Auth: email + password, with REAL email verification and
password reset, all hosted by Google (zero backend code of ours). Per-device usage
snapshots live in Cloud Firestore as CIPHERTEXT ONLY — the E2E key is derived from the
password ON THIS machine (sync.derive_enc_key) and never leaves it. Firebase Auth gets a
second key derived from the password with another salt (sync.derive_auth_secret), not the
password, so Google can't derive the E2E key from what it sees either. Accounts made
before 0.3.6 used the password itself; login() moves them over.

What makes sync a Pro feature is firestore.rules (repo root), once deployed: it reads the
account's `plan` from /users/{uid}, which only billing can write, and caps the size and
rate of every device write. The client is open source, so nothing in this file can be
what enforces Pro; the checks here only let the dashboard say why Firebase refused.

Talks to Firebase over its REST APIs with stdlib urllib (no SDK) so the free core stays
dependency-free; only the E2E crypto needs `cryptography` (pip install burnmeter[sync]).
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import threading
import time
import urllib.error
import urllib.request
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


def _err_code(resp) -> str:
    """Firebase's SCREAMING_SNAKE code, without the ' : explanation' some carry."""
    err = resp.get("error") if isinstance(resp, dict) else None
    msg = (err.get("message") if isinstance(err, dict) else None) or ""
    return msg.split(" : ", 1)[0]


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
    return friendly.get(msg, friendly.get(_err_code(resp), msg))


class SyncError(RuntimeError):
    """A Firestore call that failed. kind is one of network, quota, auth, plan,
    unverified, denied, error; the message is worded for the dashboard."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _failure(st: int, r) -> tuple:
    """(kind, message) for a failed Firestore call."""
    err = (r.get("error") or {}) if isinstance(r, dict) else {}
    status = err.get("status") or ""
    if st == 0:
        return "network", "can't reach Firebase; Burnmeter will retry"
    if st == 429 or status == "RESOURCE_EXHAUSTED":
        return "quota", ("Firebase's daily quota is used up; sync resumes when it resets "
                         "(midnight Pacific time)")
    if st == 401 or status == "UNAUTHENTICATED":
        return "auth", "signed out; sign in again to sync"
    if st == 403 or status == "PERMISSION_DENIED":
        return "denied", "Firebase refused the request"
    return "error", f"Firebase HTTP {st}: {err.get('message') or 'unknown error'}"


def _key() -> str:
    return FIREBASE["apiKey"]


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
    cfg.pop("signed_out", None)
    _sync.ensure_device_id(cfg)
    save_config(cfg)
    return cfg


def _sign_in(email: str, secret: str):
    return _req(f"{_IDENTITY}/accounts:signInWithPassword?key={_key()}",
                payload={"email": email, "password": secret, "returnSecureToken": True})


def signup(email: str, password: str) -> dict:
    """Create a Firebase account, send the verification email. Stores the session."""
    if len((password or "")) < 10:
        return {"ok": False, "error": "password must be at least 10 characters "
                                      "(it's the only thing protecting your encrypted data)"}
    email = (email or "").strip().lower()
    st, r = _req(f"{_IDENTITY}/accounts:signUp?key={_key()}",
                 payload={"email": email, "password": _sync.derive_auth_secret(email, password),
                          "returnSecureToken": True})
    if "idToken" not in r:
        return {"ok": False, "error": _err(r)}
    _req(f"{_IDENTITY}/accounts:sendOobCode?key={_key()}",
         payload={"requestType": "VERIFY_EMAIL", "idToken": r["idToken"]})
    _store_session(email, password, r)
    return {"ok": True, "uid": r["localId"], "verified": False, "verification_sent": True}


_WRONG_PASSWORD = {"INVALID_LOGIN_CREDENTIALS", "INVALID_PASSWORD"}


def login(email: str, password: str) -> dict:
    """Sign in to an existing account. Stores the session.

    An account made before 0.3.6 has the password itself as its Firebase password. When
    the derived secret is refused, try the password; if that works, switch the account to
    the derived secret. Firebase then signs the account's other devices out once: 0.3.6
    signs them back in with the same password, older versions can't until updated."""
    email = (email or "").strip().lower()
    secret = _sync.derive_auth_secret(email, password)
    st, r = _sign_in(email, secret)
    migrated = False
    if "idToken" not in r and _err_code(r) in _WRONG_PASSWORD:
        st, r = _sign_in(email, password)
        if "idToken" in r:
            st, upd = _req(f"{_IDENTITY}/accounts:update?key={_key()}",
                           payload={"idToken": r["idToken"], "password": secret,
                                    "returnSecureToken": True})
            if "idToken" in upd:   # the old tokens die with the old password
                r, migrated = {**r, **upd}, True
    if "idToken" not in r:
        return {"ok": False, "error": _err(r)}
    _store_session(email, password, r)
    return {"ok": True, "uid": r["localId"], "verified": _is_verified_token(r["idToken"]),
            "migrated": migrated}


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
    if not tok:
        return False
    v = _is_verified_token(tok)
    if v and not cfg.get("_verified_refreshed"):
        # Right after the user clicks the verification link, the cached ID token still
        # carries email_verified=FALSE in its claims — Firestore rules gate on that claim,
        # so they 403. Force ONE token refresh so the new JWT reflects the verified state.
        valid_token(cfg, force=True)
        cfg["_verified_refreshed"] = True
        save_config(cfg)
    return v


# securetoken's answer when a session can't be refreshed any more: the password changed
# on another device (that revokes every refresh token), or the account is disabled/gone.
_SESSION_ENDED = {"TOKEN_EXPIRED", "INVALID_REFRESH_TOKEN", "USER_DISABLED", "USER_NOT_FOUND"}


def valid_token(cfg: dict, force: bool = False) -> Optional[str]:
    """A fresh idToken, refreshing via the refresh_token when expired (or force=True —
    used after email verification to pick up the updated email_verified claim). Persisted."""
    if not cfg.get("refresh_token") or cfg.get("signed_out"):
        return None
    if not force and cfg.get("id_token") and time.time() < cfg.get("token_expiry", 0):
        return cfg["id_token"]
    st, r = _req(f"{_SECURETOKEN}/token?key={_key()}",
                 payload={"grant_type": "refresh_token", "refresh_token": cfg["refresh_token"]})
    id_token = r.get("id_token") or r.get("access_token")
    if not id_token:
        if _err_code(r) in _SESSION_ENDED:   # the dashboard asks to sign in again
            cfg["signed_out"] = _err_code(r)
            save_config(cfg)
            return None
        return cfg.get("id_token")   # keep the old one rather than nulling on a transient error
    cfg["id_token"] = id_token
    cfg["token_expiry"] = time.time() + int(r.get("expires_in", 3600)) - 60
    if r.get("refresh_token"):
        cfg["refresh_token"] = r["refresh_token"]
    save_config(cfg)
    return id_token


def _firestore(cfg: dict, url: str, method: str = "GET", payload: Optional[dict] = None):
    """Authed Firestore call that retries ONCE with a force-refreshed token on 401/403 —
    handles the just-verified stale-claim case (and any transient token staleness)."""
    tok = valid_token(cfg)
    if not tok:
        return 401, {"error": {"message": "not signed in", "status": "UNAUTHENTICATED"}}
    st, r = _req(url, method=method, payload=payload, token=tok)
    if st in (401, 403):
        tok = valid_token(cfg, force=True)
        if tok:
            st, r = _req(url, method=method, payload=payload, token=tok)
    return st, r


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
    """Plan/status from /users/{uid}; no such document means free. Read-only for the
    client. Raises SyncError when Firestore can't say (it used to report "free")."""
    url = (f"{_FIRESTORE}/projects/{FIREBASE['projectId']}/databases/(default)"
           f"/documents/users/{cfg['uid']}")
    st, r = _firestore(cfg, url, method="GET")
    if st == 404:
        return {"plan": "free", "status": "active"}
    if st != 200:
        raise SyncError(*_failure(st, r))
    fields = (r or {}).get("fields") or {}
    plan = (fields.get("plan") or {}).get("stringValue", "free")
    status = (fields.get("status") or {}).get("stringValue", "active")
    return {"plan": plan, "status": status}


def explain_denial(cfg: dict) -> tuple:
    """(kind, message) for a request firestore.rules refused: the plan, an unverified
    email, or neither (kind "denied")."""
    try:
        acct = account(cfg)
    except SyncError:
        acct = None
    if acct and acct["plan"] != "pro":
        return "plan", "sync is a Pro feature, and this account has no Pro plan"
    if acct and acct["status"] != "active":
        return "plan", f"this account's Pro plan is {acct['status']}, so sync is paused"
    if not is_verified(cfg):
        return "unverified", "verify your email to start syncing"
    return "denied", "Firebase refused the update; Burnmeter will try again"


def push_snapshot(cfg: dict, snapshot: dict) -> dict:
    """Encrypt the snapshot locally and write it to /users/{uid}/devices/{device_id}.

    The doc holds only the ciphertext; the device name travels inside it (0.3.5 also
    wrote a plaintext label, which this write drops). Firestore stamps updated_at with the
    request time, which is what firestore.rules rate-limits on, so a wrong local clock
    can't get a write refused."""
    if not valid_token(cfg):
        return {"ok": False, "kind": "auth", "error": "signed out; sign in again to sync"}
    blob = _sync.encrypt_snapshot(snapshot, cfg)   # uses cfg["enc_key"]
    write = {
        "update": {"name": f"{_devices_collection(cfg['uid'])}/{cfg['device_id']}",
                   "fields": {"blob": {"stringValue": base64.b64encode(blob).decode("ascii")}}},
        "updateTransforms": [{"fieldPath": "updated_at", "setToServerValue": "REQUEST_TIME"}],
    }
    url = f"{_FIRESTORE}/projects/{FIREBASE['projectId']}/databases/(default)/documents:commit"
    st, r = _firestore(cfg, url, method="POST", payload={"writes": [write]})
    if st == 200:
        return {"ok": True, "device_id": cfg["device_id"]}
    kind, msg = _failure(st, r)
    return {"ok": False, "kind": kind, "error": msg, "status": st}


def pull(cfg: dict) -> list:
    """List + decrypt every device snapshot for this account."""
    if not valid_token(cfg):
        raise SyncError("auth", "signed out; sign in again to sync")
    url = f"{_FIRESTORE}/{_devices_collection(cfg['uid'])}"
    st, r = _firestore(cfg, url, method="GET")
    if st != 200:
        kind, msg = _failure(st, r)
        if kind == "denied":
            kind, msg = explain_denial(cfg)
        raise SyncError(kind, msg)
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


# ---------- when to push ----------
# firestore.rules takes one update per device every 5 min, and the free Firebase plan has
# 20k writes a day for every account together; pushing every 60 s (before 0.3.6) spent
# 1,440 a day per device. So: look every 10 min, push only a changed snapshot, and back
# off when Firebase says the plan or the quota is the problem.
CHECK_EVERY_S = 10 * 60
MIN_GAP_S = 5 * 60 + 10       # the rules' 5 min, plus slack for request latency
HEARTBEAT_S = 6 * 60 * 60     # an unchanged snapshot still goes up this often, so a running
                              # device shows as idle, not offline (the dashboard: > 24 h)
BACKOFF_S = {"plan": 60 * 60, "quota": 60 * 60, "auth": 60 * 60}   # others: next check


def snapshot_digest(snapshot: dict) -> str:
    """Identifies what another device would see; generated_at changes on every build."""
    body = {k: v for k, v in snapshot.items() if k != "generated_at"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str)
                          .encode("utf-8")).hexdigest()[:16]


def _ago(s: float) -> str:
    return f"{int(s)} s" if s < 90 else f"{round(s / 60)} min"


def _remember_push(cfg: dict, now: float, digest: str) -> None:
    cfg["last_push_at"], cfg["last_push_digest"] = now, digest
    disk = load_config()   # re-read: another thread may have saved a refreshed token
    if disk.get("uid") and disk.get("uid") == cfg.get("uid"):
        disk.update(last_push_at=now, last_push_digest=digest)
        save_config(disk)


class AutoPush:
    """When this device pushes, and the last failure for the dashboard.

    The server's loop asks due() every 30 s and only then builds a snapshot; Push now and
    a rename call push(force=True). The last push's time and digest live in the config
    file, so a restart doesn't push again."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self.next_check = 0.0
        self.retry_at = 0.0
        self.pending = False   # a forced push waits for the 5-min gap
        self.error = None      # {"kind", "message", "at"} of the last failed push

    def _since_last(self, cfg: dict, now: float) -> float:
        elapsed = now - float(cfg.get("last_push_at") or 0)
        return elapsed if elapsed >= 0 else MIN_GAP_S   # the clock went back: don't stall

    def due(self, cfg: dict) -> bool:
        now = self._clock()
        if now < self.retry_at or self._since_last(cfg, now) < MIN_GAP_S:
            return False
        return self.pending or now >= self.next_check

    def clear_backoff(self, kind: str) -> None:
        """The dashboard saw the problem go away (e.g. the plan is now Pro)."""
        if self.error and self.error["kind"] == kind:
            self.retry_at = 0.0
            self.next_check = 0.0

    def push(self, cfg: dict, snapshot: dict, force: bool = False) -> dict:
        with self._lock:
            now = self._clock()
            since = self._since_last(cfg, now)
            if since < MIN_GAP_S:
                self.pending = self.pending or force
                return {"ok": False, "kind": "wait", "retry_in": int(MIN_GAP_S - since) + 1,
                        "error": (f"synced {_ago(since)} ago; Firebase takes one update per "
                                  f"device every 5 min, so the next sync is in "
                                  f"{_ago(MIN_GAP_S - since)}")}
            self.next_check = now + CHECK_EVERY_S
            digest = snapshot_digest(snapshot)
            if (not (force or self.pending) and digest == cfg.get("last_push_digest")
                    and since < HEARTBEAT_S):
                return {"ok": True, "skipped": "unchanged"}
            res = push_snapshot(cfg, snapshot)
            if res.get("ok"):
                self.pending, self.retry_at, self.error = False, 0.0, None
                _remember_push(cfg, now, digest)
                return res
            if res.get("kind") == "denied":
                kind, msg = explain_denial(cfg)
                res.update(kind=kind, error=msg)
            self.pending = self.pending or force
            self.retry_at = now + BACKOFF_S.get(res.get("kind"), 0)
            self.error = {"kind": res.get("kind"), "message": res.get("error"), "at": now}
            return res

    def state(self, cfg: dict) -> dict:
        """For /api/pro/status: the last good push, the last failure, the next attempt."""
        now = self._clock()
        last = float(cfg.get("last_push_at") or 0)
        # a queued push goes as soon as the gap allows; otherwise at the next check
        nxt = max(self.retry_at, last + MIN_GAP_S) if self.pending else max(self.next_check, self.retry_at)
        return {"last_ok_at": last or None, "error": self.error, "pending": self.pending,
                "next_in": max(0, int(nxt - now))}
