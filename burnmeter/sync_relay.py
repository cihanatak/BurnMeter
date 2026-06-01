"""Burnmeter Pro — self-hostable sync relay.

A tiny zero-knowledge blob store: it holds per-device **ciphertext** snapshots and
hands them back to the same account's other devices. It never sees your passphrase
or plaintext usage. Run it on a small box (your own server / Tailscale node / VPS):

    burnmeter sync-relay --port 8899 --storage ~/.burnmeter-relay

Auth is a per-account Bearer token (any opaque string the account's devices share).
The Pro-gate here is a stub — a self-hosted relay treats every token as Pro. The
HOSTED relay (later phase) swaps `_plan_for` for a billing lookup.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BLOB = 256 * 1024   # 256 KB — snapshots are tiny; reject anything suspicious


def _account_dir(storage: Path, token: str) -> Path:
    h = hashlib.sha256(("burnmeter-acct:" + token).encode()).hexdigest()[:24]
    return storage / h


def _plan_for(token: str) -> dict:
    # STUB. Self-host = everyone Pro. Hosted relay replaces this with a billing lookup
    # (e.g. Lemon Squeezy webhook → plan). Set BURNMETER_RELAY_FREE_TOKENS to test the gate.
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

        # ---- routes ----
        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/") or "/"
            if path == "/health":
                return self._json(200, {"ok": True, "service": "burnmeter-sync-relay"})
            token = self._token()
            if not token:
                return self._json(401, {"error": "missing Bearer token"})
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
            token = self._token()
            if not token:
                return self._json(401, {"error": "missing Bearer token"})
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
            token = self._token()
            if not token:
                return self._json(401, {"error": "missing Bearer token"})
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
    sys.stderr.write(f"[burnmeter-relay] zero-knowledge sync relay → http://{host}:{port}\n")
    sys.stderr.write(f"[burnmeter-relay] storage: {storage} (ciphertext only)\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\n[burnmeter-relay] shutting down\n")
        httpd.shutdown()
