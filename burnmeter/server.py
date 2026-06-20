"""Tiny stdlib HTTP server that serves the dashboard + JSON API.

Local-only by default (binds to 127.0.0.1). No third-party deps. The only
external resource is Chart.js, loaded by the browser from a CDN — if you
want fully air-gapped operation, vendor chart.umd.min.js into ./static.
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
import threading
import uuid
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

from . import __version__
from .parser import CLAUDE_PROJECTS_DIR, load_records
from .codex_parser import CODEX_SESSIONS_DIR, load_codex_records
from .analytics import build_report


if getattr(sys, "frozen", False):
    # PyInstaller bundle: data is added at <_MEIPASS>/burnmeter/static.
    STATIC_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "burnmeter" / "static"
else:
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
        """Spawn the build worker; return its report dict or None on failure.

        Config and result go through TEMP FILES (env vars), not stdin/stdout — a
        frozen --windowed (no-console) worker has no usable std streams and
        writing the report to stdout crashed with OSError [Errno 22]."""
        import subprocess
        import tempfile
        from ._proc import NO_WINDOW
        # Frozen (PyInstaller) exe has no `python -m`: re-invoke the exe with a
        # `_worker` arg, which the frozen entry routes to burnmeter._worker.main().
        if getattr(sys, "frozen", False):
            worker_argv = [sys.executable, "_worker"]
        else:
            worker_argv = [sys.executable, "-m", "burnmeter._worker"]
        in_fd, in_path = tempfile.mkstemp(suffix=".json", prefix="bm-wk-in-")
        os.close(in_fd)
        out_fd, out_path = tempfile.mkstemp(suffix=".json", prefix="bm-wk-out-")
        os.close(out_fd)
        try:
            Path(in_path).write_text(json.dumps(self.worker_config or {}), encoding="utf-8")
            env = dict(os.environ, BURNMETER_WORKER_IN=in_path,
                       BURNMETER_WORKER_OUT=out_path, PYTHONUTF8="1")
            proc = subprocess.run(
                worker_argv, capture_output=True, text=True, timeout=1200,
                creationflags=NO_WINDOW, env=env,   # NO_WINDOW: no cmd flash per rebuild
            )
            if proc.returncode != 0:
                sys.stderr.write(f"[burnmeter] worker rc={proc.returncode}: {(proc.stderr or '')[:300]}\n")
                return None
            return json.loads(Path(out_path).read_text(encoding="utf-8"))
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
            sys.stderr.write(f"[burnmeter] worker failed: {e}\n")
            return None
        finally:
            for p in (in_path, out_path):
                try:
                    os.unlink(p)
                except Exception:
                    pass

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


# Per-process identity, set by setup_server() and echoed by /api/health. The
# single-instance guard matches THIS token so it recognises the exact instance
# the pidfile describes (not merely "a" Burnmeter on that port).
_INSTANCE_TOKEN: Optional[str] = None

# Set by the active launcher to a callable that cleanly stops THIS instance:
# icon.stop in tray mode, server.shutdown in console mode. trigger_restart() calls
# it so a one-click update can exit gracefully and a relauncher rebinds the port.
_RESTART_HOOK = None


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

    # Pro sync: auto-push this device's snapshot every 60s, REUSING the cached
    # reports (no re-parse). 60s (was 300s) so another device sees this one's live-ish
    # burn within ~1 min instead of up to ~5 min. No-op if sync isn't configured.
    def _sync_autopush():
        from . import sync as _sync
        from . import firebase_sync as _fb
        while True:
            time.sleep(60)
            try:
                cfg = _fb.load_config()
                if not _fb.is_configured(cfg):
                    continue
                reports = {}
                for s in ("claude", "codex"):
                    if s == "codex" and codex_cache is None:
                        continue
                    try:
                        reports[s] = build_for(s, None)
                    except Exception:
                        pass
                # snapshot_from_reports (in sync.py) builds the metadata-only blob; firebase
                # encrypts (E2E) + upserts it to Firestore. Silent no-op if unverified (403).
                _fb.push_snapshot(cfg, _sync.snapshot_from_reports(cfg, reports))
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
        # Keep the terminal clean for end users. The dashboard polls /api/report
        # every 10s; and if another service on the machine expects this port, its
        # pollers (/health, /favicon.ico, foreign API paths) hammer us with 404s.
        # Logging all of that drowns the "Burnmeter is running: <link>" banner.
        # Log ONLY genuine server errors (>=500); 2xx and 4xx (incl. foreign 404s)
        # stay silent.
        def log_request(self, code="-", size="-"):
            try:
                c = int(code)
            except (TypeError, ValueError):
                c = 0
            if c >= 500:
                self.log_message('"%s" %s', self.requestline, str(code))

        def log_error(self, format, *args):
            # send_error() also logs (e.g. "code 404, message Not found"). Suppress
            # client errors (4xx) — foreign pollers / favicon shouldn't flood the
            # terminal — but keep genuine server errors (5xx) and uncoded errors.
            code = args[0] if args else None
            if isinstance(code, int) and code < 500:
                return
            self.log_message(format, *args)

        def log_message(self, format, *args):
            sys.stderr.write("[burnmeter] %s - %s\n" % (
                self.address_string(), format % args
            ))

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/update":
                # One-click self-update from the dashboard. CSRF guard: a random
                # web page can't set this custom header without a CORS preflight
                # (which we never grant), so only our own same-origin dashboard
                # reaches the pip install. Source is the fixed official repo.
                if self.headers.get("X-Burnmeter-Update") != "1":
                    _json_response(self, 403, {"ok": False, "message": "forbidden"})
                    return
                # Return INSTANTLY, then a DETACHED updater stops this server, runs
                # pip, and relaunches. We must NOT run pip inside this request: the
                # server runs FROM site-packages, and pip --force-reinstall replacing
                # those in-use files (Windows) hangs → the long request never returns
                # → the dashboard shows a false "couldn't reach". The dashboard polls
                # /api/health and reloads when the new version is up.
                host, port = self.server.server_address[0], self.server.server_address[1]
                _json_response(self, 200, {"ok": True, "message": "Updating — Burnmeter will restart…"})
                def _go():
                    time.sleep(0.6)        # let the response flush first
                    _spawn_update(host, port)
                threading.Thread(target=_go, daemon=True).start()
                return

            if path in ("/api/pro/connect", "/api/pro/disconnect", "/api/pro/resend", "/api/pro/push", "/api/pro/rename"):
                # Pro account actions from the dashboard, backed by Firebase Auth. The
                # password is used ONLY here on localhost — to sign in to Firebase and to
                # derive the E2E key — never stored, never sent anywhere except Firebase
                # over TLS. CSRF guard (custom header a cross-site form can't set) + Host
                # allowlist (defense-in-depth vs DNS-rebinding).
                if self.headers.get("X-Burnmeter-Pro") != "1":
                    _json_response(self, 403, {"ok": False, "error": "forbidden"})
                    return
                if self.headers.get("Host", "").rsplit(":", 1)[0] not in ("127.0.0.1", "localhost", "::1"):
                    _json_response(self, 403, {"ok": False, "error": "forbidden host"})
                    return
                from . import firebase_sync as _fb
                sync_cache["data"] = None      # bust the devices cache so the UI refreshes
                if path == "/api/pro/disconnect":
                    _fb.logout()
                    _json_response(self, 200, {"ok": True})
                    return
                if path == "/api/pro/resend":
                    _json_response(self, 200, _fb.resend_verification())
                    return
                if path == "/api/pro/push":
                    # Push THIS device's snapshot to Firestore now (no 5-min wait), reusing
                    # cached reports. Encrypted client-side; Firestore holds ciphertext only.
                    from . import sync as _sync
                    cfg2 = _fb.load_config()
                    if not _fb.is_configured(cfg2):
                        _json_response(self, 400, {"ok": False, "error": "not signed in"})
                        return
                    reports = {}
                    for s in ("claude", "codex"):
                        if s == "codex" and codex_cache is None:
                            continue
                        try:
                            reports[s] = build_for(s, None)
                        except Exception:
                            pass
                    res = _fb.push_snapshot(cfg2, _sync.snapshot_from_reports(cfg2, reports))
                    _json_response(self, 200 if res.get("ok") else 400, res)
                    return
                if path == "/api/pro/rename":
                    # Rename THIS device. Sets a user label (name_source='user' so the
                    # auto-IP migration never overwrites it) and re-pushes immediately so
                    # the new name shows on every machine. Cross-device rename isn't here:
                    # a remote device re-stamps its own label on its next push.
                    from . import sync as _sync
                    try:
                        length = int(self.headers.get("Content-Length", 0) or 0)
                        body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                    except Exception:
                        _json_response(self, 400, {"ok": False, "error": "bad request"})
                        return
                    label = (body.get("label") or "").strip()[:40]
                    if not label:
                        _json_response(self, 400, {"ok": False, "error": "name required"})
                        return
                    cfg3 = _fb.load_config()
                    if not _fb.is_configured(cfg3):
                        _json_response(self, 400, {"ok": False, "error": "not signed in"})
                        return
                    cfg3["label"] = label
                    cfg3["name_source"] = "user"
                    _fb.save_config(cfg3)
                    reports = {}
                    for s in ("claude", "codex"):
                        if s == "codex" and codex_cache is None:
                            continue
                        try:
                            reports[s] = build_for(s, None)
                        except Exception:
                            pass
                    res = _fb.push_snapshot(cfg3, _sync.snapshot_from_reports(cfg3, reports))
                    _json_response(self, 200 if res.get("ok") else 400, {"ok": bool(res.get("ok")), "label": label, "error": res.get("error")})
                    return
                try:
                    length = int(self.headers.get("Content-Length", 0) or 0)
                    data = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except Exception:
                    _json_response(self, 400, {"ok": False, "error": "bad request"})
                    return
                mode = (data.get("mode") or "login").strip()
                email = (data.get("email") or "").strip()
                password = data.get("password") or ""
                if not email or not password:
                    _json_response(self, 400, {"ok": False, "error": "email and password required"})
                    return
                try:
                    res = _fb.signup(email, password) if mode == "signup" else _fb.login(email, password)
                except Exception as e:
                    res = {"ok": False, "error": str(e)}
                _json_response(self, 200 if res.get("ok") else 400, res)
                return
            _json_response(self, 404, {"ok": False, "message": "not found"})

        def do_GET(self):
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            path = url.path

            if path in ("/", "/index.html"):
                # Serve dashboard.html with the cache-bust token (?v=__BMVER__) set to
                # the running version → the browser ALWAYS re-fetches css/js after an
                # update (no more stale assets from a forgotten manual ?v bump).
                try:
                    html = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8").replace("__BMVER__", __version__)
                    body = html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception:
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
                    "token": _INSTANCE_TOKEN,
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

            if path == "/api/pro/status":
                # Is this device signed in to Pro? Account metadata for the "Connect Pro"
                # panel — never the password or any decrypted usage.
                if self.headers.get("Host", "").rsplit(":", 1)[0] not in ("127.0.0.1", "localhost", "::1"):
                    _json_response(self, 403, {"error": "forbidden host"})
                    return
                from . import firebase_sync as _fb
                cfg = _fb.load_config()
                if not _fb.is_configured(cfg):
                    _json_response(self, 200, {"configured": False})
                    return
                out = {"configured": True, "email": cfg.get("email"),
                       "device_id": cfg.get("device_id"), "label": cfg.get("label")}
                try:
                    out["verified"] = _fb.is_verified(cfg)
                    out.update(_fb.account(cfg))   # plan, status
                except Exception as e:
                    out["error"] = str(e)
                _json_response(self, 200, out)
                return

            if path == "/api/sync/devices":
                # Pro: remote-device snapshots, decrypted HERE (this local server holds the
                # E2E key) for the 127.0.0.1 UI. Firestore only ever holds ciphertext — E2E
                # intact. 20s cache so we don't hammer Firebase.
                from . import firebase_sync as _fb
                cfg = _fb.load_config()
                if not _fb.is_configured(cfg):
                    _json_response(self, 200, {"configured": False, "devices": []})
                    return
                now = time.time()
                if sync_cache["data"] is not None and now - sync_cache["ts"] < 20:
                    _json_response(self, 200, sync_cache["data"])
                    return
                try:
                    data = {"configured": True, "this_device": cfg.get("device_id"),
                            "devices": _fb.pull(cfg)}
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


def _open_browser_async(url: str, delay: float = 1.2) -> None:
    """Open `url` in the default browser after a short delay (lets the listener
    come up first). Daemon thread; failures are non-fatal. Shared by serve(),
    run_tray(), and the single-instance guard."""
    def _open():
        import webbrowser
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_open, daemon=True).start()


def _spawn_relaunch(host: str, port: int, open_browser: bool = True) -> bool:
    """Spawn a DETACHED helper that waits for THIS instance to exit, then starts a
    fresh Burnmeter (running the freshly-installed code). Returns True on spawn.
    open_browser=False when the dashboard initiated it (that tab reloads itself,
    so we don't pop a duplicate)."""
    import subprocess
    from ._proc import NO_WINDOW
    from . import desktop
    argv = [desktop._pythonw(sys.executable), "-m", "burnmeter", "_relaunch",
            "--host", str(host), "--port", str(port)]
    if not open_browser:
        argv.append("--no-browser")
    try:
        kw = {"close_fds": True, "cwd": str(Path.home())}
        if sys.platform == "win32":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
            kw["creationflags"] = 0x00000008 | 0x00000200 | NO_WINDOW
        else:
            kw["start_new_session"] = True
        subprocess.Popen(argv, **kw)
        return True
    except Exception:
        return False


def _spawn_update(host: str, port: int) -> bool:
    """Spawn the DETACHED robust updater (`burnmeter _update`): it stops this server,
    reinstalls (package no longer in use), then relaunches. Survives this server
    exiting. Returns True on spawn."""
    import subprocess
    from ._proc import NO_WINDOW
    from . import desktop
    try:
        kw = {"close_fds": True, "cwd": str(Path.home())}
        if sys.platform == "win32":
            kw["creationflags"] = 0x00000008 | 0x00000200 | NO_WINDOW  # DETACHED|NEWGROUP|NO_WINDOW
        else:
            kw["start_new_session"] = True
        subprocess.Popen(
            [desktop._pythonw(sys.executable), "-m", "burnmeter", "_update",
             "--host", str(host), "--port", str(port)], **kw)
        return True
    except Exception:
        return False


def trigger_restart(host: str, port: int, open_browser: bool = True) -> None:
    """One-click-update restart: spawn the relauncher, then stop THIS instance so
    it exits and frees the port → the relauncher rebinds with the new code. Falls
    back to os._exit(0) if there's no clean restart hook (self-heal handles the
    leftover pidfile)."""
    if not _spawn_relaunch(host, port, open_browser=open_browser):
        return   # couldn't spawn the relauncher → do NOT kill ourselves (no dead server)
    hook = _RESTART_HOOK
    if hook is not None:
        try:
            hook()
            return
        except Exception:
            pass
    os._exit(0)


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness. POSIX os.kill(pid, 0) is cheap+reliable; on Windows
    tasklist is flaky, so we don't gate the startup decision on it there — the
    HTTP /api/health probe is the authoritative signal (a process that answers is
    alive by definition)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return True   # advisory only — the health probe decides on Windows
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _already_running(host: str, port: int, open_browser: bool = False,
                     strict_port: bool = False) -> bool:
    """True iff THIS machine's Burnmeter (per the pidfile) is already serving.

    Identity-bound, not "any Burnmeter on that port": we require the recorded
    port to answer /api/health with a token that MATCHES the pidfile's token —
    defending against a reused PID, a foreign app squatting the port, and a
    different Burnmeter instance. If the pidfile exists but the probe fails
    (dead / foreign / nobody), we DELETE the stale pidfile so the next launch is
    clean and fast, and return False (caller binds fresh).

    strict_port (the auto-update RELAUNCH path): the user's open tab is pinned to
    the EXACT requested port, so a pidfile that names a DIFFERENT port is
    irrelevant — return False and let the caller rebind the requested port. The
    old default ("any Burnmeter the pidfile knows about is 'already running'")
    silently aborted the relaunch when the pidfile pointed elsewhere (e.g. a
    prior restart that landed on 7655, or a leftover test instance) → the user's
    tab on the original port then had nothing to reach ("couldn't reach the
    local server")."""
    import urllib.request
    pf = Path.home() / ".burnmeter" / "server.json"
    if not pf.exists():
        return False
    try:
        info = json.loads(pf.read_text(encoding="utf-8"))
        rport = int(info["port"])
        rhost = info.get("host", host)
        rtoken = info.get("token")
    except Exception:
        return False

    # Relaunch: a pidfile for some OTHER port must not block rebinding the port
    # the user's tab is pinned to. (Don't unlink it — it may be a legitimately
    # live, separate instance on a different port.)
    if strict_port and rport != port:
        return False

    probe_host = "127.0.0.1" if rhost in ("0.0.0.0", "") else rhost
    url = f"http://{probe_host}:{rport}"
    alive, rver = False, None
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=1.0) as r:
            data = json.loads(r.read().decode("utf-8"))
        if isinstance(data, dict):
            rver = data.get("version")
            alive = bool(data.get("ok") and rtoken and data.get("token") == rtoken)
    except Exception:
        alive = False

    if not alive:
        try:
            pf.unlink()           # self-heal: stale/foreign pidfile
        except Exception:
            pass
        return False

    if rver and rver != __version__:
        sys.stdout.write(
            f"  Note: a different Burnmeter (v{rver}) is already running on "
            f"{url}. Run `burnmeter stop` first if you want v{__version__}.\n")
    sys.stdout.write(f"\n  ➜  Burnmeter is already running:  {url}\n")
    sys.stdout.write("     Opening it in your browser…\n\n")
    sys.stdout.flush()
    if open_browser:
        _open_browser_async(url)
    return True


