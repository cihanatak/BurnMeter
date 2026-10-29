"""The dashboard loads nothing from the network: Chart.js is bundled.

It used to come from cdn.jsdelivr.net, so an offline machine (or a blocked CDN)
lost every chart, and the page depended on a third-party host at each load.
"""
from __future__ import annotations

import base64
import hashlib
import re
import threading
import urllib.request

from burnmeter import server as srv

# chart.js@4.4.7 dist/chart.umd.js, as listed by the npm package.
_CHART_JS_SHA256 = "KBLLiCX9xXRp6y97sFXpQpJE5ZmSBRHuR36ChJm2Mss="


def test_the_dashboard_references_no_remote_script_or_style():
    html = (srv.STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    assert not re.search(r'<(?:script|link)\b[^>]*\b(?:src|href)="(?:https?:)?//', html)
    assert 'src="/static/chart.umd.js' in html


def test_the_bundled_chart_js_is_the_published_file_and_is_served(tmp_path):
    body = (srv.STATIC_DIR / "chart.umd.js").read_bytes()
    assert base64.b64encode(hashlib.sha256(body).digest()).decode() == _CHART_JS_SHA256

    cache = srv._Cache(projects_dir=tmp_path, ttl_seconds=999, loader=lambda: ([], {}, {}, []))
    httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.make_handler(cache))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/static/chart.umd.js?v=4.4.7"
        with urllib.request.urlopen(url, timeout=5) as r:
            assert r.status == 200
            assert r.headers["Content-Type"].startswith("application/javascript")
            assert r.read() == body
    finally:
        httpd.shutdown()
        httpd.server_close()
