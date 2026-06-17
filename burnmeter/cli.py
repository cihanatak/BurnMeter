"""Command line interface for terminal-only users.

Subcommands:
    status   one-screen summary (today + active 5h window)
    daily    daily breakdown table
    models   per-model totals
    sessions recent sessions
    serve    start the local web dashboard
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .parser import CLAUDE_PROJECTS_DIR, load_records
from .analytics import (
    build_report,
    aggregate_by_day,
    aggregate_by_model,
    aggregate_by_session,
    aggregate_total,
    detect_billing_windows,
    current_window_status,
    infer_plan,
    PLAN_LIMITS,
    INDUSTRY_REFERENCE,
)


# ANSI helpers — degrade gracefully if not a tty.
def _supports_color() -> bool:
    return sys.stdout.isatty() and sys.platform != "win32"


def _c(code: str, s: str) -> str:
    if not _supports_color():
        return s
    return f"\033[{code}m{s}\033[0m"


def bold(s): return _c("1", s)
def dim(s): return _c("2", s)
def green(s): return _c("32", s)
def yellow(s): return _c("33", s)
def red(s): return _c("31", s)
def cyan(s): return _c("36", s)


def _fmt_int(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def _fmt_money(n) -> str:
    try:
        return f"${float(n):,.2f}"
    except (TypeError, ValueError):
        return str(n)


def _fmt_pct(n) -> str:
    try:
        return f"{float(n) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(n)


def _bar(value: float, total: float, width: int = 30) -> str:
    if total <= 0:
        return "[" + " " * width + "]"
    pct = max(0.0, min(1.0, value / total))
    fill = int(round(pct * width))
    bar = "█" * fill + "░" * (width - fill)
    color = green
    if pct >= 0.95:
        color = red
    elif pct >= 0.75:
        color = yellow
    return "[" + color(bar) + "]"


def _print_header(title: str):
    print()
    print(bold(cyan(f"━━━ {title} ━━━")))


def cmd_status(args):
    records, stats, _, _ = load_records(Path(args.projects_dir))
    if not records:
        print(red("No usage records found."))
        print(dim(f"Looked in: {stats['root']} (exists={stats['root_exists']})"))
        print(dim(f"  files scanned: {stats['files_scanned']}"))
        print(dim(f"  parse errors:  {stats['parse_errors']}"))
        return 1

    report = build_report(records, plan=args.plan)

    today_iso = datetime.now(timezone.utc).date().isoformat()
    today = next((d for d in report["daily"] if d["date"] == today_iso), None)
    totals = report["totals"]
    baseline = report["baseline"]
    cw = report["current_window"]
    plan_inf = report["plan_inference"]

    _print_header("burnmeter status")
    print(f"  data: {_fmt_int(report['record_count'])} usage records "
          f"across {_fmt_int(stats['files_scanned'])} files")

    _print_header("today")
    if today:
        print(f"  tokens: {bold(_fmt_int(today['total_tokens']))}   "
              f"cost: {bold(_fmt_money(today['cost_usd']))}   "
              f"cache hit: {bold(_fmt_pct(today['cache_hit_rate']))}")
        if baseline.get("samples", 0) >= 3:
            p50 = baseline["cost_p50"]
            p90 = baseline["cost_p90"]
            ratio = today["cost_usd"] / p50 if p50 else 0
            verdict = green("normal")
            if today["cost_usd"] > p90:
                verdict = red(f"high (>P90 of {_fmt_money(p90)})")
            elif ratio >= 1.5:
                verdict = yellow(f"elevated ({ratio:.1f}x your median)")
            elif ratio <= 0.5:
                verdict = dim("light")
            print(f"  vs baseline (P50={_fmt_money(p50)}, P90={_fmt_money(p90)}): {verdict}")
        ref = INDUSTRY_REFERENCE
        print(dim(
            f"  reference: avg ent. dev = {_fmt_money(ref['avg_cost_per_active_day_usd'])}/active day, "
            f"P90 = {_fmt_money(ref['active_day_p90_cost_usd'])}/active day"
        ))
    else:
        print(dim("  no activity today"))

    _print_header("active 5-hour window")
    if cw.get("active"):
        print(f"  plan: {bold(cw['plan'].upper())}  "
              f"(inferred from P90 over last {plan_inf['samples']} windows: "
              f"{_fmt_int(plan_inf['p90_window_tokens'])} tokens)")
        used = cw["tokens_used"]
        limit = cw["plan_limit"]
        print(f"  usage: {_fmt_int(used)} / {_fmt_int(limit)}  "
              f"({_fmt_pct(cw['utilization'])})")
        print(f"         {_bar(used, limit)}")
        mins_left = cw["remaining_seconds"] // 60
        print(f"  burn rate: {_fmt_int(int(cw['burn_tokens_per_min']))} tokens/min   "
              f"time left: {mins_left} min")
        if cw.get("projected_overage", 0) > 0:
            print(red(f"  ⚠ projected to exceed limit by "
                      f"{_fmt_int(cw['projected_overage'])} tokens at window close"))
        else:
            print(green(f"  ✓ projected close: "
                        f"{_fmt_int(cw['projected_close_tokens'])} tokens"))
    else:
        print(dim("  no active window"))

    _print_header("lifetime")
    print(f"  {_fmt_int(totals['total_tokens'])} tokens, "
          f"{_fmt_money(totals['cost_usd'])} (estimated)")
    print(f"  cache hit rate: {bold(_fmt_pct(totals['cache_hit_rate']))}  ", end="")
    chr_ = totals["cache_hit_rate"]
    if chr_ >= 0.7:
        print(green("(excellent — keep doing this)"))
    elif chr_ >= 0.4:
        print(yellow("(ok — there's headroom to save more)"))
    else:
        print(red("(low — most input is uncached; expect higher bills)"))

    if report["anomalies"]:
        _print_header("recent anomalies (last 14 days)")
        for a in report["anomalies"][-5:]:
            print(yellow(
                f"  {a['date']}: {_fmt_int(a['tokens'])} tokens "
                f"({_fmt_money(a['cost_usd'])}) — {', '.join(a['flags'])}"
            ))
    return 0


def cmd_daily(args):
    records, _, _, _ = load_records(Path(args.projects_dir))
    daily = aggregate_by_day(records)
    if not daily:
        print("(no data)")
        return 1
    print(f"{'date':<12}{'tokens':>14}{'cost':>10}{'cache hit':>12}{'msgs':>8}")
    print("-" * 56)
    for d in daily[-args.limit:]:
        print(
            f"{d['date']:<12}"
            f"{_fmt_int(d['total_tokens']):>14}"
            f"{_fmt_money(d['cost_usd']):>10}"
            f"{_fmt_pct(d['cache_hit_rate']):>12}"
            f"{_fmt_int(d['messages']):>8}"
        )
    return 0


def cmd_models(args):
    records, _, _, _ = load_records(Path(args.projects_dir))
    rows = aggregate_by_model(records)
    if not rows:
        print("(no data)")
        return 1
    print(f"{'model':<10}{'msgs':>8}{'tokens':>14}{'cost':>10}{'cache hit':>12}")
    print("-" * 54)
    for r in rows:
        print(
            f"{r['model_family']:<10}"
            f"{_fmt_int(r['messages']):>8}"
            f"{_fmt_int(r['total_tokens']):>14}"
            f"{_fmt_money(r['cost_usd']):>10}"
            f"{_fmt_pct(r['cache_hit_rate']):>12}"
        )
    return 0


def cmd_sessions(args):
    records, _, _, _ = load_records(Path(args.projects_dir))
    rows = aggregate_by_session(records)[: args.limit]
    if not rows:
        print("(no data)")
        return 1
    for s in rows:
        models = ",".join({m.split("-")[1] if "-" in m else m for m in s["models"]}) or "?"
        print(
            f"{s['started_at'][:19]}  "
            f"{s['project_label'][:20]:<20}  "
            f"{models:<8}  "
            f"{_fmt_int(s['total_tokens']):>10} tok  "
            f"{_fmt_money(s['cost_usd']):>8}  "
            f"{s['session_id'][:8]}"
        )
    return 0


def _common_kwargs(args) -> dict:
    """The server/tray kwargs shared by `serve` and `tray` (identical options)."""
    return dict(
        host=args.host, port=args.port,
        projects_dir=Path(args.projects_dir), ttl_seconds=args.ttl,
        extra_roots=[Path(p).expanduser() for p in (args.extra_projects_dir or [])],
        codex_dir=(Path(args.codex_dir).expanduser()
                   if getattr(args, "codex_dir", None) else None),
        codex_extra_roots=[Path(p).expanduser()
                           for p in (getattr(args, "codex_extra_dir", []) or [])],
        codex_since_days=getattr(args, "codex_days", 90),
    )


def _ensure_shortcut_once(args) -> None:
    """Drop/upgrade the 'Burnmeter' desktop shortcut (double-click to relaunch).
    Idempotent; --no-shortcut opts out. Never raises — best-effort UX."""
    if getattr(args, "no_shortcut", False):
        return
    try:
        from . import desktop
        path, changed = desktop.ensure_shortcut(port=args.port)
        if changed and path:
            print(green(f"✓ '{path.name}' shortcut ready on your Desktop — double-click it any time."))
    except Exception:
        pass


def _is_windowless() -> bool:
    """True when launched via pythonw.exe (no console): main() has routed
    stdout to os.devnull, so printed hints would be invisible."""
    return getattr(sys.stdout, "name", "") == os.devnull


def _windowless_notice(msg: str) -> None:
    """Pop a native dialog so a windowless launch can still surface a message
    (a console hint would go to devnull). Windows only; no-op elsewhere."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "Burnmeter", 0x40)  # MB_ICONINFORMATION
        except Exception:
            pass


