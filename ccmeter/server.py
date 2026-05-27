"""Tiny stdlib HTTP server that serves the dashboard + JSON API.

Local-only by default (binds to 127.0.0.1). No third-party deps. The only
external resource is Chart.js, loaded by the browser from a CDN — if you
want fully air-gapped operation, vendor chart.umd.min.js into ./static.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

from . import __version__
from .parser import CLAUDE_PROJECTS_DIR, load_records
from .analytics import build_report


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class _Cache:
    """Re-parse JSONL no more than once every `ttl_seconds`."""

    def __init__(self, projects_dir: Path, ttl_seconds: int = 15):
        self.projects_dir = projects_dir
        self.ttl = ttl_seconds
        self._lock = threading.Lock()
        self._records = None
        self._stats = None
        self._intents: dict[str, str] = {}
        self._errors: list[dict] = []
        self._loaded_at = 0.0

    def get(self, force: bool = False):
        with self._lock:
            now = time.time()
            if force or self._records is None or (now - self._loaded_at) > self.ttl:
                self._records, self._stats, self._intents, self._errors = load_records(self.projects_dir)
                self._loaded_at = now
            return self._records, self._stats, self._loaded_at, self._intents, self._errors


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict):
    body = json.dumps(payload, default=str, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _file_response(handler: BaseHTTPRequestHandler, path: Path, content_type: str):
    if not path.exists() or not path.is_file():
        handler.send_error(404, "Not found")
        return
    data = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def make_handler(cache: _Cache):

    class Handler(BaseHTTPRequestHandler):
        # Quieter than the default verbose logger.
        def log_message(self, format, *args):
            sys.stderr.write("[ccmeter] %s - %s\n" % (
                self.address_string(), format % args
            ))

        def do_GET(self):
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            path = url.path

            if path in ("/", "/index.html"):
                _file_response(self, STATIC_DIR / "dashboard.html", "text/html; charset=utf-8")
                return

            if path == "/static/dashboard.js":
                _file_response(self, STATIC_DIR / "dashboard.js", "application/javascript; charset=utf-8")
                return

            if path == "/static/dashboard.css":
                _file_response(self, STATIC_DIR / "dashboard.css", "text/css; charset=utf-8")
                return

            if path == "/api/health":
                _json_response(self, 200, {
                    "ok": True,
                    "version": __version__,
                    "projects_dir": str(cache.projects_dir),
                    "projects_dir_exists": cache.projects_dir.exists(),
                })
                return

            # Compact statusline — designed to be pulled by Claude Code's
            # `statusLine.command` and shown live in the prompt. Returns
            # plain text by default, JSON if ?format=json.
            if path == "/api/statusline":
                records, stats, loaded_at, intents, error_events = cache.get()
                report = build_report(records, plan=None, user_intents=intents, error_events=error_events)
                cw = report.get("current_window") or {}
                err = report.get("errors") or {}
                fc = report.get("forecast") or {}
                totals = report.get("totals") or {}

                # Active model = the model used by the latest session.
                active_model = "—"
                sessions = report.get("by_session") or []
                if sessions and sessions[0].get("models"):
                    raw = sessions[0]["models"][-1]
                    if raw and not str(raw).startswith("<"):
                        m = str(raw).lower()
                        match = None
                        for fam in ("opus", "sonnet", "haiku"):
                            if fam in m:
                                import re
                                hit = re.search(r"(opus|sonnet|haiku)-(\d+)-(\d+)", m)
                                if hit:
                                    active_model = f"{hit.group(1).capitalize()} {hit.group(2)}.{hit.group(3)}"
                                    match = True
                                    break
                        if not match:
                            active_model = raw

                burn = fc.get("burn_rate_per_hour_recent", cw.get("cost_per_hour", 0))
                # Verdict dot based on speedometer zones.
                zones = report.get("burn_rate_zones") or {}
                if burn >= (zones.get("heavy", 25)):       dot = "🔴"
                elif burn >= (zones.get("busy", 15)):     dot = "🟠"
                elif burn >= (zones.get("typical", 5)):   dot = "🟡"
                else:                                      dot = "🟢"

                # Block remaining (5h window) — show as mm or h:mm.
                rem_sec = cw.get("remaining_seconds", 0)
                rem_min = max(0, rem_sec // 60)
                if rem_min >= 60:
                    rem_str = f"{rem_min // 60}h{rem_min % 60:02d}m"
                else:
                    rem_str = f"{rem_min}dk"

                # Block-P90-already-past flag — show a warning if blown.
                p90_flag = "⚠️ P90+" if cw.get("block_already_past_p90") else ""

                err24 = err.get("total", 0)
                cache_rate = totals.get("cache_hit_rate", 0) * 100

                # Plain text line by default.
                want_json = qs.get("format", [""])[0] == "json"
                if want_json:
                    _json_response(self, 200, {
                        "verdict": dot,
                        "active_model": active_model,
                        "burn_rate_per_hour": round(burn, 2),
                        "block_remaining_min": int(rem_min),
                        "block_past_p90": bool(cw.get("block_already_past_p90")),
                        "errors_24h": err24,
                        "cache_hit_pct": round(cache_rate, 1),
                        "today_cost": round(fc.get("today", {}).get("so_far", 0), 2),
                    })
                    return
                line = (
                    f"{dot} {active_model} ${burn:.0f}/h · blok {rem_str} {p90_flag}· "
                    f"{err24}err/24h · cache %{cache_rate:.0f} · gün ${fc.get('today', {}).get('so_far', 0):.0f}"
                )
                body = line.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if path == "/api/report":
                force = qs.get("refresh", ["0"])[0] == "1"
                plan = (qs.get("plan", [None])[0] or None)
                records, stats, loaded_at, intents, error_events = cache.get(force=force)
                report = build_report(records, plan=plan, user_intents=intents, error_events=error_events)
                report["_meta"] = {
                    **stats,
                    "loaded_at": loaded_at,
                    "served_at": time.time(),
                    "version": __version__,
                }
                _json_response(self, 200, report)
                return

            if path == "/api/records.csv":
                records, _, _, _, _ = cache.get()
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header(
                    "Content-Disposition",
                    "attachment; filename=ccmeter_records.csv",
                )
                self.end_headers()
                cols = [
                    "timestamp", "session_id", "project_label", "git_branch",
                    "model", "input_tokens", "output_tokens",
                    "cache_creation_tokens", "cache_read_tokens",
                ]
                self.wfile.write((",".join(cols) + "\n").encode("utf-8"))
                for r in records:
                    row = [
                        r.timestamp.isoformat(),
                        r.session_id,
                        r.project_label,
                        r.git_branch,
                        r.model,
                        str(r.input_tokens),
                        str(r.output_tokens),
                        str(r.cache_creation_tokens),
                        str(r.cache_read_tokens),
                    ]
                    safe = [
                        '"' + c.replace('"', '""') + '"' if "," in c or '"' in c else c
                        for c in row
                    ]
                    self.wfile.write((",".join(safe) + "\n").encode("utf-8"))
                return

            self.send_error(404, "Not found")

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8765,
          projects_dir: Optional[Path] = None,
          ttl_seconds: int = 15) -> None:
    projects_dir = Path(projects_dir) if projects_dir else CLAUDE_PROJECTS_DIR
    cache = _Cache(projects_dir, ttl_seconds=ttl_seconds)

    if not projects_dir.exists():
        sys.stderr.write(
            f"[ccmeter] WARNING: {projects_dir} does not exist. "
            "The dashboard will load with empty data until Claude Code writes a session here.\n"
        )

    server = ThreadingHTTPServer((host, port), make_handler(cache))
    sys.stderr.write(f"[ccmeter] v{__version__} → http://{host}:{port}\n")
    sys.stderr.write(f"[ccmeter] reading: {projects_dir}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\n[ccmeter] shutting down\n")
        server.server_close()


def main(argv=None):
    p = argparse.ArgumentParser(prog="ccmeter-serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--projects-dir", default=str(CLAUDE_PROJECTS_DIR))
    p.add_argument("--ttl", type=int, default=15,
                   help="seconds between JSONL re-scans (cache TTL)")
    args = p.parse_args(argv)
    serve(host=args.host, port=args.port,
          projects_dir=Path(args.projects_dir), ttl_seconds=args.ttl)


if __name__ == "__main__":
    main()
