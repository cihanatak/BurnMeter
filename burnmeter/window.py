"""Native desktop window for the Burnmeter dashboard (pywebview).

Wraps the same local HTTP server in an OS-native window so Burnmeter feels like
a real desktop app instead of "a browser tab on localhost".

Launch model (the desktop icon runs ``burnmeter app``):
  * If a Burnmeter server is already live on this machine, the window ATTACHES
    to it (identity-checked via the pidfile token + /api/health).
  * Otherwise, with ``ensure_background=True`` (the icon path), we spawn a
    DETACHED, windowless **tray** server that OUTLIVES this window process, then
    attach the window to it. Closing the window then leaves the dashboard
    running — quit it from the tray icon. Re-opening the icon attaches instantly.
  * Without ``ensure_background`` (e.g. ``burnmeter app`` from a throwaway shell)
    we host the server in-process; it stops when the window closes.

pywebview is an OPTIONAL dependency (``pip install burnmeter[app]``). If it isn't
installed we fall back to opening the dashboard in the default browser — never a
dead double-click.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from . import __version__


def _running_url() -> Optional[str]:
    """URL of an already-running Burnmeter on this machine, or None.

    Mirrors ``server._already_running``'s identity check: the pidfile's recorded
    port must answer ``/api/health`` with a token that MATCHES the pidfile's
    token (defends against a reused PID or a foreign app squatting the port).
    Read-only — we never unlink here; ``server.setup_server`` self-heals stale
    pidfiles on the next bind."""
    import urllib.request
    pf = Path.home() / ".burnmeter" / "server.json"
    if not pf.exists():
        return None
    try:
        info = json.loads(pf.read_text(encoding="utf-8"))
        rport = int(info["port"])
        rhost = info.get("host", "127.0.0.1")
        rtoken = info.get("token")
    except Exception:
        return None
    probe_host = "127.0.0.1" if rhost in ("0.0.0.0", "") else rhost
    url = f"http://{probe_host}:{rport}"
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=1.0) as r:
            data = json.loads(r.read().decode("utf-8"))
        if isinstance(data, dict) and data.get("ok") and rtoken and data.get("token") == rtoken:
            return url
    except Exception:
        return None
    return None


def _await_running_url(timeout: float = 20.0) -> Optional[str]:
    """Poll _running_url() until a server answers (the detached tray is booting),
    up to `timeout` seconds. Returns the URL or None if it never came up."""
    steps = max(1, int(timeout / 0.5))
    for _ in range(steps):
        url = _running_url()
        if url:
            return url
        time.sleep(0.5)
    return _running_url()


def _kwargs_to_argv(kwargs) -> list:
    """Rebuild serve/tray CLI flags from the _common_kwargs dict, so a detached
    background tray inherits the SAME data sources as this window would use."""
    out = ["--host", str(kwargs.get("host", "127.0.0.1")),
           "--port", str(kwargs.get("port", 7654)),
           "--ttl", str(kwargs.get("ttl_seconds", 15))]
    for r in (kwargs.get("extra_roots") or []):
        out += ["--extra-projects-dir", str(r)]
    if kwargs.get("codex_dir"):
        out += ["--codex-dir", str(kwargs["codex_dir"])]
    for r in (kwargs.get("codex_extra_roots") or []):
        out += ["--codex-extra-dir", str(r)]
    out += ["--codex-days", str(kwargs.get("codex_since_days", 90))]
    return out


def _spawn_background_server(kwargs) -> None:
    """Start a DETACHED, windowless server that outlives this window process.

    Prefer the **tray** (icon + right-click Quit) when pystray is installed; fall
    back to a plain ``serve --no-browser`` otherwise (persists, no icon — quit via
    ``burnmeter stop``). Either way ``--no-browser`` keeps a browser tab from
    popping up: the native window is the surface. On Windows: pythonw + DETACHED/
    NO_WINDOW flags; on POSIX: a new session."""
    import importlib.util
    env = dict(os.environ, BURNMETER_TRAY_DETACHED="1", PYTHONUTF8="1")
    home = str(Path.home())
    win_flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW

    if getattr(sys, "frozen", False):
        # The exe IS the launcher: `Burnmeter.exe tray …`. pystray is bundled.
        argv = [sys.executable, "tray", "--no-browser", "--no-shortcut",
                *_kwargs_to_argv(kwargs)]
        if sys.platform == "win32":
            subprocess.Popen(argv, env=env, creationflags=win_flags, close_fds=True, cwd=home)
        else:
            subprocess.Popen(argv, env=env, start_new_session=True, close_fds=True, cwd=home)
        return

    sub = "tray" if importlib.util.find_spec("pystray") is not None else "serve"
    argv = ["-m", "burnmeter", sub, "--no-browser", "--no-shortcut",
            *_kwargs_to_argv(kwargs)]
    if sys.platform == "win32":
        from . import desktop
        pyw = desktop._pythonw(sys.executable)
        subprocess.Popen([pyw, *argv], env=env, creationflags=win_flags,
                         close_fds=True, cwd=home)
    else:
        subprocess.Popen([sys.executable, *argv], env=env,
                         start_new_session=True, close_fds=True, cwd=home)


def _python_for_window() -> Path:
    """python.exe next to the current interpreter — pywebview's window is broken
    under pythonw.exe (no console), so the window process must be python.exe."""
    py = Path(sys.executable)
    if py.name.lower() == "pythonw.exe":
        cand = py.with_name("python.exe")
        if cand.exists():
            return cand
    return py


def spawn_window(extra_args=None) -> bool:
    """Launch the native window as a DETACHED process under python.exe with a
    HIDDEN console (CREATE_NO_WINDOW on Windows): console allocated so pywebview
    renders, no window so no flash/ghost. Used by the desktop-icon re-exec and by
    the tray's "Open dashboard". Returns True if the process was spawned.

    BURNMETER_APP_REEXEC=1 tells the child's cmd_app to skip re-exec and run the
    window directly (it's already python.exe)."""
    if getattr(sys, "frozen", False):
        # The exe IS the launcher: `Burnmeter.exe app …` (no `python -m`).
        argv = [sys.executable, "app", *(extra_args or [])]
    else:
        argv = [str(_python_for_window()), "-m", "burnmeter", "app", *(extra_args or [])]
    env = dict(os.environ, BURNMETER_APP_REEXEC="1", PYTHONUTF8="1")
    home = str(Path.home())
    try:
        if sys.platform == "win32":
            subprocess.Popen(argv, env=env, creationflags=0x08000000,  # CREATE_NO_WINDOW
                             close_fds=True, cwd=home)
        else:
            subprocess.Popen(argv, env=env, start_new_session=True,
                             close_fds=True, cwd=home)
        return True
    except Exception:
        return False


def run_window(open_browser: bool = False, ensure_background: bool = False, **kwargs) -> int:
    """Open the dashboard in a native window.

    ``ensure_background`` (the desktop-icon path): when nothing is running, spawn
    a detached tray server that persists after the window closes, then attach.
    ``open_browser`` is accepted for signature parity; on the window path it's
    ignored, on the no-pywebview fallback it's forced on.
    """
    try:
        import webview  # pywebview
    except Exception:
        from .server import serve
        sys.stderr.write(
            "[burnmeter] pywebview not installed — opening in your browser instead.\n"
            "            For the native window: pip install burnmeter[app]\n")
        serve(open_browser=True, **kwargs)
        return 0

    from .server import setup_server

    handle = None                       # set only when WE host the server in-process
    existing = _running_url()
    if existing:
        url = existing
        sys.stderr.write(f"[burnmeter] attaching window to running server {url}\n")
    elif ensure_background:
        sys.stderr.write("[burnmeter] starting background server…\n")
        _spawn_background_server(kwargs)
        url = _await_running_url(20.0)
        if not url:
            # Detached server didn't come up → host in-process; this server
            # stops when the window closes (no persistence, but it works).
            sys.stderr.write(
                "[burnmeter] background server didn't start — hosting in-window instead\n")
            handle = setup_server(**kwargs)
            threading.Thread(target=handle.server.serve_forever, daemon=True).start()
            handle.start_warm()
            url = handle.url
    else:
        try:
            handle = setup_server(**kwargs)
        except RuntimeError as e:
            sys.stderr.write(f"[burnmeter] ERROR: {e}\n")
            return 1
        threading.Thread(target=handle.server.serve_forever, daemon=True).start()
        handle.start_warm()
        url = handle.url

    # maximized=True is load-bearing: under pythonw.exe (the desktop-icon
    # interpreter) pywebview's auto-centering miscomputes screen metrics and
    # parks the window OFF-SCREEN at a huge negative coord, collapsed to a
    # title-bar sliver. A maximized window's geometry is computed by Windows to
    # fill the monitor, bypassing pywebview's broken positioning — so it can
    # never land off-screen. (python.exe positions fine; pythonw does not.)
    webview.create_window(
        f"Burnmeter v{__version__}", url,
        width=1280, height=860, min_size=(900, 600), maximized=True)
    try:
        webview.start()           # blocks on the main thread until the window closes
    finally:
        if handle is not None:    # only tear down a server WE own; never the tray
            handle.close()
    return 0