class _ServerHandle:
    """A bound (not-yet-serving) Burnmeter server plus its cleanup, so the
    console path (block on serve_forever) and the tray path (serve_forever on a
    daemon thread) share identical teardown. close() is idempotent —
    server_close() + pidfile unlink. start_warm() is separate so the tray can
    kick the cache pre-build AFTER serve_forever is actually accepting."""
    def __init__(self, server, host, port, url, pidfile, warm_paths):
        self.server = server
        self.host = host
        self.port = port
        self.url = url
        self._pidfile = pidfile
        self._warm_paths = warm_paths
        self._closed = False

    def start_warm(self) -> None:
        def _warm():
            import urllib.request
            time.sleep(1.0)
            for p in self._warm_paths:
                try:
                    urllib.request.urlopen(
                        f"http://{self.host}:{self.port}{p}", timeout=620).read()
                except Exception:
                    pass
        threading.Thread(target=_warm, daemon=True).start()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.server.server_close()
        except Exception:
            pass
        if self._pidfile:
            try:
                self._pidfile.unlink()
            except Exception:
                pass


def setup_server(host: str = "127.0.0.1", port: int = 7654,
                 projects_dir: Optional[Path] = None,
                 ttl_seconds: int = 15,
                 extra_roots: Optional[list[Path]] = None,
                 codex_dir: Optional[Path] = None,
                 codex_extra_roots: Optional[list[Path]] = None,
                 codex_since_days: int = 90,
                 reuse_addr: bool = False,
                 strict_port: bool = False) -> "_ServerHandle":
    """Build caches, bind the port (with fallback), write the pidfile (incl. a
    per-process instance token), log the reading paths. Returns a bound
    _ServerHandle. Does NOT serve_forever, open the browser, or start the warm
    thread — the caller drives those. Raises RuntimeError if no port binds.

    strict_port (auto-update RELAUNCH): bind ONLY the requested port — never fall
    back to port+1/ephemeral. The user's tab is pinned to the original port, so
    "landing on 7655" would strand it. The just-exited instance can leave the
    port briefly unavailable (still closing, or TIME_WAIT), so retry the SAME
    port for a few seconds instead of moving."""
    global _INSTANCE_TOKEN
    projects_dir = Path(projects_dir) if projects_dir else CLAUDE_PROJECTS_DIR
    # Claude now has a per-file cache (parser.py) → builds are fast (~5-8s warm,
    # only changed files re-read). A 30s floor keeps data fresh while staying
    # comfortably above the build time; bg stale-while-revalidate keeps it
    # non-blocking.
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
        loader=lambda: load_codex_records(codex_root, extra_roots=codex_extra_roots, since_days=codex_since_days) + ([],),
        worker_config={
            "source": "codex",
            "codex_dir": str(codex_root),
            "codex_extra_roots": [str(r) for r in (codex_extra_roots or [])],
            "codex_since_days": codex_since_days,
        },
    )

    if not projects_dir.exists():
        sys.stderr.write(
            f"[burnmeter] WARNING: {projects_dir} does not exist. "
            "The dashboard will load with empty data until Claude Code writes a session here.\n"
        )

    # Bind the requested port, falling back to the next free one — then an
    # ephemeral port — if it's already taken, so a busy 7654 never crashes the
    # user's first run. make_handler() is called once (it starts daemon threads);
    # only the bind is retried.
    class _Server(ThreadingHTTPServer):
        # On Windows SO_REUSEADDR lets a second bind silently SUCCEED on a port
        # another server already holds (port hijack) — disable reuse there so a
        # busy port raises and we fall back; keep it on POSIX (restart-friendly).
        # EXCEPT on a relaunch (reuse_addr=True): the previous instance just exited
        # and its port may be in TIME_WAIT, so we MUST allow reuse to rebind the
        # SAME port (otherwise the auto-update restart lands on 7655 and the user's
        # tab can't reach it).
        allow_reuse_address = reuse_addr or not sys.platform.startswith("win")
        # Daemonise per-request worker threads so an in-flight request — e.g. a
        # 20-minute cold codex build — never blocks Ctrl+C / tray Quit teardown.
        daemon_threads = True

    handler = make_handler(cache, codex_cache)
    server = None
    requested = port
    # Relaunch pins the exact port (no port-hopping); a fresh launch may hop to a
    # free neighbour so a busy 7654 never crashes the first run.
    candidates = [port] if strict_port else [port, *range(port + 1, port + 11), 0]
    attempts = 16 if strict_port else 1          # ~8s of 0.5s retries for TIME_WAIT
    for attempt in range(attempts):
        for cand in candidates:
            try:
                server = _Server((host, cand), handler)
                break
            except OSError:
                continue
        if server is not None:
            break
        if strict_port:                          # same port still settling — wait, retry
            time.sleep(0.5)
    if server is None:
        where = f"port {requested}" if strict_port else f"any free port near {requested}"
        raise RuntimeError(f"could not bind {where}")
    port = server.server_address[1]
    if port != requested:
        sys.stderr.write(f"[burnmeter] port {requested} was busy → using {port} instead\n")

    # Per-process token → /api/health echoes it → the single-instance guard can
    # recognise THIS exact instance (not merely "a" Burnmeter on the port).
    _INSTANCE_TOKEN = uuid.uuid4().hex
    _pidfile = Path.home() / ".burnmeter" / "server.json"
    try:
        _pidfile.parent.mkdir(parents=True, exist_ok=True)
        _pidfile.write_text(
            json.dumps({"pid": os.getpid(), "port": port, "host": host,
                        "token": _INSTANCE_TOKEN, "version": __version__}),
            encoding="utf-8")
    except Exception:
        _pidfile = None

    sys.stderr.write(f"[burnmeter] v{__version__} → http://{host}:{port}\n")
    sys.stderr.write(f"[burnmeter] reading (claude): {projects_dir}\n")
    sys.stderr.write(f"[burnmeter] reading (codex):  {codex_root} (exists={codex_root.exists()})\n")
    for er in (extra_roots or []):
        sys.stderr.write(f"[burnmeter] + claude extra: {er} (exists={er.exists()})\n")
    for er in (codex_extra_roots or []):
        sys.stderr.write(f"[burnmeter] + codex extra:  {er} (exists={er.exists()})\n")

    view_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    url = f"http://{view_host}:{port}"
    handle = _ServerHandle(server, host, port, url, _pidfile,
                           ("/api/report", "/api/report?source=codex"))
    # Normal interpreter exit also cleans up (does NOT run on SIGKILL/taskkill —
    # the guard's self-heal covers those).
    atexit.register(handle.close)
    return handle


