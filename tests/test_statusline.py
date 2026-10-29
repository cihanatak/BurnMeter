"""The statusline: one English line, fast enough for Claude Code's statusLine.

v0.3.4 rebuilt the whole report on every call (65 s on the maker's history),
printed raw ids for sonnet-5 / fable-5-1, picked the alphabetically last model
of the session instead of the latest one, and spoke Turkish.
"""
from __future__ import annotations

import argparse
import json
import threading
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from burnmeter import cli
from burnmeter.parser import UsageRecord
from burnmeter.statusline import build_statusline, format_statusline

_FIELDS = {"verdict": "🟠", "active_model": "Opus 5.5", "burn_rate_per_hour": 4.2,
           "block_remaining_min": 264, "block_past_p90": False, "errors_24h": 0,
           "cache_hit_pct": 97.4, "today_cost": 32.1}


def test_the_line_reads_in_english():
    assert format_statusline(_FIELDS) == (
        "🟠 Opus 5.5 $4.20/h · block 4h24m · 0 err/24h · cache 97% · today $32")
    assert "⚠ P90+" in format_statusline({**_FIELDS, "block_past_p90": True})
    assert "block 7m" in format_statusline({**_FIELDS, "block_remaining_min": 7})
    assert "block — ·" in format_statusline({**_FIELDS, "block_remaining_min": None})


def test_the_active_model_is_the_latest_one_used():
    report = {"by_session": [{"models": ["claude-opus-5-5", "claude-sonnet-5"],
                              "model_latest": "claude-opus-5-5"}]}
    assert build_statusline(report)["active_model"] == "Opus 5.5"
    report["by_session"][0]["model_latest"] = "claude-fable-5-1"
    assert build_statusline(report)["active_model"] == "Fable 5.1"


def test_cache_rate_is_todays_when_there_is_activity_today():
    report = {"totals": {"cache_hit_rate": 0.5},
              "daily": [{"date": date.today().isoformat(), "cache_hit_rate": 0.9}]}
    assert build_statusline(report)["cache_hit_pct"] == 90.0
    report["daily"] = []
    assert build_statusline(report)["cache_hit_pct"] == 50.0


def _args(projects_dir: Path) -> argparse.Namespace:
    return argparse.Namespace(source="claude", json=False, codex_dir=None,
                              projects_dir=str(projects_dir), plan=None)


def _serve(projects_dir: Path, fields: dict):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/api/health"):
                body = {"ok": True, "token": "tok", "projects_dir": str(projects_dir)}
            elif self.path.startswith("/api/statusline"):
                body = fields
            else:
                self.send_response(404); self.end_headers(); return
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_a_running_burnmeter_answers_without_reading_any_log(tmp_path, monkeypatch, capsys):
    projects = tmp_path / "projects"
    projects.mkdir()
    srv = _serve(projects, _FIELDS)
    try:
        (tmp_path / ".burnmeter").mkdir()
        (tmp_path / ".burnmeter" / "server.json").write_text(json.dumps(
            {"pid": 1, "host": "127.0.0.1", "port": srv.server_address[1], "token": "tok"}))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(cli, "CLAUDE_PROJECTS_DIR", projects)

        def _no_logs(*a, **k):
            raise AssertionError("must not parse logs while a server is running")
        monkeypatch.setattr(cli, "load_records", _no_logs)
        assert cli.cmd_statusline(_args(projects)) == 0
    finally:
        srv.shutdown()
        srv.server_close()
    assert capsys.readouterr().out.strip() == format_statusline(_FIELDS)


def test_a_server_reading_other_logs_is_not_trusted(tmp_path, monkeypatch, capsys):
    projects = tmp_path / "projects"
    projects.mkdir()
    srv = _serve(tmp_path / "somewhere-else", _FIELDS)
    try:
        (tmp_path / ".burnmeter").mkdir()
        (tmp_path / ".burnmeter" / "server.json").write_text(json.dumps(
            {"pid": 1, "host": "127.0.0.1", "port": srv.server_address[1], "token": "tok"}))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(cli, "CLAUDE_PROJECTS_DIR", projects)
        assert cli.cmd_statusline(_args(projects)) == 0
    finally:
        srv.shutdown()
        srv.server_close()
    assert capsys.readouterr().out.strip() == "○ burnmeter: no claude usage in the last 26 h"


def test_without_a_server_it_reads_only_recent_logs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)          # no pidfile
    projects = tmp_path / "projects"
    seen = {}

    def _load(root, since=None, **k):
        seen["since"] = since
        return [], {}, {}, []
    monkeypatch.setattr(cli, "load_records", _load)
    assert cli.cmd_statusline(_args(projects)) == 0
    assert capsys.readouterr().out.strip() == "○ burnmeter: no claude usage in the last 26 h"
    age = datetime.now(timezone.utc) - seen["since"]
    assert timedelta(hours=25) < age < timedelta(hours=27)


def _standalone_block(tmp_path, monkeypatch, capsys, hours_ago: list[float]):
    """block_remaining_min the standalone path prints for records this many hours old."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)          # no pidfile
    now = datetime.now(timezone.utc)
    recs = [UsageRecord(timestamp=now - timedelta(hours=h), session_id="s", project_dir="/p",
                        project_label="p", git_branch="", model="claude-opus-5-5",
                        message_id=f"m{i}", input_tokens=100, output_tokens=100,
                        cache_creation_tokens=0, cache_read_tokens=0)
            for i, h in enumerate(sorted(hours_ago, reverse=True))]
    monkeypatch.setattr(cli, "load_records", lambda root, since=None, **k: (recs, {}, {}, []))
    args = _args(tmp_path / "projects")
    args.json = True
    assert cli.cmd_statusline(args) == 0
    fields = json.loads(capsys.readouterr().out)
    # The pace dot compares against weeks of the user's blocks: not known from 26 h.
    assert fields["verdict"] == "⚪" and fields["block_past_p90"] is False
    return fields["block_remaining_min"]


def test_the_block_is_unknown_when_the_day_read_has_no_5_hour_pause(tmp_path, monkeypatch, capsys):
    # Busy since before the 26 hours read: the block chain starts earlier, so the
    # current block can't be told from these logs. v0.3.4's server said 113 min
    # where a 26-hour read said 55.
    assert _standalone_block(tmp_path, monkeypatch, capsys, [25.5, 21.5, 17.5, 13.5, 9.5, 5.5, 1.5]) is None


def test_the_block_is_known_after_a_5_hour_pause(tmp_path, monkeypatch, capsys):
    # A pause opens the next block at its first message: 2 h ago, so 3 h remain.
    assert 175 <= _standalone_block(tmp_path, monkeypatch, capsys, [25.5, 21.5, 2.0, 1.0]) <= 180
    # Nothing for 5 hours: no block is open.
    assert _standalone_block(tmp_path, monkeypatch, capsys, [25.5, 21.5, 6.0]) == 0