def cmd_serve(args):
    # `burnmeter serve --tray` ≡ `burnmeter tray` — same code path.
    if getattr(args, "tray", False):
        return cmd_tray(args)
    from .server import serve
    _ensure_shortcut_once(args)
    # serve() prints the clear, clickable link with the REAL bound port (it may
    # fall back from 7654 if that port is busy), so we only show a starting note.
    print("Starting Burnmeter…")
    serve(open_browser=not getattr(args, "no_browser", False), **_common_kwargs(args))
    return 0


def cmd_app(args):
    """Open Burnmeter in a native desktop window (pywebview). Attaches to a
    running instance if present; otherwise spawns a DETACHED background tray
    server and attaches the window to it — so closing the window leaves the
    dashboard running (quit from the tray icon). Falls back to the browser if
    pywebview isn't installed — never a dead double-click."""
    _ensure_shortcut_once(args)
    from .window import run_window
    print("Opening Burnmeter…")
    return run_window(open_browser=False, ensure_background=True,
                      **_common_kwargs(args))


def _tray_passthrough_args(args) -> list:
    """Rebuild the CLI flags so a detached re-spawn keeps the same options."""
    out = ["--host", str(args.host), "--port", str(args.port), "--ttl", str(args.ttl)]
    if getattr(args, "no_browser", False):
        out.append("--no-browser")
    if getattr(args, "no_shortcut", False):
        out.append("--no-shortcut")
    for p in (getattr(args, "extra_projects_dir", []) or []):
        out += ["--extra-projects-dir", str(p)]
    if getattr(args, "codex_dir", None):
        out += ["--codex-dir", str(args.codex_dir)]
    for p in (getattr(args, "codex_extra_dir", []) or []):
        out += ["--codex-extra-dir", str(p)]
    out += ["--codex-days", str(getattr(args, "codex_days", 90))]
    return out


