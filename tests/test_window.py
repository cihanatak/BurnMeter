"""run_window() logic — verified without opening a real GUI window.

We inject a fake `webview` module (create_window/start are no-ops that record
their args) so the three paths are exercised headlessly:
  1. attach to an already-running server (no new server started)
  2. start our own server when none is running (warm + teardown)
  3. fall back to the browser when pywebview isn't importable
"""
import sys
import types
from pathlib import Path

import burnmeter.window as W
import burnmeter.server as S


def _fake_webview():
    m = types.ModuleType("webview")
    m._calls = {}

    def create_window(title, url, **kw):
        m._calls.update(title=title, url=url, kw=kw)
        return object()

    def start(*a, **k):
        m._calls["started"] = True

    m.create_window = create_window
    m.start = start
    return m


_KW = dict(host="127.0.0.1", port=9999, projects_dir=Path("."), ttl_seconds=15,
           extra_roots=[], codex_dir=None, codex_extra_roots=[], codex_since_days=90)


def test_window_attaches_to_running_server(monkeypatch):
    fake = _fake_webview()
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(W, "_running_url", lambda: "http://127.0.0.1:7654")

    def _boom(**k):
        raise AssertionError("must not start a second server when one is live")
    monkeypatch.setattr(S, "setup_server", _boom)

    rc = W.run_window(**_KW)
    assert rc == 0
    assert fake._calls["url"] == "http://127.0.0.1:7654"
    assert fake._calls.get("started") is True
    assert fake._calls["title"].startswith("Burnmeter v")


def test_window_starts_own_when_none(monkeypatch):
    fake = _fake_webview()
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(W, "_running_url", lambda: None)

    events = []

    class FakeServer:
        def serve_forever(self):
            events.append("serve_forever")

    class FakeHandle:
        def __init__(self):
            self.server = FakeServer()
            self.url = "http://127.0.0.1:9999"

        def start_warm(self):
            events.append("warm")

        def close(self):
            events.append("close")

    monkeypatch.setattr(S, "setup_server", lambda **k: FakeHandle())

    rc = W.run_window(**_KW)
    assert rc == 0
    assert fake._calls["url"] == "http://127.0.0.1:9999"
    assert "warm" in events          # cache pre-build kicked
    assert "close" in events         # server torn down after window closed


def test_window_ensure_background_spawns_and_attaches(monkeypatch):
    """Desktop-icon path: nothing running → spawn a detached tray, then attach
    the window to it. No in-process server is hosted."""
    fake = _fake_webview()
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(W, "_running_url", lambda: None)        # nothing live yet
    spawned = {"n": 0}
    monkeypatch.setattr(W, "_spawn_background_server",
                        lambda kw: spawned.__setitem__("n", spawned["n"] + 1))
    monkeypatch.setattr(W, "_await_running_url",
                        lambda *a, **k: "http://127.0.0.1:7654")

    def _boom(**k):
        raise AssertionError("must not host in-process when the tray came up")
    monkeypatch.setattr(S, "setup_server", _boom)

    rc = W.run_window(ensure_background=True, **_KW)
    assert rc == 0
    assert spawned["n"] == 1
    assert fake._calls["url"] == "http://127.0.0.1:7654"


def test_window_ensure_background_falls_back_in_process(monkeypatch):
    """If the detached tray never answers (e.g. pystray missing), host the server
    in-process so the window still works."""
    fake = _fake_webview()
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(W, "_running_url", lambda: None)
    monkeypatch.setattr(W, "_spawn_background_server", lambda kw: None)
    monkeypatch.setattr(W, "_await_running_url", lambda *a, **k: None)  # never came up

    events = []

    class FakeServer:
        def serve_forever(self):
            events.append("serve")

    class FakeHandle:
        def __init__(self):
            self.server = FakeServer()
            self.url = "http://127.0.0.1:7777"

        def start_warm(self):
            events.append("warm")

        def close(self):
            events.append("close")

    monkeypatch.setattr(S, "setup_server", lambda **k: FakeHandle())

    rc = W.run_window(ensure_background=True, **_KW)
    assert rc == 0
    assert fake._calls["url"] == "http://127.0.0.1:7777"
    assert "close" in events            # we own this one → torn down on close


def test_spawn_window_builds_app_command(monkeypatch):
    """spawn_window launches `python.exe -m burnmeter app <flags>` detached with
    the re-exec guard set, and NEVER under pythonw.exe."""
    calls = {}

    def fake_popen(argv, **kw):
        calls["argv"] = argv
        calls["env"] = kw.get("env", {})

        class _P:
            pass
        return _P()

    monkeypatch.setattr(W.subprocess, "Popen", fake_popen)
    ok = W.spawn_window(["--port", "9000"])
    assert ok is True
    assert calls["argv"][1:4] == ["-m", "burnmeter", "app"]
    assert calls["argv"][4:] == ["--port", "9000"]
    assert calls["env"].get("BURNMETER_APP_REEXEC") == "1"
    assert "pythonw.exe" not in calls["argv"][0].lower()   # window needs a console


def test_window_fallback_without_pywebview(monkeypatch):
    # None in sys.modules makes `import webview` raise ImportError.
    monkeypatch.setitem(sys.modules, "webview", None)
    called = {}
    monkeypatch.setattr(S, "serve", lambda **k: called.setdefault("serve", k))

    rc = W.run_window(**_KW)
    assert rc == 0
    assert called.get("serve") is not None        # fell back to browser serve
    assert called["serve"]["port"] == 9999
