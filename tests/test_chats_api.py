"""Every chat reaches the Chats page.

The report kept the newest 200 chats and the page showed 60 of them, with no way
to see the rest. The report now has every chat; /api/report, which the dashboard
polls every 10 s, carries the newest 200 with the count of all, and /api/chats
has them all.
"""
from __future__ import annotations

import json
import threading
import urllib.request

from burnmeter import server as srv


def _get(port: int, path: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return json.loads(r.read())


def test_the_poll_carries_the_newest_chats_and_api_chats_has_all(monkeypatch, tmp_path):
    chats = [{"session_id": f"s{i}", "total_tokens": 10,
              "ended_at": f"2026-10-29T{23 - i // 60:02d}:{59 - i % 60:02d}:00Z"} for i in range(250)]
    monkeypatch.setattr(srv, "build_report", lambda *a, **k: {"by_session": list(chats)})
    cache = srv._Cache(projects_dir=tmp_path, ttl_seconds=999, loader=lambda: ([], {}, {}, []))
    httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.make_handler(cache))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        rep = _get(port, "/api/report")
        full = _get(port, "/api/chats")
        again = _get(port, "/api/report")
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert [s["session_id"] for s in rep["by_session"]] == [f"s{i}" for i in range(200)]
    assert rep["by_session_total"] == 250
    assert full["source"] == "claude" and len(full["by_session"]) == 250
    assert len(again["by_session"]) == 200, "serving the poll doesn't cut the cached report"