def _maybe_detach_tray(args) -> bool:
    """Windows: when `burnmeter tray` is launched FROM a console, re-spawn it as a
    windowless, fully detached process and return True (caller exits) — so the
    dashboard SURVIVES closing that terminal. Returns False when already detached,
    when there's no console (the desktop icon already runs pythonw/windowless), or
    on non-Windows / any failure (then the tray just runs in-process)."""
    if sys.platform != "win32":
        return False
    if os.environ.get("BURNMETER_TRAY_DETACHED") == "1" or _is_windowless():
        return False
    try:
        from . import desktop
        pyw = desktop._pythonw(sys.executable)
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
        subprocess.Popen(
            [pyw, "-m", "burnmeter", "tray", *_tray_passthrough_args(args)],
            env=dict(os.environ, BURNMETER_TRAY_DETACHED="1", PYTHONUTF8="1"),
            creationflags=flags, close_fds=True, cwd=str(Path.home()))
    except Exception:
        return False
    print(green("✓ Burnmeter is starting in the system tray."))
    print(dim("  You can close this window — the dashboard keeps running."))
    print(dim("  Stop it from the tray icon (right-click → Quit) or run: burnmeter stop"))
    return True


def cmd_tray(args):
    """Run Burnmeter in the system tray (recommended desktop launch). Falls back
    to console mode — never a silent dead double-click — if the optional tray
    dependency is missing or the tray can't run here."""
    # Launched from a terminal? Detach so it survives that window closing.
    if _maybe_detach_tray(args):
        return 0
    _ensure_shortcut_once(args)
    kwargs = _common_kwargs(args)
    open_browser = not getattr(args, "no_browser", False)
    from . import tray as traymod
    try:
        return traymod.run_tray(open_browser=open_browser, **kwargs)
    except (ImportError, traymod.TrayUnavailable):
        from .server import serve
        if _is_windowless():
            _windowless_notice(
                "Burnmeter's tray icon needs an extra package:\n\n"
                "    pip install burnmeter[tray]\n\n"
                "Opening the dashboard in your browser instead.")
        else:
            print(yellow("Tray support needs an extra package — install with:"))
            print("    pip install burnmeter[tray]")
            print(dim("Falling back to console mode (Ctrl+C to stop)…"))
        # ALWAYS open the browser in the fallback — on the windowless path it's
        # the only proof-of-life that the launch did something.
        serve(open_browser=True, **kwargs)
        return 0


def cmd_relaunch(args):
    """Internal: wait for the previous Burnmeter on this port to exit, then start a
    fresh tray running the freshly-installed code. Spawned by the one-click update's
    auto-restart. Falls back to console serve so Burnmeter never just vanishes."""
    import time, urllib.request
    url = f"http://{args.host}:{args.port}/api/health"
    for _ in range(60):                       # up to ~30s for the old one to go down
        try:
            urllib.request.urlopen(url, timeout=1).read()
            time.sleep(0.5)                   # still up → keep waiting
        except Exception:
            break                             # down → proceed
    time.sleep(1.5)                           # let the socket fully release
    ob = not getattr(args, "no_browser", False)
    # reuse_addr=True: the previous instance just exited; its port may be in
    # TIME_WAIT on Windows, so we must allow reuse to rebind the SAME port (else the
    # restart lands on 7655 and the user's tab can't reach it).
    common = dict(host=args.host, port=args.port, reuse_addr=True)
    try:
        from . import tray as traymod
        return traymod.run_tray(open_browser=ob, **common)
    except Exception:
        from .server import serve
        serve(open_browser=ob, **common)
        return 0


