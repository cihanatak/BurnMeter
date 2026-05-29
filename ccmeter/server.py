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
from .codex_parser import CODEX_SESSIONS_DIR, load_codex_records
from .analytics import build_report


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class _Cache:
    """Re-parse JSONL no more than once every `ttl_seconds`.

    `loader` is a callable returning (records, stats, intents, errors). Default
    loads Claude data; codex_meter passes a Codex loader. This keeps the
    analytics pipeline source-agnostic — same build_report on either record set.
    """

    def __init__(self, projects_dir: Path, ttl_seconds: int = 15,
                 extra_roots: Optional[list[Path]] = None, loader=None):
        self.projects_dir = projects_dir
        self.extra_roots = extra_roots or []
        self.ttl = ttl_seconds
        self._loader = loader or (
            lambda: load_records(self.projects_dir, extra_roots=self.extra_roots)
        )
        self._lock = threading.Lock()
        self._records = None
        self._stats = None
        self._intents: dict[str, str] = {}
        self._errors: list[dict] = []
        self._loaded_at = 0.0
        # Built-report memo — build_report is expensive on large record sets
        # (Codex 525K records ≈ 16s). Cache the BUILT report for TTL so only
        # the first request after expiry pays the cost; the rest are instant.
        self._report = None
        self._report_key = None
        self._report_at = 0.0

    def get(self, force: bool = False):
        with self._lock:
            now = time.time()
            if force or self._records is None or (now - self._loaded_at) > self.ttl:
                self._records, self._stats, self._intents, self._errors = self._loader()
                self._loaded_at = now
                self._report = None   # records changed → invalidate report memo
            return self._records, self._stats, self._loaded_at, self._intents, self._errors

    def get_report(self, build_fn, key, force: bool = False):
        """Return a memoized built report. build_fn(records, stats, intents,
        errors) → report. Rebuilt when records reload or `key` changes.

        RAM disiplini (commercial): rapor kurulduktan sonra ham record'lar
        (yarım milyon+ obje) bellekte tutulmaz — atılır, sadece kompakt rapor
        kalır. Bir sonraki TTL'de tekrar yüklenir. Aralarda RSS düşük kalır."""
        now = time.time()
        # Rapor hâlâ taze mi? Öyleyse record'lara HİÇ dokunma (reload etme) —
        # bu sayede discard edilmiş record'lar gereksiz yere geri yüklenmez.
        if (not force and self._report is not None and self._report_key == key
                and (now - self._report_at) <= self.ttl):
            return self._report
        # Rebuild gerek: record'ları yükle, raporu kur, sonra record'ları at.
        records, stats, loaded_at, intents, errors = self.get(force=force)
        with self._lock:
            self._report = build_fn(records, stats, loaded_at, intents, errors)
            self._report_key = key
            self._report_at = time.time()
            self._records = None      # ham record'ları bırak → RSS düşük kalsın
            records = None
            import gc
            gc.collect()
            return self._report


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


def make_handler(cache: _Cache, codex_cache: Optional[_Cache] = None):

    def pick_cache(qs):
        src = (qs.get("source", ["claude"])[0] or "claude").lower()
        if src == "codex" and codex_cache is not None:
            return codex_cache, "codex"
        return cache, "claude"

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
                sel_cache, source = pick_cache(qs)

                def _build(records, stats, loaded_at, intents, error_events):
                    rep = build_report(records, plan=plan, user_intents=intents,
                                       error_events=error_events, source=source)
                    rep["_meta"] = {
                        **stats, "source": source, "loaded_at": loaded_at,
                        "version": __version__,
                    }
                    if source == "codex" and stats.get("rate_limits"):
                        rep["codex_rate_limits"] = stats["rate_limits"]
                    return rep

                report = sel_cache.get_report(_build, key=(source, plan), force=force)
                report["_meta"]["served_at"] = time.time()
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
          ttl_seconds: int = 15,
          extra_roots: Optional[list[Path]] = None,
          codex_dir: Optional[Path] = None,
          codex_extra_roots: Optional[list[Path]] = None) -> None:
    projects_dir = Path(projects_dir) if projects_dir else CLAUDE_PROJECTS_DIR
    cache = _Cache(projects_dir, ttl_seconds=ttl_seconds, extra_roots=extra_roots)

    # codex_meter — second source. Codex sessions are huge (16GB), so a longer
    # TTL + the parser's own incremental (path,mtime,size) cache keep it cheap.
    codex_root = Path(codex_dir) if codex_dir else CODEX_SESSIONS_DIR
    codex_cache = _Cache(
        codex_root,
        ttl_seconds=max(ttl_seconds, 60),
        extra_roots=codex_extra_roots,
        loader=lambda: load_codex_records(codex_root, extra_roots=codex_extra_roots) + ([],),
    )

    if not projects_dir.exists():
        sys.stderr.write(
            f"[ccmeter] WARNING: {projects_dir} does not exist. "
            "The dashboard will load with empty data until Claude Code writes a session here.\n"
        )

    server = ThreadingHTTPServer((host, port), make_handler(cache, codex_cache))
    sys.stderr.write(f"[ccmeter] v{__version__} → http://{host}:{port}\n")
    sys.stderr.write(f"[ccmeter] reading (claude): {projects_dir}\n")
    sys.stderr.write(f"[ccmeter] reading (codex):  {codex_root} (exists={codex_root.exists()})\n")
    for er in (extra_roots or []):
        sys.stderr.write(f"[ccmeter] + claude extra: {er} (exists={er.exists()})\n")
    for er in (codex_extra_roots or []):
        sys.stderr.write(f"[ccmeter] + codex extra:  {er} (exists={er.exists()})\n")
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
    p.add_argument("--extra-projects-dir", action="append", default=[],
                   help="ek JSONL dizinleri (örn. PC'den Syncthing'le mirror'lanmış "
                        "~/.claude/projects-pc/). Birden çok için flag'i tekrarla.")
    p.add_argument("--ttl", type=int, default=15,
                   help="seconds between JSONL re-scans (cache TTL)")
    args = p.parse_args(argv)
    extras = [Path(p).expanduser() for p in (args.extra_projects_dir or [])]
    serve(host=args.host, port=args.port,
          projects_dir=Path(args.projects_dir), ttl_seconds=args.ttl,
          extra_roots=extras)


if __name__ == "__main__":
    main()
