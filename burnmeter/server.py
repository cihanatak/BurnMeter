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


STATIC_DIR = Path(__file__).resolve().parent / "static"   # burnmeter/static (paket içi — pip-install'da da bulunur)
LANDING_FILE = STATIC_DIR.parent.parent / "docs" / "index.html"   # repo_root/docs/index.html — web-deploy ile TEK kaynak (drift yok); pip-install'da yoksa graceful 404


class _Cache:
    """Memoized report cache backed by a short-lived BUILD WORKER subprocess.

    RAM disiplini (commercial-grade): the heavy load+build (~half-a-million
    records, ~800MB transient) runs in `python -m burnmeter._worker`, which prints
    the report JSON and EXITS — so the OS reclaims that memory immediately. This
    long-running server only ever caches the small report JSON (~0.5MB). RSS
    stays tiny and bounded; no streaming-aggregate rewrite, zero logic change.

    `worker_config` is the JSON config sent to the worker (source + dirs).
    Falls back to the in-process `loader`+`build_fn` if the worker fails.
    """

    def __init__(self, projects_dir: Path, ttl_seconds: int = 15,
                 extra_roots: Optional[list[Path]] = None, loader=None,
                 worker_config: Optional[dict] = None):
        self.projects_dir = projects_dir
        self.extra_roots = extra_roots or []
        self.ttl = ttl_seconds
        self.worker_config = worker_config
        self._loader = loader or (
            lambda: load_records(self.projects_dir, extra_roots=self.extra_roots)
        )
        self._lock = threading.Lock()
        self._build_lock = threading.Lock()   # single-flight: 1 rebuild at a time
        self._report = None
        self._report_key = None
        self._report_at = 0.0

    def _run_worker(self) -> Optional[dict]:
        """Spawn the build worker; return its report dict or None on failure."""
        import subprocess
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "burnmeter._worker"],
                input=json.dumps(self.worker_config or {}),
                capture_output=True, text=True, timeout=180,
            )
            if proc.returncode != 0:
                sys.stderr.write(f"[burnmeter] worker rc={proc.returncode}: {proc.stderr[:300]}\n")
                return None
            return json.loads(proc.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
            sys.stderr.write(f"[burnmeter] worker failed: {e}\n")
            return None

    def _do_build(self, build_fn, key):
        """Run the heavy build (worker subprocess, RAM-bounded; in-process on
        worker failure) and store it. Assumes self._build_lock is held."""
        report = None
        if self.worker_config is not None:
            report = self._run_worker()   # heavy work in a process that then dies
        if report is None:
            # Fallback: in-process (records discarded right after to limit RSS).
            records, stats, intents, errors = self._loader()
            report = build_fn(records, stats, time.time(), intents, errors)
            records = None
            import gc; gc.collect()
        with self._lock:
            self._report = report
            self._report_key = key
            self._report_at = time.time()
        return report

    def _bg_rebuild(self, build_fn, key):
        """Refresh the cache off the request path; release the build lock after."""
        try:
            self._do_build(build_fn, key)
        except Exception as e:                # never let a bg thread die silently
            sys.stderr.write(f"[burnmeter] bg rebuild failed: {e}\n")
        finally:
            self._build_lock.release()

    def get_report(self, build_fn, key, force: bool = False):
        """Return a memoized report. Built via worker subprocess (RAM-bounded)
        when worker_config is set; else in-process.

        SINGLE-FLIGHT + BACKGROUND stale-while-revalidate. The codex build is
        heavy (~30s over ~0.5M records); the dashboard's 10s auto-refresh would
        otherwise (a) stampede the cache on every TTL expiry → N concurrent
        ~800MB builds → OOM + total server hang, and (b) block the source-switch
        request for the whole build → frozen UI. So a stale-but-present report is
        returned INSTANTLY and refreshed in ONE background thread; only a cold
        cache (first load) or an explicit ?refresh=1 blocks on the build."""
        now = time.time()
        have = self._report is not None and self._report_key == key
        if have and not force and (now - self._report_at) <= self.ttl:
            return self._report                       # fresh

        if force:
            # Manual refresh: rebuild synchronously, serialized with bg builds.
            with self._build_lock:
                if (self._report is not None and self._report_key == key
                        and (time.time() - self._report_at) <= self.ttl):
                    return self._report               # a bg build just refreshed it
                return self._do_build(build_fn, key)

        if have:
            # Stale: serve instantly, refresh in the background (single-flight).
            if self._build_lock.acquire(blocking=False):
                try:
                    threading.Thread(target=self._bg_rebuild, args=(build_fn, key),
                                     daemon=True).start()
                except RuntimeError:                  # thread couldn't start
                    self._build_lock.release()
            return self._report

        # Cold cache (nothing usable yet): must build now, serialized.
        with self._build_lock:
            if self._report is not None and self._report_key == key:
                return self._report                   # another thread built it
            return self._do_build(build_fn, key)


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

    def build_for(source, plan, force=False):
        """Cached report for a source (worker-backed). Used by /api/report
        and /api/statusline so both share one RAM-bounded build per TTL."""
        sel = codex_cache if (source == "codex" and codex_cache) else cache
        def _build(records, stats, loaded_at, intents, error_events):
            rep = build_report(records, plan=plan, user_intents=intents,
                               error_events=error_events, source=source)
            rep["_meta"] = {**stats, "source": source}
            if source == "codex" and stats.get("rate_limits"):
                rep["codex_rate_limits"] = stats["rate_limits"]
            return rep
        return sel.get_report(_build, key=(source, plan), force=force)

    # Pro sync: 20s cache for pulled remote devices (avoid hammering the relay).
    sync_cache = {"data": None, "ts": 0.0}

    # Pro sync: auto-push this device's snapshot every 5 min, REUSING the cached
    # reports (no re-parse). No-op if sync isn't configured / cryptography absent.
    def _sync_autopush():
        from . import sync as _sync
        while True:
            time.sleep(300)
            try:
                cfg = _sync.load_config()
                if not _sync.is_configured(cfg):
                    continue
                reports = {}
                for s in ("claude", "codex"):
                    if s == "codex" and codex_cache is None:
                        continue
                    try:
                        reports[s] = build_for(s, None)
                    except Exception:
                        pass
                _sync.push_snapshot(cfg, _sync.snapshot_from_reports(cfg, reports))
            except Exception:
                pass
    threading.Thread(target=_sync_autopush, daemon=True).start()

    # Local pre-limit ALERTS: every ~60s evaluate the cached reports and push to
    # the user's OWN webhook/Slack/email on an upward threshold crossing. Runs
    # entirely locally (no phone-home) and reuses the cached reports (no re-parse).
    # No-op unless ~/.config/burnmeter/alerts.json is configured + enabled.
    def _alert_watch():
        from . import alerts as _alerts
        state: dict = {}
        while True:
            time.sleep(60)
            try:
                cfg = _alerts.load_config()
                if not _alerts.is_enabled(cfg):
                    continue
                reports = {}
                for s in (cfg.get("sources") or ["claude", "codex"]):
                    if s == "codex" and codex_cache is None:
                        continue
                    try:
                        reports[s] = build_for(s, None)
                    except Exception:
                        pass
                state = _alerts.check_and_fire(reports, cfg, state)
            except Exception:
                pass
    threading.Thread(target=_alert_watch, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        # Quieter than the default verbose logger.
        def log_message(self, format, *args):
            sys.stderr.write("[burnmeter] %s - %s\n" % (
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

            if path in ("/pricing", "/pricing.html", "/static/pricing.html"):
                _file_response(self, STATIC_DIR / "pricing.html", "text/html; charset=utf-8")
                return

            # Public landing/marketing sayfası — docs/index.html'i (web-deploy kaynağı) aynen serve eder.
            # Source checkout'ta çalışır; pip-install'da docs/ yoksa _file_response temiz 404 döner.
            if path in ("/landing", "/landing.html", "/home"):
                _file_response(self, LANDING_FILE, "text/html; charset=utf-8")
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
                # Single source of truth: burnmeter/statusline.py (shared with the
                # `burnmeter statusline` CLI subcommand, so HTTP pull + terminal never drift).
                from .statusline import build_statusline, statusline_text
                src = (qs.get("source", ["claude"])[0] or "claude").lower()
                report = build_for(src, None)   # shared worker-backed cached report
                if qs.get("format", [""])[0] == "json":
                    _json_response(self, 200, build_statusline(report))
                    return
                body = statusline_text(report).encode("utf-8")
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
                _, source = pick_cache(qs)
                report = build_for(source, plan, force=force)
                report.setdefault("_meta", {})
                report["_meta"]["version"] = __version__
                report["_meta"]["served_at"] = time.time()
                _json_response(self, 200, report)
                return

            if path == "/api/sync/devices":
                # Pro: remote-device snapshots, decrypted server-side (server holds the
                # passphrase) for the LOCAL 127.0.0.1 UI. The RELAY only ever sees
                # ciphertext — E2E intact. 20s cache so we don't hammer the relay.
                from . import sync as _sync
                cfg = _sync.load_config()
                if not _sync.is_configured(cfg):
                    _json_response(self, 200, {"configured": False, "devices": []})
                    return
                now = time.time()
                if sync_cache["data"] is not None and now - sync_cache["ts"] < 20:
                    _json_response(self, 200, sync_cache["data"])
                    return
                try:
                    data = {"configured": True, "this_device": cfg.get("device_id"),
                            "devices": _sync.pull(cfg)}
                except Exception as e:
                    data = {"configured": True, "error": str(e), "devices": []}
                sync_cache["data"] = data
                sync_cache["ts"] = now
                _json_response(self, 200, data)
                return

            if path == "/api/records.csv":
                # Export endpoint (rare) — load transiently, not held in server RAM.
                # source-aware: ?source=codex → Codex kayıtları (yoksa default cache).
                src_cache, _ = pick_cache(qs)
                records, _, _, _ = src_cache._loader()
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header(
                    "Content-Disposition",
                    "attachment; filename=burnmeter_records.csv",
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
          codex_extra_roots: Optional[list[Path]] = None,
          open_browser: bool = False) -> None:
    projects_dir = Path(projects_dir) if projects_dir else CLAUDE_PROJECTS_DIR
    # Claude now has a per-file cache (parser.py) → builds are fast (~5-8s warm,
    # only changed files re-read). A 30s floor keeps data fresh while staying
    # comfortably above the build time (no churn even if a build spikes under
    # memory pressure); bg stale-while-revalidate keeps it non-blocking.
    cache = _Cache(
        projects_dir, ttl_seconds=max(ttl_seconds, 30), extra_roots=extra_roots,
        worker_config={
            "source": "claude",
            "projects_dir": str(projects_dir),
            "extra_roots": [str(r) for r in (extra_roots or [])],
        },
    )

    # burnmeter — second source. Codex sessions are huge (16GB+); the build
    # worker isolates that memory in a short-lived process. Longer TTL too.
    codex_root = Path(codex_dir) if codex_dir else CODEX_SESSIONS_DIR
    codex_cache = _Cache(
        codex_root,
        ttl_seconds=max(ttl_seconds, 120),   # ~30s build → bg-refresh, az churn
        extra_roots=codex_extra_roots,
        loader=lambda: load_codex_records(codex_root, extra_roots=codex_extra_roots) + ([],),
        worker_config={
            "source": "codex",
            "codex_dir": str(codex_root),
            "codex_extra_roots": [str(r) for r in (codex_extra_roots or [])],
        },
    )

    if not projects_dir.exists():
        sys.stderr.write(
            f"[burnmeter] WARNING: {projects_dir} does not exist. "
            "The dashboard will load with empty data until Claude Code writes a session here.\n"
        )

    server = ThreadingHTTPServer((host, port), make_handler(cache, codex_cache))
    sys.stderr.write(f"[burnmeter] v{__version__} → http://{host}:{port}\n")
    sys.stderr.write(f"[burnmeter] reading (claude): {projects_dir}\n")
    sys.stderr.write(f"[burnmeter] reading (codex):  {codex_root} (exists={codex_root.exists()})\n")
    for er in (extra_roots or []):
        sys.stderr.write(f"[burnmeter] + claude extra: {er} (exists={er.exists()})\n")
    for er in (codex_extra_roots or []):
        sys.stderr.write(f"[burnmeter] + codex extra:  {er} (exists={er.exists()})\n")

    # Startup warm: pre-build both caches off the request path so the first
    # source-switch after a (re)boot is instant instead of blocking ~30s on the
    # cold codex build. Uses the real HTTP path → warms the exact (source, None)
    # keys the dashboard requests. Daemon thread; failures are non-fatal.
    def _warm():
        import urllib.request
        time.sleep(1.0)   # let the listener bind
        for path in ("/api/report", "/api/report?source=codex"):
            try:
                urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=200).read()
            except Exception:
                pass
    threading.Thread(target=_warm, daemon=True).start()

    # CLI UX: open the dashboard in the user's browser once the listener is up.
    if open_browser:
        view_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
        def _open_browser():
            import webbrowser
            time.sleep(1.2)
            try:
                webbrowser.open(f"http://{view_host}:{port}")
            except Exception:
                pass
        threading.Thread(target=_open_browser, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\n[burnmeter] shutting down\n")
        server.server_close()


def main(argv=None):
    p = argparse.ArgumentParser(prog="burnmeter-serve")
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
