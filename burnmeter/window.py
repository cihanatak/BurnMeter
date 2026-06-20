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


# --- single-instance: never stack a 2nd native window ----------------------
# A real app has ONE window. Every launch path (desktop icon, tray "Open", the
# update restart, the installer's relaunch) routes through run_window; the guard
# below makes any launch-while-open just FOCUS the existing window instead of
# opening another. Race-safe (atomic O_EXCL lock) so the update restart — which
# can fire two launches at once — still yields exactly one window.
_WINDOW_LOCK = Path.home() / ".burnmeter" / "window.lock"


def _win_pid_alive(pid: int) -> bool:
    """Reliable 'is this pid running?' — Windows via ctypes OpenProcess + exit-code
    (tasklist is flaky), POSIX via os.kill(pid, 0). Used to detect a stale lock left
    by a crashed window so the next launch can steal it."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            try:
                code = ctypes.c_ulong()
                if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                    return True            # advisory: don't falsely steal a live lock
                return code.value == 259   # STILL_ACTIVE
            finally:
                k32.CloseHandle(h)
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _focus_existing_window() -> bool:
    """Bring an already-open Burnmeter window to the front (Windows-only, ctypes
    EnumWindows matched by the "Burnmeter v…" title). Returns True if one was found."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hits = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _lp):
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value.startswith("Burnmeter v"):
                    hits.append(hwnd)
                    return False           # stop enumerating
            return True

        user32.EnumWindows(_cb, 0)
        if hits:
            user32.ShowWindow(hits[0], 9)            # SW_RESTORE (un-minimize)
            user32.SetForegroundWindow(hits[0])
            return True
    except Exception:
        pass
    return False


