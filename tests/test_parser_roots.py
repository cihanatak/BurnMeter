"""Where Claude Code's logs are found, how a damaged parse cache is treated, and
how a log line with an unusual timestamp is dated."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from burnmeter import parser
from burnmeter.parser import _parse_timestamp, claude_project_roots, load_records


def _line(uuid, msg_id, out, ts="2026-10-29T10:00:00.000Z", omit_ts=False):
    d = {"type": "assistant", "sessionId": "s1", "uuid": uuid, "cwd": "/tmp/proj",
         "message": {"id": msg_id, "role": "assistant", "model": "claude-opus-5-5", "content": [],
                     "usage": {"input_tokens": 3, "output_tokens": out,
                               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}
    if not omit_ts:
        d["timestamp"] = ts
    return json.dumps(d)


def _write(root: Path, lines, name="s1.jsonl") -> Path:
    p = root / "proj" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: h))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(parser, "_CLAUDE_CACHE_PATH", tmp_path / "cache.json")
    return h


# ---- F6: damaged cache ----

@pytest.mark.parametrize("content", [b"\x81\x8d\xff\xfe garbage \x00\x80", b'["not", "a", "dict"]', b'{"truncated": '])
def test_a_damaged_cache_means_no_cache(tmp_path, monkeypatch, content):
    cache = tmp_path / "cache.json"
    cache.write_bytes(content)
    monkeypatch.setattr(parser, "_CLAUDE_CACHE_PATH", cache)
    assert parser._claude_load_cache() == {}
    _write(tmp_path / "logs", [_line("u1", "m1", out=7)])
    records, *_ = load_records(tmp_path / "logs")
    assert [r.output_tokens for r in records] == [7]
    assert isinstance(json.loads(cache.read_text(encoding="utf-8")), dict)   # healed


# ---- F10: timestamps ----

def test_parse_timestamp_unix_seconds_and_milliseconds():
    want = datetime(2024, 11, 10, 9, 53, 20, tzinfo=timezone.utc)
    assert _parse_timestamp(1731232400) == want
    assert _parse_timestamp(1731232400.0) == want
    assert _parse_timestamp(1731232400000) == want
    assert _parse_timestamp("1731232400") == want
    assert _parse_timestamp("1731232400000") == want


def test_parse_timestamp_keeps_todays_results():
    assert _parse_timestamp("2026-10-29T10:00:00.000Z") == datetime(2026, 10, 29, 10, tzinfo=timezone.utc)
    assert _parse_timestamp("2026-10-29T10:00:00") == datetime(2026, 10, 29, 10, tzinfo=timezone.utc)
    # A compact date: ISO on Python 3.11+, unreadable on 3.10. Never a 1970 day.
    assert _parse_timestamp("20241110") in (datetime(2024, 11, 10, tzinfo=timezone.utc),
                                            datetime.fromtimestamp(0, tz=timezone.utc))
    assert _parse_timestamp(20241110).timestamp() == 0
    for empty in (None, "", "garbage", True, [], 0):
        assert _parse_timestamp(empty).timestamp() == 0


def test_numeric_and_missing_timestamps_keep_their_usage(tmp_path):
    p = _write(tmp_path, [
        _line("u1", "m1", out=11, ts="2026-10-29T10:00:00.000Z"),
        _line("u2", "m2", out=22, ts=1731232400),            # seconds
        _line("u3", "m3", out=33, ts=1731232400000),         # milliseconds
        _line("u4", "m4", out=44, omit_ts=True),             # none: borrows the previous
    ])
    records, stats, *_ = load_records(tmp_path)
    by = {r.message_id: r for r in records}
    assert {k: v.output_tokens for k, v in by.items()} == {"m1": 11, "m2": 22, "m3": 33, "m4": 44}
    assert stats["parse_errors"] == 0
    assert by["m2"].timestamp.year == 2024 and by["m3"].timestamp == by["m2"].timestamp
    assert by["m4"].timestamp == by["m3"].timestamp
    assert all(r.timestamp.year > 2000 for r in records)


def test_a_first_line_without_a_timestamp_gets_the_file_mtime(tmp_path):
    p = _write(tmp_path, [_line("u1", "m1", out=5, omit_ts=True)])
    os.utime(p, (1731232400, 1731232400))
    records, *_ = load_records(tmp_path)
    assert records[0].timestamp == datetime.fromtimestamp(1731232400, tz=timezone.utc)


# ---- F4: roots ----

def test_only_the_xdg_style_root_exists(home):
    xdg = home / ".config" / "claude" / "projects"
    _write(xdg, [_line("u1", "m1", out=9)])
    assert claude_project_roots() == [xdg]
    records, stats, *_ = load_records()
    assert [r.output_tokens for r in records] == [9]
    assert stats["root"] == str(xdg)
    assert not (home / ".claude").exists()          # never created


def test_config_dir_with_two_folders_comes_first(home, monkeypatch):
    a, b = home / "acct-a", home / "acct-b"
    _write(a / "projects", [_line("u1", "m1", out=1)])
    _write(b / "projects", [_line("u2", "m2", out=2)])
    default = home / ".claude" / "projects"
    _write(default, [_line("u3", "m3", out=3)])
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", f"{a}, {b},{home / 'missing'}")
    assert claude_project_roots() == [a / "projects", b / "projects", default]
    records, *_ = load_records()
    assert sorted(r.output_tokens for r in records) == [1, 2, 3]


def test_one_folder_under_two_names_is_read_once(home, monkeypatch):
    real = home / ".claude" / "projects"
    _write(real, [_line("u1", "m1", out=4)])
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude" / ".." / ".claude"))   # same place
    assert len(claude_project_roots()) == 1
    records, *_ = load_records()
    assert len(records) == 1


def test_an_explicit_folder_ignores_the_environment(home, monkeypatch, tmp_path):
    env_dir = home / "acct"
    _write(env_dir / "projects", [_line("u1", "m1", out=1)])
    _write(home / ".claude" / "projects", [_line("u2", "m2", out=2)])
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(env_dir))
    mine = tmp_path / "mine"
    _write(mine, [_line("u3", "m3", out=3)])
    records, stats, *_ = load_records(mine)
    assert [r.output_tokens for r in records] == [3]
    assert stats["root"] == str(mine)


def test_cache_stays_in_claude_dir_when_it_exists_else_beside_first_root(home, monkeypatch):
    (home / ".claude").mkdir()
    assert parser._default_cache_path() == home / ".claude" / ".burnmeter_claude_cache.json"
    (home / ".claude").rmdir()
    _write(home / ".config" / "claude" / "projects", [_line("u1", "m1", out=1)])
    assert parser._default_cache_path() == home / ".config" / "claude" / ".burnmeter_claude_cache.json"
    assert not (home / ".claude").exists()
