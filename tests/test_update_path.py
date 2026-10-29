"""One-click update from the dashboard, as the installed (frozen) app runs it.

v0.3.4 spawned `burnmeter-server.exe -m burnmeter _update` in the frozen app;
the CLI exited at once on "-m", nothing was logged, and the dashboard said
"✓ Updated" after 150 s. Frozen builds now run the release installer, and
/api/health reports what happened.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

from burnmeter import server as srv
from burnmeter import updater


def test_installer_url_names_the_release_the_dashboard_saw():
    assert updater.installer_url("0.3.5").endswith("/releases/download/v0.3.5/BurnmeterSetup.exe")
    assert updater.installer_url("") == updater.INSTALLER_URL
    assert updater.installer_url("0.3.5; del *") == updater.INSTALLER_URL


def _health(port: int) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
        return json.loads(r.read())


def test_a_frozen_update_runs_the_installer_and_reports_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)       # update_error.log lands here
    asked = []

    def _fake_installer(url):
        asked.append(url)
        return False                                          # e.g. the release isn't published
    monkeypatch.setattr(updater, "run_installer_update", _fake_installer)

    def _no_spawn(*a, **k):
        raise AssertionError("a frozen build has no Python to run -m burnmeter _update")
    monkeypatch.setattr(srv, "_spawn_update", _no_spawn)

    cache = srv._Cache(projects_dir=tmp_path, ttl_seconds=999, loader=lambda: ([], {}, {}, []))
    httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.make_handler(cache))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        assert _health(port)["frozen"] is True
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/update", method="POST",
            data=json.dumps({"version": "0.3.5"}).encode(),
            headers={"X-Burnmeter-Update": "1", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert json.loads(r.read())["ok"] is True
        deadline = time.time() + 10
        while time.time() < deadline and (_health(port).get("update") or {}).get("state") != "failed":
            time.sleep(0.1)
        state = _health(port)["update"]
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert asked == [updater.installer_url("0.3.5")]
    assert state["state"] == "failed" and "v0.3.5" in state["error"]
    assert "update spawn failed" in (tmp_path / ".burnmeter" / "update_error.log").read_text()
