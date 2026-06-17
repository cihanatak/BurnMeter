"""Burnmeter Pro — cross-device sync (client).

Privacy model (matches the local-first promise): the relay only ever stores
**ciphertext**. Each device encrypts a tiny summary snapshot with a key derived
from a passphrase that NEVER leaves your machines. The relay cannot read your
usage. Other devices pull the ciphertext and decrypt locally.

Dependency note: the free Burnmeter core is zero-dependency. E2E encryption needs
`cryptography` (Fernet/AES) — imported lazily here only, so installing it is opt-in
via `pip install burnmeter[sync]`. Key derivation (scrypt) is Python stdlib.
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import unicodedata
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path.home() / ".config" / "burnmeter" / "sync.json"
SNAPSHOT_VERSION = 2   # v2: + bounded recent-turns tail (metadata only) so cross-device
                       # "recent activity" works WITHOUT mirroring raw logs.
SNAPSHOT_RECENT_MAX = 25   # last N turns per source carried in the snapshot (a few KB)


# ---------- config ----------
def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    try:
        CONFIG_PATH.chmod(0o600)   # contains the passphrase + token
    except OSError:
        pass


def is_configured(cfg: Optional[dict] = None) -> bool:
    c = cfg if cfg is not None else load_config()
    return bool(c.get("relay_url") and c.get("account_token") and c.get("enc_key"))


# ---------- E2E crypto + password-derived auth (zero-knowledge) ----------
# scrypt n=2**17 (~128 MiB) — a deliberately expensive, memory-hard derivation so that
# even if the relay's account file is stolen, brute-forcing a password offline is slow.
# Login derivation happens rarely (per device sign-in), so the ~0.2-0.4s cost is fine.
_SCRYPT_N = 2 ** 17
_SCRYPT_MAXMEM = 256 * 1024 * 1024


def _norm_email(email: str) -> str:
    # MUST match the relay's _normalize_email (NFKC + strip + lower) or derived keys won't match.
    return unicodedata.normalize("NFKC", (email or "")).strip().lower()


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=8, p=1,
                          dklen=32, maxmem=_SCRYPT_MAXMEM)


def derive_auth_secret(email: str, password: str) -> str:
    """The sync BEARER + login credential, derived from the password. The relay only
    ever stores its HASH (peppered) — never the password. Salt from email so every
    device derives the same value."""
    salt = hashlib.sha256(("burnmeter-auth-v1:" + _norm_email(email)).encode()).digest()
    return base64.urlsafe_b64encode(_scrypt(password, salt)).decode("ascii")


def derive_enc_key(email: str, password: str) -> bytes:
    """The E2E key (Fernet). NEVER leaves the machine; a DIFFERENT salt label from the
    auth secret, so the relay (which receives the auth secret) cannot derive it."""
    salt = hashlib.sha256(("burnmeter-enc-v1:" + _norm_email(email)).encode()).digest()
    return base64.urlsafe_b64encode(_scrypt(password, salt))


def _fernet(cfg: dict):
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:
        raise RuntimeError(
            "E2E sync needs the 'cryptography' package. Install with:\n"
            "    pip install burnmeter[sync]\n"
            "(The free Burnmeter core stays dependency-free; only sync needs it.)"
        ) from e
    key = cfg.get("enc_key")
    if not key:
        raise RuntimeError("Pro not connected — run `burnmeter sync login` first.")
    return Fernet(key.encode("ascii") if isinstance(key, str) else key)


def encrypt_snapshot(snapshot: dict, cfg: dict) -> bytes:
    return _fernet(cfg).encrypt(json.dumps(snapshot, separators=(",", ":")).encode("utf-8"))


def decrypt_snapshot(blob: bytes, cfg: dict) -> dict:
    return json.loads(_fernet(cfg).decrypt(blob).decode("utf-8"))


# ---------- snapshot (small; raw logs NEVER leave the machine) ----------
def _summarize(report: dict) -> dict:
    fc = report.get("forecast") or {}
    cw = report.get("current_window") or {}
    totals = report.get("totals") or {}
    # Bounded recent-turns tail — metadata ONLY (no message content, no tool output).
    # This is what lets another device show this machine's recent activity without
    # ever shipping the raw ~/.codex / ~/.claude logs. Capped + tiny (a few KB).
    recent = [
        {
            "ts": t.get("timestamp"),
            "project": t.get("project_label"),
            "model": t.get("model"),
            "tokens": t.get("total_tokens", 0) or 0,
            "cost": round(t.get("cost_usd", 0) or 0, 4),
        }
        for t in (report.get("recent_turns") or [])[:SNAPSHOT_RECENT_MAX]
    ]
    return {
        "record_count": report.get("record_count", 0),
        "burn_rate_per_hour": (fc.get("burn_rate_per_hour_recent") or 0),
        "today_cost": round((fc.get("today", {}) or {}).get("so_far", 0) or 0, 2),
        "month_so_far": round((fc.get("month", {}) or {}).get("so_far", 0) or 0, 2),
        "month_projected": round((fc.get("month", {}) or {}).get("projected_eom", 0) or 0, 2),
        "lifetime_cost": round(totals.get("cost_usd", 0) or 0, 2),
        "lifetime_tokens": totals.get("total_tokens", 0),
        "cache_hit_rate": round(totals.get("cache_hit_rate", 0) or 0, 4),
        "rate_limit_pct": (cw.get("block_pct_used") if cw else None),
        "recent": recent,
    }


def build_device_snapshot(cfg: dict, projects_dir: Path, codex_dir: Optional[Path]) -> dict:
    """Tiny per-device summary built from the SAME analytics the dashboard uses.
    Only aggregates leave the machine — never the raw ~/.claude or ~/.codex logs."""
    from .parser import load_records
    from .analytics import build_report

    sources = {}
    try:
        records, _stats, intents, errors = load_records(projects_dir)
        if records:
            sources["claude"] = _summarize(build_report(records, user_intents=intents,
                                                         error_events=errors, source="claude"))
    except Exception:
        pass
    if codex_dir:
        try:
            from .codex_parser import load_codex_records
            crecords, _cstats, cintents = load_codex_records(codex_dir)
            if crecords:
                sources["codex"] = _summarize(build_report(crecords, user_intents=cintents,
                                                            source="codex"))
        except Exception:
            pass

    return {
        "v": SNAPSHOT_VERSION,
        "device_id": cfg["device_id"],
        "label": cfg.get("label") or socket.gethostname(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
    }


# ---------- relay HTTP (stdlib) ----------
def _req(cfg: dict, method: str, path: str, body: Optional[bytes] = None, timeout: int = 15):
    url = cfg["relay_url"].rstrip("/") + path
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", "Bearer " + cfg["account_token"])
    if body is not None:
        req.add_header("Content-Type", "application/octet-stream")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def push(cfg: dict, projects_dir: Path, codex_dir: Optional[Path]) -> dict:
    snap = build_device_snapshot(cfg, projects_dir, codex_dir)
    blob = encrypt_snapshot(snap, cfg)
    status, _ = _req(cfg, "PUT", "/v1/snap/" + cfg["device_id"], body=blob)
    return {"ok": status in (200, 201), "status": status,
            "device_id": cfg["device_id"], "sources": list(snap["sources"].keys())}


def _assemble_snapshot(cfg: dict, sources: dict) -> dict:
    return {
        "v": SNAPSHOT_VERSION,
        "device_id": cfg["device_id"],
        "label": cfg.get("label") or socket.gethostname(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
    }


def snapshot_from_reports(cfg: dict, reports: dict) -> dict:
    """Build a snapshot from ALREADY-built report dicts (no re-parse). Used by the
    server's auto-push so it reuses its cached reports instead of reloading logs."""
    sources = {s: _summarize(r) for s, r in (reports or {}).items() if r and r.get("record_count")}
    return _assemble_snapshot(cfg, sources)