def cmd_update(args):
    """Internal: the ROBUST one-click updater. Runs detached so the server isn't
    executing pip on its OWN package files (on Windows that file-in-use conflict made
    `pip --force-reinstall` hang → the /api/update request never returned → the
    dashboard showed a false 'couldn't reach'). Sequence: STOP the running server →
    wait for it down → reinstall (package now free) → spawn `_relaunch` (new code,
    same port)."""
    import time, signal, urllib.request
    from ._proc import NO_WINDOW
    host, port = args.host, args.port
    hurl = f"http://{host}:{port}/api/health"
    # 1. stop the server ON THIS PORT so it releases the package files. Target by
    # PORT (not the single shared pidfile) so we can never kill a DIFFERENT instance.
    def _pids_on_port(p):
        pids = set()
        try:
            if sys.platform == "win32":
                out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                                     creationflags=NO_WINDOW).stdout
                for ln in out.splitlines():
                    if f":{p} " in ln and "LISTENING" in ln.upper():
                        t = ln.split()
                        if t and t[-1].isdigit():
                            pids.add(int(t[-1]))
            else:
                out = subprocess.run(["lsof", "-ti", f"tcp:{p}", "-sTCP:LISTEN"],
                                     capture_output=True, text=True).stdout
                pids = {int(x) for x in out.split() if x.strip().isdigit()}
        except Exception:
            pass
        return pids
    my_pid = os.getpid()
    for pid in _pids_on_port(port):
        if pid == my_pid:
            continue                        # never kill ourselves
        try:
            if sys.platform == "win32":
                # NO /T (tree kill): THIS _update process is a CHILD of the server we
                # are killing (the server's /api/update handler spawned us). /T would
                # reap the whole tree — including this process — so we'd die before
                # pip/relaunch and the server would never come back (the "stuck on
                # restarting…" bug). Kill ONLY the listener; the port frees on exit.
                subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                               capture_output=True, creationflags=NO_WINDOW)
            else:
                os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
    # 2. wait until it's actually down
    for _ in range(40):
        try:
            urllib.request.urlopen(hurl, timeout=1).read(); time.sleep(0.5)
        except Exception:
            break
    time.sleep(1.0)
    # 3. reinstall — now nothing is using the package, so pip can replace it cleanly.
    #    (BURNMETER_UPDATE_SKIP_PIP=1 skips this — used to test the restart path.)
    if os.environ.get("BURNMETER_UPDATE_SKIP_PIP") != "1":
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "--force-reinstall",
                            "--no-cache-dir", "git+https://github.com/cihanatak/BurnMeter"],
                           capture_output=True, text=True, timeout=600, creationflags=NO_WINDOW)
        except Exception:
            pass
    # 4. relaunch the freshly-installed code on the SAME port (new process → new code)
    try:
        from . import desktop
        flags = (0x00000008 | 0x00000200 | NO_WINDOW) if sys.platform == "win32" else 0
        subprocess.Popen([desktop._pythonw(sys.executable), "-m", "burnmeter", "_relaunch",
                          "--host", str(host), "--port", str(port)],
                         creationflags=flags, close_fds=True, cwd=str(Path.home()),
                         start_new_session=(sys.platform != "win32"))
    except Exception:
        pass
    return 0


def cmd_stop(args):
    """Stop a backgrounded Burnmeter server.

    The dashboard shortcut launches via pythonw.exe (no console), so there is no
    window to Ctrl+C. `serve` writes ~/.burnmeter/server.json {pid, port}; this
    reads it and terminates that process, then cleans the pidfile up."""
    pf = Path.home() / ".burnmeter" / "server.json"
    if not pf.exists():
        print("No running Burnmeter server found (no pidfile).")
        return 1
    try:
        info = json.loads(pf.read_text(encoding="utf-8"))
        pid = int(info["pid"])
    except Exception:
        print("Pidfile is unreadable — nothing to stop. Removing it.")
        try:
            pf.unlink()
        except Exception:
            pass
        return 1
    killed = False
    try:
        if sys.platform == "win32":
            from ._proc import NO_WINDOW
            r = subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                               capture_output=True, text=True,
                               creationflags=NO_WINDOW)
            killed = r.returncode == 0
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
            killed = True
    except Exception as e:
        print(f"Could not stop pid {pid}: {e}")
    try:
        pf.unlink()
    except Exception:
        pass
    if killed:
        print(green(f"✓ Stopped Burnmeter (pid {pid}, port {info.get('port', '?')})."))
        return 0
    print(f"Burnmeter (pid {pid}) was not running — cleaned up the stale pidfile.")
    return 0


