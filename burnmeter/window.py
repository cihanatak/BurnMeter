"""Native desktop window for the Burnmeter dashboard (pywebview).

Wraps the same local HTTP server in an OS-native window so Burnmeter feels like
a real desktop app instead of "a browser tab on localhost".

Reuse over duplication: if a Burnmeter server is already serving on this machine
(e.g. the tray), the window ATTACHES to it — closing the window then leaves that
server running. Only when nothing is running do we start our own server (on a
daemon thread) and tear it down when the window closes.

pywebview is an OPTIONAL dependency (``pip install burnmeter[app]``). If it isn't
installed we fall back to opening the dashboard in the default browser — never a
dead double-click.
"""
from __future__ import annotations

import json
import sys
import threading
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


def run_window(open_browser: bool = False, **kwargs) -> int:
    """Open the dashboard in a native window.

    ``open_browser`` is accepted for signature parity with ``serve``/``run_tray``.
    On the window path it's ignored (the window IS the surface); on the
    no-pywebview fallback it's forced on so the launch still shows something.
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

    existing = _running_url()
    handle = None
    if existing:
        url = existing
        sys.stderr.write(f"[burnmeter] attaching window to running server {url}\n")
    else:
        try:
            handle = setup_server(**kwargs)
        except RuntimeError as e:
            sys.stderr.write(f"[burnmeter] ERROR: {e}\n")
            return 1
        threading.Thread(target=handle.server.serve_forever, daemon=True).start()
        handle.start_warm()
        url = handle.url

    webview.create_window(
        f"Burnmeter v{__version__}", url,
        width=1480, height=980, min_size=(940, 640))
    try:
        webview.start()           # blocks on the main thread until the window closes
    finally:
        if handle is not None:
            handle.close()
    return 0
