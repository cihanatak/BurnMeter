"""A fresh install's first load: the dashboard gets an answer at once.

Burnmeter reads every log once after it's installed, which takes minutes on a big
history (7.8 GB of Claude Code logs on the maker's PC). The page used to wait on that
read, give up after 45 s and show "failed to load: signal is aborted without reason"
until it was done. Now /api/report answers 202 with the read's progress, then a quick
report of the last day (flagged partial) until the full one is in.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from burnmeter import codex_parser, parser, server as srv
from burnmeter.parser import load_records
from test_codex_correctness import write_rollout

KEY = ("claude", None)


def _get(port: int, path: str):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _poll(port: int, path: str, until, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while True:
        status, body = _get(port, path)
        if until(status, body) or time.monotonic() > deadline:
            return status, body
        time.sleep(0.05)


def _serve(cache):
    httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.make_handler(cache))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class _Worker:
    """Stands in for the build worker: each report comes when the test lets it go."""

    def __init__(self, progress=None):
        self.quick_go, self.full_go = threading.Event(), threading.Event()
        self.calls = []
        self.progress = progress

    def __call__(self, overrides=None, progress_path=None, timeout=1200):
        self.calls.append(overrides)
        if overrides:                                   # the quick report of the last day
            self.quick_go.wait(10)
            return {"by_session": [{"session_id": "today"}], "_meta": {"source": "claude"}}
        if self.progress and progress_path:
            Path(progress_path).write_text(json.dumps(self.progress), encoding="utf-8")
        self.full_go.wait(10)
        return {"by_session": [{"session_id": "today"}, {"session_id": "last-month"}],
                "_meta": {"source": "claude"}}

    def release(self):
        self.quick_go.set()
        self.full_go.set()


def test_a_fresh_install_shows_its_last_day_then_everything(monkeypatch, tmp_path):
    monkeypatch.setattr(srv, "_FIRST_BUILD_WAIT_S", 0.1)
    worker = _Worker(progress={"phase": "reading", "started_at": time.time() - 10,
                               "files_done": 10, "files_total": 40,
                               "bytes_done": 250, "bytes_total": 1000})
    cache = srv._Cache(projects_dir=tmp_path, ttl_seconds=999, worker_config={"source": "claude"},
                       quick_config={"since_hours": 24}, cache_file=tmp_path / "no-cache-yet.json")
    cache._run_worker = worker
    httpd = _serve(cache)
    port = httpd.server_address[1]
    try:
        warming = _get(port, "/api/report")
        worker.quick_go.set()
        status, partial = _poll(port, "/api/report", lambda s, b: (
            ((b.get("_meta") or {}).get("progress") or {}).get("files_total")))
        chats = _get(port, "/api/chats")[1]
        worker.full_go.set()
        _, full = _poll(port, "/api/report", lambda s, b: s == 200 and not b["_meta"].get("partial"))
    finally:
        worker.release()
        httpd.shutdown()
        httpd.server_close()

    assert warming[0] == 202 and warming[1]["warming"] is True
    assert warming[1]["progress"]["phase"] == "quick" and warming[1]["progress"]["fresh"] is True

    assert status == 200 and partial["_meta"]["partial"] is True
    assert [s["session_id"] for s in partial["by_session"]] == ["today"]
    progress = partial["_meta"]["progress"]
    assert progress["fresh"] is True and progress["files_done"] == 10
    assert 29 <= progress["eta_s"] <= 45, "a quarter read in ~10 s leaves ~30 s"
    assert chats["partial"] is True and len(chats["by_session"]) == 1

    assert "partial" not in full["_meta"] and len(full["by_session"]) == 2
    assert worker.calls == [{"since_hours": 24}, None]
    assert cache.progress()["building"] is False


def test_a_restart_with_a_parse_cache_skips_the_quick_report(tmp_path):
    # With the parse cache on disk, only the changed logs are read: a quick report
    # would only hold the full one up.
    (tmp_path / "cache.json").write_text("{}", encoding="utf-8")
    worker = _Worker()
    cache = srv._Cache(projects_dir=tmp_path, ttl_seconds=999, worker_config={"source": "claude"},
                       quick_config={"since_hours": 24}, cache_file=tmp_path / "cache.json")
    cache._run_worker = worker
    try:
        report, quick, progress = cache.peek(lambda *a: None, KEY, wait_s=0.1)
        worker.full_go.set()
        done = cache.peek(lambda *a: None, KEY, wait_s=5)
    finally:
        worker.release()
    assert report is None and quick is None
    assert progress["building"] is True and progress["fresh"] is False
    assert progress["phase"] == "reading"
    assert len(done[0]["by_session"]) == 2 and worker.calls == [None]


def test_a_first_read_that_fails_says_why_and_is_not_retried_in_a_loop(tmp_path):
    calls = []

    def loader():
        calls.append(1)
        raise OSError("the logs drive was unplugged")

    cache = srv._Cache(projects_dir=tmp_path, ttl_seconds=999, loader=loader)
    cache.peek(lambda *a: None, KEY, wait_s=0.3)
    _, _, progress = cache.peek(lambda *a: None, KEY, wait_s=0.3)
    assert progress["error"] == "the logs drive was unplugged"
    assert progress["building"] is False
    assert calls == [1], "a failed first read waits 30 s before the next try"


def test_the_statusline_does_not_wait_on_a_first_read(tmp_path):
    # Claude Code runs the statusline every few seconds; each call held a server
    # thread for the whole first read. The CLI computes its own on a 503.
    worker = _Worker()
    cache = srv._Cache(projects_dir=tmp_path, ttl_seconds=999, worker_config={"source": "claude"},
                       cache_file=tmp_path / "cache.json")
    cache._run_worker = worker
    httpd = _serve(cache)
    port = httpd.server_address[1]
    try:
        started = time.monotonic()
        status, body = _get(port, "/api/statusline?format=json")
        took = time.monotonic() - started
    finally:
        worker.release()
        httpd.shutdown()
        httpd.server_close()
    assert status == 503 and body["warming"] is True
    assert took < 1.0


def _claude_logs(root: Path, n: int) -> list[Path]:
    paths = []
    for i in range(n):
        path = root / "proj" / f"s{i}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "type": "assistant", "timestamp": "2026-10-29T10:00:00.000Z", "sessionId": f"s{i}",
            "uuid": f"u{i}", "cwd": "/tmp/proj",
            "message": {"id": f"m{i}", "role": "assistant", "model": "claude-opus-5-5", "content": [],
                        "usage": {"input_tokens": 3, "output_tokens": 5,
                                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}},
        }) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


def test_the_claude_read_counts_its_files_and_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "_CLAUDE_CACHE_PATH", tmp_path / "cache.json")
    size = sum(p.stat().st_size for p in _claude_logs(tmp_path / "logs", 3))
    first, again = [], []
    load_records(tmp_path / "logs", progress=first.append)
    load_records(tmp_path / "logs", progress=again.append)
    assert first[-1] == {"files_done": 3, "files_total": 3, "bytes_done": size, "bytes_total": size}
    assert again[-1] == {"files_done": 3, "files_total": 3, "bytes_done": 0, "bytes_total": 0}, \
        "logs served from the parse cache aren't read"


def test_a_long_first_read_saves_what_it_has_read_as_it_goes(tmp_path, monkeypatch):
    # The cache was written only at the end: closing the app midway through a first
    # read threw the whole read away.
    monkeypatch.setattr(parser, "_CLAUDE_CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setattr(parser, "_CACHE_SAVE_EVERY", 0.0)
    _claude_logs(tmp_path / "logs", 3)
    saves = []
    real_save = parser._claude_save_cache
    monkeypatch.setattr(parser, "_claude_save_cache",
                        lambda cache: (saves.append(len(cache)), real_save(cache)))
    records, *_ = load_records(tmp_path / "logs")
    assert saves == [1, 2, 3]
    assert len(records) == 3


def test_the_codex_read_counts_its_files_and_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_parser, "_CACHE_PATH", tmp_path / "_cache.json")
    monkeypatch.setattr(codex_parser, "_OLD_CACHE_PATH", tmp_path / "_old_cache.json")
    root = tmp_path / "sessions"
    root.mkdir()
    a = root / "rollout-2026-10-29T10-00-00-A.jsonl"
    b = root / "rollout-2026-10-29T11-00-00-B.jsonl"
    write_rollout(a, "019e0000-0000-7000-8000-00000000aaaa", [("T1", 100, 0, 50, "2026-10-29T10:00:00.000Z")])
    write_rollout(b, "019e0000-0000-7000-8000-00000000bbbb", [("T2", 200, 0, 80, "2026-10-29T11:00:00.000Z")])
    size = a.stat().st_size + b.stat().st_size
    first, again, grown = [], [], []
    codex_parser.load_codex_records(root, progress=first.append)
    codex_parser.load_codex_records(root, progress=again.append)
    with a.open("ab") as fh:
        fh.write(b'{"type":"event_msg"}\n')
    codex_parser.load_codex_records(root, progress=grown.append)
    assert first[-1] == {"files_done": 2, "files_total": 2, "bytes_done": size, "bytes_total": size}
    assert again[-1]["bytes_total"] == 0 and again[-1]["files_done"] == 2
    assert grown[-1]["bytes_total"] == 21, "a log that only grew is read from where it was"


# A restart: the parse cache is there, but reading what changed and building the report
# still took 45 s on the maker's PC. The last run's report is kept on disk and shown at
# once (_meta.stale) until the fresh one is in.

def _snapshot_worker(go=None):
    def run(overrides=None, progress_path=None, timeout=1200):
        if go is not None:
            go.wait(10)
        return {"by_session": [{"session_id": "s1"}], "record_count": 7, "_meta": {"source": "claude"}}
    return run


def test_a_restart_shows_the_last_report_at_once_then_the_fresh_one(monkeypatch, tmp_path):
    monkeypatch.setattr(srv, "_FIRST_BUILD_WAIT_S", 5.0)   # the stale report must not wait on it
    snap, cfg = tmp_path / "report-claude.json", {"source": "claude"}
    first = srv._Cache(projects_dir=tmp_path, ttl_seconds=999, worker_config=cfg, snapshot_file=snap)
    first._run_worker = _snapshot_worker()
    first.get_report(lambda *a: None, KEY)
    saved = json.loads(snap.read_text(encoding="utf-8"))
    assert saved["version"] == srv.__version__ and saved["key"] == ["claude", None]

    go = threading.Event()
    restarted = srv._Cache(projects_dir=tmp_path, ttl_seconds=999, worker_config=cfg, snapshot_file=snap)
    restarted._run_worker = _snapshot_worker(go)          # its first build reads what changed
    httpd = _serve(restarted)
    try:
        port = httpd.server_address[1]
        t0 = time.monotonic()
        status, body = _get(port, "/api/report")
        assert status == 200 and time.monotonic() - t0 < 1.0
        assert body["_meta"]["stale"] is True and body["_meta"]["as_of"] == saved["built_at"]
        assert "progress" in body["_meta"] and body["record_count"] == 7
        status, chats = _get(port, "/api/chats")
        assert (chats["stale"], chats["partial"]) == (True, False)
        assert chats["by_session"] == [{"session_id": "s1"}]
        go.set()
        status, body = _poll(port, "/api/report", lambda s, b: not b["_meta"].get("stale"))
        assert status == 200 and "stale" not in body["_meta"]
    finally:
        go.set()
        httpd.shutdown()


def test_another_versions_or_folders_report_is_not_shown(monkeypatch, tmp_path):
    monkeypatch.setattr(srv, "_FIRST_BUILD_WAIT_S", 0.1)
    for i, (version, config) in enumerate((("0.0.1", {"source": "claude"}),
                                           (srv.__version__, {"source": "claude", "projects_dir": "x"}))):
        snap = tmp_path / f"report-{i}.json"
        snap.write_text(json.dumps({"version": version, "key": ["claude", None], "config": config,
                                    "built_at": 1.0, "report": {"record_count": 1, "_meta": {}}}),
                        encoding="utf-8")
        go = threading.Event()
        cache = srv._Cache(projects_dir=tmp_path, ttl_seconds=999, worker_config={"source": "claude"},
                           snapshot_file=snap)
        cache._run_worker = _snapshot_worker(go)
        httpd = _serve(cache)
        try:
            status, body = _get(httpd.server_address[1], "/api/report")
            assert status == 202, (version, config)
        finally:
            go.set()
            httpd.shutdown()


def test_the_report_is_kept_at_most_every_5_minutes(tmp_path):
    snap = tmp_path / "report-claude.json"
    cache = srv._Cache(projects_dir=tmp_path, ttl_seconds=0, worker_config={"source": "claude"},
                       snapshot_file=snap)
    cache._run_worker = _snapshot_worker()
    cache.get_report(lambda *a: None, KEY, force=True)
    kept = snap.read_text(encoding="utf-8")
    cache.get_report(lambda *a: None, KEY, force=True)
    assert snap.read_text(encoding="utf-8") == kept       # not rewritten 0 s later