def cmd_desktop(args):
    """Create a 'Burnmeter' desktop shortcut (double-click → dashboard opens)."""
    from . import desktop
    try:
        path = desktop.create_shortcut(port=args.port)
    except Exception as e:
        print(red(f"Couldn't create the shortcut: {e}")); return 1
    print(green(f"✓ Desktop shortcut ready: {path}"))
    print(dim("  Double-click it → Burnmeter opens in the system tray (no console window)."))
    return 0


def cmd_statusline(args):
    """One short line for a prompt / Claude Code statusLine.command.

    Computes standalone (no running server needed) so it works even before
    `burnmeter serve` is up. Shares burnmeter/statusline.py with the HTTP endpoint,
    so the terminal line and the web pull are identical.
    """
    from .statusline import build_statusline, statusline_text
    src = (getattr(args, "source", "claude") or "claude").lower()
    if src == "codex":
        from .codex_parser import CODEX_SESSIONS_DIR, load_codex_records
        root = (Path(args.codex_dir).expanduser()
                if getattr(args, "codex_dir", None) else CODEX_SESSIONS_DIR)
        records, _stats, intents = load_codex_records(root)
        errors: list = []
    else:
        records, _stats, intents, errors = load_records(Path(args.projects_dir))

    # A statusLine command must ALWAYS print exactly one short line and exit 0,
    # otherwise Claude Code surfaces it as an error in the prompt.
    if not records:
        print(f"○ burnmeter: {src} verisi yok")
        return 0

    report = build_report(records, plan=args.plan, user_intents=intents,
                          error_events=errors, source=src)
    if getattr(args, "json", False):
        import json
        print(json.dumps(build_statusline(report), ensure_ascii=False))
    else:
        print(statusline_text(report))
    return 0


def cmd_sync_relay(args):
    """Pro: self-host edilebilir zero-knowledge sync relay sunucusu."""
    from .sync_relay import run_relay
    storage = Path(args.storage).expanduser() if args.storage else None
    run_relay(host=args.host, port=args.port, storage=storage)
    return 0


def cmd_relay_account(args):
    """Pro: relay müşteri hesaplarını yönet (create/list/revoke). /admin ekranının
    okuduğu kimlik katmanı — relay'in accounts.json'ı üzerinde doğrudan çalışır
    (relay storage'ının olduğu makinede çalıştır). İleride ödeme webhook'u da aynı
    create_account()'u çağıracak."""
    from . import sync_relay as R
    storage = Path(args.storage).expanduser() if args.storage else (Path.home() / ".burnmeter-relay")
    act = args.action

    if act == "create":
        if not args.email:
            print(red("--email gerekli")); return 1
        rec = R.create_account(storage, email=args.email, plan=args.plan,
                               device_limit=args.device_limit, name=args.name or "",
                               source="cli")
        print(green(f"✓ hesap oluşturuldu · {rec['email']} · plan {bold(rec['plan'])} · "
                    f"cihaz limiti {rec['device_limit']}"))
        print(bold(f"  aktivasyon kodu: {rec['activation_code']}"))
        print(dim("  müşteriye email'i + bu kodu ver. İlk cihazda:"))
        print(dim(f"    burnmeter sync activate --relay <url> --email {rec['email']} --code {rec['activation_code']}"))
        print(dim("  (şifresini seçer; sonraki cihazlarda sadece: sync login --email ... )"))
        return 0

    if act == "list":
        accs = R.list_accounts(storage)
        if not accs:
            print(dim(f"hesap yok ({R._accounts_path(storage)})")); return 0
        _print_header(f"hesaplar ({len(accs)})")
        for a in accs:
            act_lbl = green("aktif") if a.get("activated") else dim("aktive değil")
            print(f"  {bold(a.get('email') or '?')} · {a.get('plan')} · {a.get('status')} · "
                  f"{act_lbl} · cihaz {a.get('device_count')}/{a.get('device_limit')} · id {dim(a.get('id'))}")
        return 0

    if act == "revoke":
        ref = args.id or args.token
        if not ref:
            print(red("--id veya --token gerekli")); return 1
        ok = R.set_account_status(storage, ref, "canceled")
        print(green("✓ iptal edildi") if ok else red("hesap bulunamadı"))
        return 0 if ok else 1
    return 0