def _claim_window_singleton() -> bool:
    """True iff THIS process won the lock and may open the window. False → a lock
    owned by a still-alive PID exists (the caller then decides: focus the real window
    if there is one, else steal). Race-safe: O_EXCL serializes the create, and a
    stale lock (dead owner) is removed then RE-CLAIMED via the loop — a process that
    loses the unlink race re-loops to the create and never falsely returns True."""
    try:
        _WINDOW_LOCK.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return True                                   # can't lock → never block the window
    for _ in range(6):
        try:
            fd = os.open(str(_WINDOW_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode("ascii"))
            finally:
                os.close(fd)
            return True                               # we created it → we own it
        except FileExistsError:
            try:
                opid = int((_WINDOW_LOCK.read_text() or "0").strip() or "0")
            except FileNotFoundError:
                continue                              # vanished mid-read → retry the create
            except Exception:
                opid = 0
            if opid == os.getpid():
                return True
            if opid and _win_pid_alive(opid):
                return False                          # a live owner holds it
            try:
                _WINDOW_LOCK.unlink()                 # stale (dead owner) → remove…
            except FileNotFoundError:
                pass                                  # a peer already removed it
            except Exception:
                return True
            continue                                  # …and RE-CLAIM via O_EXCL (never bare True)
        except Exception:
            return True
    return True


def _steal_window_singleton() -> None:
    """Force this process to own the lock — used only when the lock is held by a live
    PID that owns NO Burnmeter window (a crashed window + reused PID), so we must open
    rather than strand the user. Best-effort; the window opening is what matters."""
    try:
        _WINDOW_LOCK.unlink()
    except Exception:
        pass
    try:
        _WINDOW_LOCK.write_text(str(os.getpid()))
    except Exception:
        pass


def _lock_owner_alive() -> bool:
    """True if the lock is currently held by a still-running OTHER process. Used by a
    launch that lost the race to decide: keep waiting for the winner's window (owner
    alive) vs take over (owner gone)."""
    try:
        opid = int((_WINDOW_LOCK.read_text() or "0").strip() or "0")
    except Exception:
        return False
    return bool(opid) and opid != os.getpid() and _win_pid_alive(opid)


def _release_window_singleton() -> None:
    try:
        if _WINDOW_LOCK.exists() and (_WINDOW_LOCK.read_text() or "").strip() == str(os.getpid()):
            _WINDOW_LOCK.unlink()
    except Exception:
        pass


# --- window geometry: reopen at the user's last position + size --------------
_WINDOW_GEOM = Path.home() / ".burnmeter" / "window_geometry.json"


def _virtual_screen():
    """(x, y, w, h) of the whole virtual desktop (all monitors) on Windows, else None.
    Used to reject a saved position that's now off-screen (monitor unplugged / resolution
    change) so the window never reopens where the user can't see it."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        u = ctypes.windll.user32
        return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),   # SM_X/YVIRTUALSCREEN
                u.GetSystemMetrics(78), u.GetSystemMetrics(79))    # SM_CX/CYVIRTUALSCREEN
    except Exception:
        return None


def _is_maximized(w: int, h: int) -> bool:
    """Heuristic: a window roughly the size of the primary monitor is 'maximized'."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        u = ctypes.windll.user32
        return w >= u.GetSystemMetrics(0) - 24 and h >= u.GetSystemMetrics(1) - 96
    except Exception:
        return False


def _load_geometry() -> Optional[dict]:
    """Last window placement so a reopen restores where the user left it. Returns
    {x,y,width,height,maximized} or None (→ default maximized) when the saved spot is no
    longer visible on the current monitor layout."""
    try:
        g = json.loads(_WINDOW_GEOM.read_text())
        x, y, w, h = int(g["x"]), int(g["y"]), int(g["width"]), int(g["height"])
        maximized = bool(g.get("maximized"))
        if w < 600 or h < 400 or w > 20000 or h > 20000:
            return None
        if abs(x) > 50000 or abs(y) > 50000:          # absurd coord (corrupt / portable guard)
            return None
        vs = _virtual_screen()
        if vs and not maximized:
            vx, vy, vw, vh = vs
            # require a visible chunk of the title bar on some monitor
            if (x + w) < vx + 80 or x > (vx + vw - 80) or y < vy - 1 or y > (vy + vh - 40):
                return None
        return {"x": x, "y": y, "width": w, "height": h, "maximized": maximized}
    except Exception:
        return None


def _save_geometry(g: dict) -> None:
    try:
        _WINDOW_GEOM.parent.mkdir(parents=True, exist_ok=True)
        _WINDOW_GEOM.write_text(json.dumps(g))
    except Exception:
        pass


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

    # Single-instance: a real app has ONE window. _focus_existing_window() is the
    # AUTHORITATIVE "is a window already open?" check (Windows); the lock is only a
    # tiebreaker for the update restart's near-simultaneous twin launches. The cardinal
    # rule: NEVER exit with zero windows — if we can't claim the lock AND can't surface
    # an existing window, the lock is stale/misleading, so steal it and open ours.
    # (Otherwise a crashed window's leftover lock — esp. with a reused PID — would
    # silently lock the user out, which is worse than the duplicate it prevents.)
    if _focus_existing_window():
        return 0
    owns_lock = _claim_window_singleton()
    if not owns_lock:
        if sys.platform == "win32":
            # Lost the race to a concurrent launch. Its window can take many seconds to
            # appear on a COLD start (it spawns + awaits the background server), so wait
            # as long as that winner is ALIVE — focus its window the moment it shows.
            # Only take over if the winner vanishes before rendering (so we never
            # strand the user) or after a generous cap (a wedged winner).
            deadline = time.monotonic() + 25.0
            while time.monotonic() < deadline:
                if _focus_existing_window():
                    return 0
                if not _lock_owner_alive():
                    break                 # winner died before showing a window → we take over
                time.sleep(0.3)
            _steal_window_singleton()
            owns_lock = True
        else:
            # No reliable cross-process window focus here — if a live instance owns the
            # lock, defer to it (one window); only open if its owner is gone.
            if _lock_owner_alive():
                return 0
            _steal_window_singleton()
            owns_lock = True

    handle = None                       # set only when WE host the server in-process
    try:
        from .server import setup_server
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

        # Reopen at the user's last position + size. First run (no saved geometry) →
        # maximized=True, which is load-bearing: under pythonw.exe (the desktop-icon
        # interpreter) pywebview's auto-centering miscomputes screen metrics and parks
        # the window OFF-SCREEN; a maximized window's geometry is computed by Windows so
        # it can never land off-screen. (python.exe positions fine; pythonw does not.)
        geo = _load_geometry()
        if geo and not geo.get("maximized"):
            win = webview.create_window(
                f"Burnmeter v{__version__}", url,
                x=geo["x"], y=geo["y"], width=max(900, geo["width"]), height=max(600, geo["height"]),
                min_size=(900, 600))
        else:
            # First run OR last session was maximized → maximized (also dodges pythonw's
            # broken off-screen auto-centering).
            win = webview.create_window(
                f"Burnmeter v{__version__}", url,
                width=(geo["width"] if geo else 1280), height=(geo["height"] if geo else 860),
                min_size=(900, 600), maximized=True)
        # Persist placement on move/resize. Track maximized so a maximized session reopens
        # maximized (not a screen-sized floating window). Pre-seed from geo / 0,0.
        _g = dict(geo) if geo else {"x": 0, "y": 0}
        def _remember(**kw):
            _g.update(kw)
            if all(k in _g for k in ("x", "y", "width", "height")):
                _save_geometry({"x": int(_g["x"]), "y": int(_g["y"]),
                                "width": int(_g["width"]), "height": int(_g["height"]),
                                "maximized": _is_maximized(int(_g["width"]), int(_g["height"]))})
        try:
            win.events.resized += (lambda w, h: _remember(width=w, height=h))
            win.events.moved += (lambda x, y: _remember(x=x, y=y))
        except Exception:
            pass
        # Diagnostic: log window LIFECYCLE events (not resized/moved — too noisy) to a small
        # capped file. Lets an unexplained "window dropped to the taskbar on its own" report
        # be traced to its trigger: a lone `minimized` with no preceding `closed`/`shown`
        # means WebView2/OS minimized it; a `closed`→`shown` pair means our code recreated it.
        def _wlog(ev):
            try:
                p = Path.home() / ".burnmeter" / "window_events.log"
                try:
                    if p.exists() and p.stat().st_size > 65536:
                        p.write_text("", encoding="utf-8")   # keep it tiny
                except Exception:
                    pass
                with open(p, "a", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + ev + "\n")
            except Exception:
                pass
        for _name in ("shown", "minimized", "restored", "maximized", "closing", "closed", "loaded"):
            try:
                evt = getattr(win.events, _name, None)
                if evt is not None:
                    evt += (lambda *a, _n=_name: _wlog(_n))
            except Exception:
                pass
        _wlog("window-created v" + __version__)
        # Persist the WebView profile (localStorage: zoom level + client prefs) across
        # restarts — private_mode=True (the default) would forget them every launch.
        storage = Path.home() / ".burnmeter" / "webview"
        try:
            storage.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            webview.start(private_mode=False, storage_path=str(storage))
        except TypeError:
            webview.start()           # older pywebview without these kwargs
        return 0
    finally:
        if owns_lock:
            _release_window_singleton()   # free the single-instance lock for the next launch
        if handle is not None:    # only tear down a server WE own; never the tray
            handle.close()