def serve(host: str = "127.0.0.1", port: int = 7654,
          projects_dir: Optional[Path] = None,
          ttl_seconds: int = 15,
          extra_roots: Optional[list[Path]] = None,
          codex_dir: Optional[Path] = None,
          codex_extra_roots: Optional[list[Path]] = None,
          codex_since_days: int = 90,
          open_browser: bool = False, reuse_addr: bool = False) -> None:
    """Blocking console server (terminal / CI / power users). Always binds its
    OWN instance — no single-instance guard here, preserving the documented
    two-terminals-two-ports workflow. The tray path (run_tray) is the one that
    de-dupes via _already_running()."""
    try:
        h = setup_server(host, port, projects_dir, ttl_seconds, extra_roots,
                         codex_dir, codex_extra_roots, codex_since_days,
                         reuse_addr=reuse_addr, strict_port=reuse_addr)
    except RuntimeError as e:
        sys.stderr.write(f"[burnmeter] ERROR: {e}\n")
        return

    h.start_warm()
    # Console restart hook: shutdown() unblocks serve_forever → serve() returns and
    # the process exits (so a one-click update's relauncher can rebind the port).
    global _RESTART_HOOK
    _RESTART_HOOK = h.server.shutdown
    if open_browser:
        _open_browser_async(h.url)

    # A clear, clickable link in the terminal — most terminals linkify http://… .
    # Printed to stdout (not the [burnmeter] stderr log) so it stands out, and it
    # always shows the REAL bound port even when 7654 was busy and we fell back.
    opening = "  (opening in your browser…)" if open_browser else ""
    sys.stdout.write(f"\n  ➜  Burnmeter is running:  {h.url}{opening}\n")
    sys.stdout.write("     Leave this window open. Press Ctrl+C to stop (or run: burnmeter stop).\n")
    sys.stdout.write("     Tip: `burnmeter tray` runs it in the background — keeps going after you close this window.\n\n")
    sys.stdout.flush()

    try:
        h.server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\n[burnmeter] shutting down\n")
    finally:
        h.close()


def main(argv=None):
    p = argparse.ArgumentParser(prog="burnmeter-serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7654)
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