def cmd_sync(args):
    """Pro: cihazlar arası E2E-şifreli sync. İlk cihaz: `activate` (email + kod + şifre);
    sonraki cihazlar: `login` (email + şifre). Sonra push/pull/status."""
    from . import sync as syncmod
    act = args.action
    cfg = syncmod.load_config()

    if act in ("activate", "login"):
        relay = args.relay or cfg.get("relay_url")
        if not relay:
            print(red("--relay <url> gerekli")); return 1
        if not args.email:
            print(red("--email gerekli")); return 1
        if act == "activate" and not args.code:
            print(red("--code gerekli (ilk cihaz için tek-kullanımlık aktivasyon kodu)")); return 1
        import getpass
        pw = args.password or getpass.getpass("Şifre (tüm cihazlarında aynı olacak): ")
        if not pw:
            print(red("şifre gerekli")); return 1
        res = syncmod.connect(relay, args.email, pw,
                              code=(args.code if act == "activate" else None),
                              label=args.label)
        if not res.get("ok"):
            print(red(f"{act} hata: {res.get('error')}")); return 1
        print(green(f"✓ bağlandı · {args.email} · plan {bold(res.get('plan','?'))} · "
                    f"cihaz {res.get('device_id')}"))
        print(dim("  E2E: şifren makineni terk etmez; relay yalnızca ciphertext görür."))
        return 0

    if not syncmod.is_configured(cfg):
        print(red("önce bağlan: burnmeter sync activate --relay <url> --email <e> --code <kod>  "
                  "(ya da login)")); return 1

    if act == "push":
        from .codex_parser import CODEX_SESSIONS_DIR
        codex = Path(args.codex_dir).expanduser() if args.codex_dir else CODEX_SESSIONS_DIR
        try:
            res = syncmod.push(cfg, Path(args.projects_dir), codex)
        except Exception as e:
            print(red(f"push hata: {e}")); return 1
        print(green(f"✓ push OK · cihaz {res['device_id']} · kaynaklar: {', '.join(res['sources']) or '(veri yok)'}"))
        return 0

    if act == "pull":
        try:
            devs = syncmod.pull(cfg)
        except Exception as e:
            print(red(f"pull hata: {e}")); return 1
        if not devs:
            print(dim("henüz senkron cihaz yok")); return 0
        _print_header(f"bağlı cihazlar ({len(devs)})")
        for d in devs:
            if d.get("_undecryptable"):
                print(yellow(f"  {d.get('device_id','?')} · çözülemedi (şifre farklı?)")); continue
            bits = [f"{s}: ay ~{_fmt_money(v.get('month_so_far', 0))} · {_fmt_int(v.get('record_count', 0))} kayıt"
                    for s, v in (d.get("sources") or {}).items()]
            print(f"  {bold(d.get('label', '?'))} ({d.get('device_id')}) · {' · '.join(bits) or 'veri yok'}")
        return 0

    if act == "status":
        _print_header("sync durumu")
        print(f"  relay:  {cfg.get('relay_url')}")
        print(f"  cihaz:  {cfg.get('device_id')} ({cfg.get('label')})")
        try:
            acc = syncmod.account(cfg)
            print(f"  plan:   {bold(acc.get('plan', '?'))} · cihaz {acc.get('device_count')}/{acc.get('device_limit')}")
        except Exception as e:
            print(yellow(f"  relay'e ulaşılamadı: {e}"))
        return 0
    return 0


def cmd_alerts(args):
    """Local pre-limit alerts — configure destinations in
    ~/.config/burnmeter/alerts.json, then `alerts test` to verify or `alerts check`
    to evaluate now. They also fire automatically from `burnmeter serve` (a local
    background thread, every ~60s). Everything stays on your machine — no phone-home."""
    from . import alerts as al
    act = args.action
    cfg = al.load_config()

    if act == "status":
        _print_header("alert durumu")
        dests = cfg.get("destinations") or {}
        configured = [k for k in ("webhook_url", "slack_webhook_url", "email") if dests.get(k)]
        print(f"  config:    {al.CONFIG_PATH}")
        print(f"  enabled:   {bold('AÇIK' if al.is_enabled(cfg) else 'kapalı')}")
        print(f"  hedefler:  {', '.join(configured) or dim('yok — alerts.json ekle')}")
        print(f"  kaynaklar: {', '.join(cfg.get('sources') or ['claude', 'codex'])}")
        print(dim("  eşik: Codex %85 uyarı / %95 kritik · Claude burn-rate heavy×1.6"))
        return 0

    if act in ("on", "off"):
        cfg["enabled"] = (act == "on")
        if not cfg.get("destinations"):
            print(yellow("not: henüz hedef yok — alerts.json'a webhook/slack/email ekle"))
        al.save_config(cfg)
        print(green(f"✓ alerts {'AÇIK' if act == 'on' else 'kapalı'}"))
        return 0

    if act == "test":
        if not (cfg.get("destinations") or {}):
            print(red(f"hedef yok. Önce {al.CONFIG_PATH} içine webhook/slack/email ekle.")); return 1
        results = al.send_test(cfg)
        for r in results:
            print((green("  ✓ ") if ":ok" in r else red("  ✗ ")) + r)
        return 0 if results and all(":ok" in r for r in results) else 1

    if act == "check":
        src = (getattr(args, "source", "claude") or "claude").lower()
        if src == "codex":
            from .codex_parser import CODEX_SESSIONS_DIR, load_codex_records
            root = (Path(args.codex_dir).expanduser()
                    if getattr(args, "codex_dir", None) else CODEX_SESSIONS_DIR)
            records, stats, intents = load_codex_records(root)
            errors: list = []
        else:
            records, stats, intents, errors = load_records(Path(args.projects_dir))
        if not records:
            print(dim(f"{src}: veri yok")); return 0
        report = build_report(records, plan=args.plan, user_intents=intents,
                              error_events=errors, source=src)
        if src == "codex" and stats.get("rate_limits"):
            report["codex_rate_limits"] = stats["rate_limits"]
        level, msg = al.evaluate(report, src)
        name = al.LEVEL_NAMES.get(level, str(level))
        color = green if level == 0 else (yellow if level == 2 else red)
        print(f"  {src}: {color(name.upper())}  {msg or dim('eşik altında, sorun yok')}")
        if getattr(args, "fire", False) and level >= 2:
            for r in al.dispatch(cfg, level, msg, src):
                print("  → " + r)
        return 0
    return 0