def push_snapshot(cfg: dict, snapshot: dict) -> dict:
    blob = encrypt_snapshot(snapshot, cfg)
    status, _ = _req(cfg, "PUT", "/v1/snap/" + cfg["device_id"], body=blob)
    return {"ok": status in (200, 201), "status": status, "device_id": cfg["device_id"],
            "sources": list(snapshot.get("sources", {}).keys())}


def pull(cfg: dict) -> list[dict]:
    status, body = _req(cfg, "GET", "/v1/snaps")
    if status != 200:
        raise RuntimeError(f"relay GET /v1/snaps → HTTP {status}")
    payload = json.loads(body.decode("utf-8"))   # {device_id: {blob_b64, updated_at}}
    out = []
    for dev_id, entry in payload.items():
        try:
            snap = decrypt_snapshot(base64.b64decode(entry["blob_b64"]), cfg)
            snap["_updated_at"] = entry.get("updated_at")
            out.append(snap)
        except Exception:
            out.append({"device_id": dev_id, "_undecryptable": True,
                        "_updated_at": entry.get("updated_at")})
    return out


def account(cfg: dict) -> dict:
    status, body = _req(cfg, "GET", "/v1/account")
    if status != 200:
        raise RuntimeError(f"relay GET /v1/account → HTTP {status}")
    return json.loads(body.decode("utf-8"))


def ensure_device_id(cfg: dict) -> dict:
    if not cfg.get("device_id"):
        cfg["device_id"] = uuid.uuid4().hex[:12]
    if not cfg.get("label"):
        cfg["label"] = socket.gethostname()
    return cfg


# ---------- email + password connect (activate / sign in) ----------
def _post(relay_url: str, path: str, payload: dict, timeout: int = 20):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(relay_url.rstrip("/") + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": f"HTTP {e.code}"}
    except Exception as e:
        return 0, {"error": str(e)}


def connect(relay_url: str, email: str, password: str, code: Optional[str] = None,
            label: Optional[str] = None) -> dict:
    """Activate (with a one-time code) or sign in (without). Derives the secrets LOCALLY,
    authenticates to the relay, and stores config. The password is never stored or sent —
    only the derived auth_secret goes to the relay (over TLS in production)."""
    if len((password or "")) < 10:
        return {"ok": False, "error": "password must be at least 10 characters "
                                      "(it's the only thing protecting your encrypted data)"}
    email = _norm_email(email)
    auth_secret = derive_auth_secret(email, password)
    enc_key = derive_enc_key(email, password).decode("ascii")
    if code:
        path, body = "/v1/activate", {"email": email, "code": code, "auth_secret": auth_secret}
    else:
        path, body = "/v1/login", {"email": email, "auth_secret": auth_secret}
    status, resp = _post(relay_url, path, body)
    if not resp.get("ok"):
        return {"ok": False, "error": resp.get("error", f"HTTP {status}")}
    cfg = load_config()
    cfg.update({"relay_url": relay_url.rstrip("/"), "email": email,
                "account_token": auth_secret, "enc_key": enc_key})
    if label:
        cfg["label"] = label
    ensure_device_id(cfg)
    save_config(cfg)
    return {"ok": True, "plan": resp.get("plan"), "device_limit": resp.get("device_limit"),
            "device_id": cfg["device_id"]}
