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
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path.home() / ".config" / "burnmeter" / "sync.json"
SNAPSHOT_VERSION = 1


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
    return bool(c.get("relay_url") and c.get("account_token") and c.get("passphrase"))


# ---------- E2E crypto ----------
def _derive_key(passphrase: str, account_token: str) -> bytes:
    """32-byte key from passphrase, salted deterministically by account so every
    device with the same passphrase + account derives the SAME key (can decrypt
    each other). scrypt is stdlib + slow enough to blunt offline guessing."""
    salt = hashlib.sha256(("burnmeter-sync-v1:" + account_token).encode()).digest()[:16]
    raw = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return base64.urlsafe_b64encode(raw)   # Fernet wants a urlsafe-b64 32-byte key


def _fernet(cfg: dict):
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:
        raise RuntimeError(
            "E2E sync needs the 'cryptography' package. Install with:\n"
            "    pip install burnmeter[sync]\n"
            "(The free Burnmeter core stays dependency-free; only sync needs it.)"
        ) from e
    return Fernet(_derive_key(cfg["passphrase"], cfg["account_token"]))


def encrypt_snapshot(snapshot: dict, cfg: dict) -> bytes:
    return _fernet(cfg).encrypt(json.dumps(snapshot, separators=(",", ":")).encode("utf-8"))


def decrypt_snapshot(blob: bytes, cfg: dict) -> dict:
    return json.loads(_fernet(cfg).decrypt(blob).decode("utf-8"))


# ---------- snapshot (small; raw logs NEVER leave the machine) ----------
def _summarize(report: dict) -> dict:
    fc = report.get("forecast") or {}
    cw = report.get("current_window") or {}
    totals = report.get("totals") or {}
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