def main(argv=None):
    # Stream hygiene for two Windows realities:
    #   1. pythonw.exe (the windowless desktop-shortcut launch) gives None for
    #      sys.stdout/stderr — any write() then crashes the server before it binds.
    #      Point None streams at a sink so the silent launch path can't crash.
    #   2. The console's locale codepage (cp1254 on Turkish) can't encode the
    #      box-drawing / emoji / Turkish output below → UnicodeEncodeError crashes
    #      every CLI command. Force UTF-8 so output is locale-independent (sibling to
    #      the git-log decode fix in analytics.py).
    for _name in ("stdout", "stderr"):
        _stream = getattr(sys, _name, None)
        if _stream is None:
            try:
                setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))
            except Exception:
                pass
            continue
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="burnmeter")
    parser.add_argument("--projects-dir", default=str(CLAUDE_PROJECTS_DIR))
    parser.add_argument("--plan", default=None,
                        choices=["pro", "max5", "max20"],
                        help="override plan inference")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="one-screen overview")

    p_daily = sub.add_parser("daily", help="per-day breakdown")
    p_daily.add_argument("--limit", type=int, default=14)

    sub.add_parser("models", help="per-model totals")

    p_sessions = sub.add_parser("sessions", help="recent sessions")
    p_sessions.add_argument("--limit", type=int, default=20)

    p_serve = sub.add_parser("serve", help="start the local web dashboard")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=7654)
    p_serve.add_argument("--ttl", type=int, default=15)
    p_serve.add_argument("--no-browser", action="store_true",
                         help="dashboard'ı tarayıcıda otomatik açma")
    p_serve.add_argument("--no-shortcut", action="store_true",
                         help="masaüstü kısayolu oluşturma")
    p_serve.add_argument("--extra-projects-dir", action="append", default=[],
                         help="ek Claude JSONL dizinleri (PC Syncthing mirror'ı vb.). "
                              "Birden çok için flag'i tekrarla.")
    p_serve.add_argument("--codex-dir", default=None,
                         help="Codex sessions kökü (default ~/.codex/sessions)")
    p_serve.add_argument("--codex-extra-dir", action="append", default=[],
                         help="ek Codex sessions dizinleri (PC mirror'ı vb.).")
    p_serve.add_argument("--codex-days", type=int, default=90,
                         help="how many recent days of Codex history to scan "
                              "(default 90; 0 = all-time — slow on a huge ~/.codex)")
    p_serve.add_argument("--tray", action="store_true",
                         help="run in the system tray instead of blocking this console "
                              "(same as: burnmeter tray)")

    # tray — recommended desktop launch: system-tray icon, no console window,
    # right-click Open/Quit. Mirrors serve's options so the two are interchangeable.
    p_tray = sub.add_parser("tray",
                            help="run Burnmeter in the system tray (no console window; recommended)")
    p_tray.add_argument("--host", default="127.0.0.1")
    p_tray.add_argument("--port", type=int, default=7654)
    p_tray.add_argument("--ttl", type=int, default=15)
    p_tray.add_argument("--no-browser", action="store_true",
                        help="don't auto-open the dashboard in the browser")
    p_tray.add_argument("--no-shortcut", action="store_true",
                        help="don't create/upgrade the desktop shortcut")
    p_tray.add_argument("--extra-projects-dir", action="append", default=[],
                        help="extra Claude JSONL dirs (repeat the flag for more)")
    p_tray.add_argument("--codex-dir", default=None,
                        help="Codex sessions root (default ~/.codex/sessions)")
    p_tray.add_argument("--codex-extra-dir", action="append", default=[],
                        help="extra Codex sessions dirs (repeat the flag for more)")
    p_tray.add_argument("--codex-days", type=int, default=90,
                        help="recent days of Codex history to scan (default 90; 0 = all-time)")

    # app — open the dashboard in a native desktop window (pywebview). Attaches
    # to a running server (e.g. the tray) if present; else starts its own.
    p_app = sub.add_parser("app",
                           help="open the dashboard in a native desktop window")
    p_app.add_argument("--host", default="127.0.0.1")
    p_app.add_argument("--port", type=int, default=7654)
    p_app.add_argument("--ttl", type=int, default=15)
    p_app.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    p_app.add_argument("--no-shortcut", action="store_true",
                       help="don't create/upgrade the desktop shortcut")
    p_app.add_argument("--extra-projects-dir", action="append", default=[],
                       help="extra Claude JSONL dirs (repeat the flag for more)")
    p_app.add_argument("--codex-dir", default=None,
                       help="Codex sessions root (default ~/.codex/sessions)")
    p_app.add_argument("--codex-extra-dir", action="append", default=[],
                       help="extra Codex sessions dirs (repeat the flag for more)")
    p_app.add_argument("--codex-days", type=int, default=90,
                       help="recent days of Codex history to scan (default 90; 0 = all-time)")

    p_relaunch = sub.add_parser("_relaunch", help=argparse.SUPPRESS)   # internal (auto-restart)
    p_relaunch.add_argument("--host", default="127.0.0.1")
    p_relaunch.add_argument("--port", type=int, default=7654)
    p_relaunch.add_argument("--no-browser", action="store_true")

    p_update = sub.add_parser("_update", help=argparse.SUPPRESS)   # internal (robust updater)
    p_update.add_argument("--host", default="127.0.0.1")
    p_update.add_argument("--port", type=int, default=7654)

    sub.add_parser("stop",
                   help="stop a backgrounded dashboard (the tray / windowless one)")

    p_sl = sub.add_parser("statusline",
                          help="tek satır canlı durum (Claude Code statusLine.command için)")
    p_sl.add_argument("--source", default="claude", choices=["claude", "codex"],
                      help="hangi araç (default claude)")
    p_sl.add_argument("--json", action="store_true",
                      help="plain text yerine JSON çıktı")
    p_sl.add_argument("--codex-dir", default=None,
                      help="Codex sessions kökü (default ~/.codex/sessions)")

    p_sync = sub.add_parser("sync", help="Pro: cihazlar arası sync (E2E şifreli)")
    p_sync.add_argument("action", choices=["activate", "login", "push", "pull", "status"])
    p_sync.add_argument("--relay", help="relay URL (activate/login)")
    p_sync.add_argument("--email", help="hesap e-postan (activate/login)")
    p_sync.add_argument("--password", help="şifren (verilmezse güvenli şekilde sorulur)")
    p_sync.add_argument("--code", help="tek-kullanımlık aktivasyon kodu (activate)")
    p_sync.add_argument("--label", help="bu cihazın etiketi (default hostname)")
    p_sync.add_argument("--codex-dir", default=None, help="Codex sessions kökü (push)")

    p_desktop = sub.add_parser("desktop", help="masaüstüne 'Burnmeter' kısayolu ekle")
    p_desktop.add_argument("--port", type=int, default=7654)

    p_relay = sub.add_parser("sync-relay", help="Pro: self-host edilebilir sync relay sunucusu")
    p_relay.add_argument("--host", default="127.0.0.1")
    p_relay.add_argument("--port", type=int, default=8899)
    p_relay.add_argument("--storage", default=None, help="ciphertext blob deposu (default ~/.burnmeter-relay)")

    p_acct = sub.add_parser("relay-account",
                            help="Pro: relay müşteri hesapları (create/list/revoke) — /admin'in okuduğu kayıt")
    p_acct.add_argument("action", choices=["create", "list", "revoke"])
    p_acct.add_argument("--email", help="müşteri e-postası (create)")
    p_acct.add_argument("--name", help="müşteri adı (create, opsiyonel)")
    p_acct.add_argument("--plan", default="pro",
                        choices=["pro", "team", "supporter", "license", "free"])
    p_acct.add_argument("--device-limit", type=int, default=None,
                        help="cihaz limiti (verilmezse plana göre otomatik)")
    p_acct.add_argument("--token", help="revoke için ham hesap token'ı")
    p_acct.add_argument("--id", help="revoke için hesap id'si (list'te görünür)")
    p_acct.add_argument("--storage", default=None,
                        help="relay storage (default ~/.burnmeter-relay)")

    p_alerts = sub.add_parser("alerts", help="local pre-limit uyarıları (webhook/Slack/e-posta)")
    p_alerts.add_argument("action", choices=["status", "test", "check", "on", "off"])
    p_alerts.add_argument("--source", default="claude", choices=["claude", "codex"],
                          help="check için kaynak (default claude)")
    p_alerts.add_argument("--codex-dir", default=None, help="Codex sessions kökü (check --source codex)")
    p_alerts.add_argument("--fire", action="store_true",
                          help="check sırasında eşik aşılırsa gerçekten gönder")

    args = parser.parse_args(argv)

    handlers = {
        "status": cmd_status,
        "daily": cmd_daily,
        "models": cmd_models,
        "sessions": cmd_sessions,
        "serve": cmd_serve,
        "tray": cmd_tray,
        "app": cmd_app,
        "_relaunch": cmd_relaunch,
        "_update": cmd_update,
        "stop": cmd_stop,
        "statusline": cmd_statusline,
        "sync": cmd_sync,
        "sync-relay": cmd_sync_relay,
        "relay-account": cmd_relay_account,
        "alerts": cmd_alerts,
        "desktop": cmd_desktop,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
