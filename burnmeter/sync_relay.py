"""Burnmeter Pro — self-hostable sync relay.

A tiny zero-knowledge blob store: it holds per-device **ciphertext** snapshots and
hands them back to the same account's other devices. It never sees your passphrase
or plaintext usage. Run it on a small box (your own server / Tailscale node / VPS):

    burnmeter sync-relay --port 8899 --storage ~/.burnmeter-relay

Auth is a per-account Bearer token (any opaque string the account's devices share).

Two modes (one codebase):
  - SELF-HOST (default): every token is Pro — `_plan_for` returns pro/10 devices.
    The free, AGPL, "run your own relay for $0" path.
  - HOSTED billing (`BURNMETER_RELAY_BILLING=1`): `_plan_for` reads a plan-store
    JSON at `BURNMETER_RELAY_PLANS` ({token: {plan, device_limit}}); unknown tokens
    get no sync (device_limit 0). A Lemon Squeezy webhook (later, payment-last) just
    writes that JSON — the relay hot-reloads it by mtime. The webhook/billing wiring
    is the ONLY closed piece; this relay stays public AGPL.

Hardening for multi-tenant hosting (stdlib only): per-token + per-IP token-bucket
rate limiting (429 on abuse), Bearer-token validation, 256 KB blob cap. Still run
it behind a TLS reverse proxy (Caddy/Traefik) — it speaks plain HTTP.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BLOB = 256 * 1024   # 256 KB — snapshots are tiny; reject anything suspicious
MAX_TOKEN = 512         # reject absurd Bearer strings early

# Per-key token-bucket rate limit (requests). Defaults suit a client that pushes
# every ~5 min; tune via env for a busy hosted relay.
RATE_CAPACITY = int(os.environ.get("BURNMETER_RELAY_RATE", "60"))
RATE_REFILL = float(os.environ.get("BURNMETER_RELAY_RATE_REFILL", "1"))   # tokens/sec
_buckets: dict = {}
_buckets_lock = threading.Lock()

# Hot-reloaded plan-store cache (hosted mode).
_plans_cache: dict = {"path": None, "mtime": -1.0, "data": {}}


def _account_dir(storage: Path, token: str) -> Path:
    h = hashlib.sha256(("burnmeter-acct:" + token).encode()).hexdigest()[:24]
    return storage / h


def _valid_token(t: str) -> bool:
    # Opaque Bearer string: non-empty, bounded, printable ASCII, no whitespace.
    return bool(t) and len(t) <= MAX_TOKEN and t.isascii() and t.isprintable() and not any(c.isspace() for c in t)


def _rl_key(token: str) -> str:
    # Never key the bucket by the raw token.
    return "tok:" + hashlib.sha256(token.encode()).hexdigest()[:16]


def _rate_ok(key: str) -> bool:
    now = time.monotonic()
    with _buckets_lock:
        tokens, last = _buckets.get(key, (float(RATE_CAPACITY), now))
        tokens = min(float(RATE_CAPACITY), tokens + (now - last) * RATE_REFILL)
        if tokens < 1.0:
            _buckets[key] = (tokens, now)
            return False
        _buckets[key] = (tokens - 1.0, now)
        return True


def _load_plans() -> dict:
    """Hosted mode plan-store: {token: {plan, device_limit}}. Hot-reloaded by mtime
    so a billing webhook can rewrite the file and the relay picks it up live."""
    path = os.environ.get("BURNMETER_RELAY_PLANS")
    if not path:
        return {}
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return {}
    if _plans_cache["path"] != path or _plans_cache["mtime"] != mtime:
        try:
            _plans_cache["data"] = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _plans_cache["data"] = {}
        _plans_cache["path"] = path
        _plans_cache["mtime"] = mtime
    return _plans_cache["data"]


def _plan_for(token: str) -> dict:
    if os.environ.get("BURNMETER_RELAY_BILLING") == "1":
        # HOSTED: plan comes from the billing-fed allowlist; unknown token = no sync.
        entry = _load_plans().get(token)
        if not entry:
            return {"plan": "free", "device_limit": 0}
        return {"plan": entry.get("plan", "pro"),
                "device_limit": int(entry.get("device_limit", 10))}
    # SELF-HOST: everyone Pro. (BURNMETER_RELAY_FREE_TOKENS gates a token for testing.)
    free = {t for t in os.environ.get("BURNMETER_RELAY_FREE_TOKENS", "").split(",") if t}
    if token in free:
        return {"plan": "free", "device_limit": 0}
    return {"plan": "pro", "device_limit": 10}


def make_handler(storage: Path):
    storage.mkdir(parents=True, exist_ok=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            sys.stderr.write("[burnmeter-relay] %s - %s\n" % (self.address_string(), fmt % args))

        # ---- helpers ----
        def _token(self):
            auth = self.headers.get("Authorization", "")
            return auth[7:].strip() if auth.startswith("Bearer ") else None

        def _json(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _device_id(self):
            # path /v1/snap/<device_id>
            parts = self.path.split("?")[0].rstrip("/").split("/")
            return parts[-1] if len(parts) >= 4 and parts[-2] == "snap" else None

        def _rate_limited(self, key) -> bool:
            if _rate_ok(key):
                return False
            self._json(429, {"error": "rate limit exceeded; slow down"})
            return True

        def _auth(self):
            """Validate + rate-limit an authenticated request. Returns the token, or
            None if an error response was already sent."""
            token = self._token()
            if not token:
                self._json(401, {"error": "missing Bearer token"}); return None
            if not _valid_token(token):
                self._json(400, {"error": "malformed token"}); return None
            if self._rate_limited(_rl_key(token)):
                return None
            return token

        # ---- routes ----
        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/") or "/"
            if path == "/health":
                if self._rate_limited("ip:" + self.address_string()):
                    return
                return self._json(200, {"ok": True, "service": "burnmeter-sync-relay"})
            token = self._auth()
            if token is None:
                return
            adir = _account_dir(storage, token)
            if path == "/v1/account":
                n = len(list(adir.glob("*.json"))) if adir.exists() else 0
                plan = _plan_for(token)
                return self._json(200, {"plan": plan["plan"], "device_count": n,
                                        "device_limit": plan["device_limit"]})
            if path == "/v1/snaps":
                out = {}
                if adir.exists():
                    for f in adir.glob("*.json"):
                        try:
                            out[f.stem] = json.loads(f.read_text())
                        except (OSError, json.JSONDecodeError):
                            continue
                return self._json(200, out)
            return self._json(404, {"error": "not found"})

        def do_PUT(self):
            token = self._auth()
            if token is None:
                return
            dev = self._device_id()
            if not dev:
                return self._json(400, {"error": "bad path; expected /v1/snap/<device_id>"})
            plan = _plan_for(token)
            adir = _account_dir(storage, token)
            existing = len(list(adir.glob("*.json"))) if adir.exists() else 0
            is_new = not (adir / f"{dev}.json").exists()
            if plan["device_limit"] <= 0:
                return self._json(402, {"error": "Pro required for sync", "plan": plan["plan"]})
            if is_new and existing >= plan["device_limit"]:
                return self._json(402, {"error": "device limit reached",
                                        "device_limit": plan["device_limit"]})
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > MAX_BLOB:
                return self._json(413, {"error": "blob too large or empty"})
            blob = self.rfile.read(length)
            adir.mkdir(parents=True, exist_ok=True)
            (adir / f"{dev}.json").write_text(json.dumps({
                "blob_b64": base64.b64encode(blob).decode("ascii"),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }))
            return self._json(201 if is_new else 200, {"ok": True, "device_id": dev})

        def do_DELETE(self):
            token = self._auth()
            if token is None:
                return
            dev = self._device_id()
            if not dev:
                return self._json(400, {"error": "bad path"})
            f = _account_dir(storage, token) / f"{dev}.json"
            try:
                f.unlink()
            except FileNotFoundError:
                pass
            return self._json(200, {"ok": True, "deleted": dev})

    return Handler


def run_relay(host: str = "127.0.0.1", port: int = 8899, storage: Path = None) -> None:
    storage = storage or (Path.home() / ".burnmeter-relay")
    httpd = ThreadingHTTPServer((host, port), make_handler(storage))
    billing = os.environ.get("BURNMETER_RELAY_BILLING") == "1"
    sys.stderr.write(f"[burnmeter-relay] zero-knowledge sync relay → http://{host}:{port}\n")
    sys.stderr.write(f"[burnmeter-relay] storage: {storage} (ciphertext only)\n")
    sys.stderr.write(f"[burnmeter-relay] mode: {'HOSTED billing (plan-store)' if billing else 'self-host (everyone Pro)'}"
                     f" · rate {RATE_CAPACITY}/{RATE_REFILL}/s\n")
    if billing and not os.environ.get("BURNMETER_RELAY_PLANS"):
        sys.stderr.write("[burnmeter-relay] WARNING: BURNMETER_RELAY_BILLING=1 but BURNMETER_RELAY_PLANS unset → all tokens denied\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\n[burnmeter-relay] shutting down\n")
        httpd.shutdown()
