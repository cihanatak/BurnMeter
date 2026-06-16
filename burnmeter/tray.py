"""System-tray launcher for Burnmeter (Ollama / Syncthing style).

Why this exists: a local dashboard is a web server — it can't outlive its
process. The old `pythonw` launch hid the console but left an invisible "ghost"
process you could only stop with `burnmeter stop`. The tray gives the process a
face: a small notification-area icon with **Open dashboard** / **Quit**, so it's
visibly running and stoppable without a terminal.

Threading model (the part that's easy to get wrong):
- The HTTP server runs serve_forever() on a DAEMON thread.
- pystray's Icon owns the MAIN thread (required on Windows/macOS event loops).
- Teardown (server.shutdown() → join → close) runs in the finally on the MAIN
  thread — never on serve_forever's own thread (that would deadlock). The Quit
  menu item only calls icon.stop(), which returns control to the main thread.

pystray + Pillow are an OPTIONAL dependency (`pip install burnmeter[tray]`); both
are imported lazily and any failure is raised as TrayUnavailable so the CLI can
fall back to console mode. Keep those imports inside function bodies so the base
package stays dependency-free.
"""
from __future__ import annotations

import os
import sys
import threading

from . import __version__
from .server import setup_server, _already_running, _open_browser_async


class TrayUnavailable(Exception):
    """Raised when the system tray can't be used here (dependency missing, no
    display, backend failure). The CLI catches this and falls back to console
    mode — it should NEVER be a silent dead double-click."""


def _make_image():
    """PIL-generated 64x64 flame-in-a-disc icon — no asset file is shipped.
    (PIL imported here, not at module scope, so the base package needs no deps.)"""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=(20, 20, 24, 255))            # dark disc
    d.polygon([(32, 11), (46, 36), (41, 52), (23, 52), (18, 36)],
              fill=(255, 122, 26, 255))                          # outer flame
    d.polygon([(32, 26), (40, 42), (32, 53), (24, 42)],
              fill=(255, 209, 102, 255))                         # inner flame
    return img


def run_tray(host: str = "127.0.0.1", port: int = 7654, projects_dir=None,
             ttl_seconds: int = 15, extra_roots=None, codex_dir=None,
             codex_extra_roots=None, codex_since_days: int = 90,
             open_browser: bool = True) -> int:
    """Run Burnmeter in the system tray. Blocks on the main thread until Quit.

    Raises TrayUnavailable if the tray can't run here (so the caller falls back
    to console mode). Raises RuntimeError if called off the main thread."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("run_tray() must run on the main thread (pystray requirement)")

    # Headless Linux has no notification area — skip straight to the fallback.
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") \
            and not os.environ.get("WAYLAND_DISPLAY"):
        raise TrayUnavailable("no display (headless Linux)")

    # If a Burnmeter is already running, just open it — never bind a 2nd server.
    if _already_running(host, port, open_browser=open_browser):
        return 0

    # Build the icon + import pystray BEFORE binding, so a missing/broken
    # dependency raises with nothing bound (no orphaned port/pidfile).
    try:
        import pystray
        image = _make_image()
    except Exception as e:                       # ImportError, PIL/backend errors
        raise TrayUnavailable(f"tray unavailable: {e}") from e

    h = setup_server(host, port, projects_dir, ttl_seconds, extra_roots,
                     codex_dir, codex_extra_roots, codex_since_days)

    srv_thread = threading.Thread(target=h.server.serve_forever, daemon=True)
    started = False
    try:
        srv_thread.start()
        started = True
        h.start_warm()

        def _on_ready(icon):
            # pystray calls setup() on its loop thread once the icon is actually
            # registered → open the browser HERE, not before run() (avoids racing
            # a not-yet-visible icon / not-yet-accepting socket).
            icon.visible = True
            if open_browser:
                _open_browser_async(h.url)

        def _on_open(icon, item):
            _open_browser_async(h.url)

        def _on_quit(icon, item):
            icon.stop()          # returns control to icon.run() on the main thread

        menu = pystray.Menu(
            pystray.MenuItem("Open dashboard", _on_open, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Burnmeter", _on_quit),
        )
        icon = pystray.Icon(
            "burnmeter", icon=image,
            title=f"Burnmeter v{__version__} — {h.url}",
            menu=menu,
        )
        icon.run(setup=_on_ready)        # BLOCKS on the main thread until Quit
    except Exception as e:
        raise TrayUnavailable(f"tray failed: {e}") from e
    finally:
        # One teardown for every exit path (Quit, logout, late failure). Runs on
        # the MAIN thread — not serve_forever's thread — so shutdown() can't
        # deadlock; guarded by `started` so it can't hang if serving never began.
        if started:
            try:
                h.server.shutdown()
            except Exception:
                pass
            srv_thread.join(timeout=5)
        h.close()
    return 0
