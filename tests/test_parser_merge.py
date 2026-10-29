"""How load_records turns streamed log lines into one record per API response,
and how it treats the parse cache it shares with an installed Burnmeter.

v0.3.4 kept the FIRST line of each response. Claude Code writes the usage
snapshot as the response streams, so the full output count is usually only on
the last line, and keeping the first dropped ~32% of all output tokens.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from burnmeter import parser
from burnmeter.parser import load_records


def _line(uuid, msg_id, out, cache_read=0, speed=None, tool=None):
    usage = {"input_tokens": 3, "output_tokens": out,
             "cache_read_input_tokens": cache_read, "cache_creation_input_tokens": 0}
    if speed:
        usage["speed"] = speed
    content = [{"type": "tool_use", "name": tool, "input": {}}] if tool else []
    return json.dumps({
        "type": "assistant", "timestamp": "2026-10-29T10:00:00.000Z", "sessionId": "s1",
        "uuid": uuid, "cwd": "/tmp/proj",
        "message": {"id": msg_id, "role": "assistant", "model": "claude-opus-5-5",
                    "content": content, "usage": usage},
    })


def _write_log(root: Path, name: str, lines: list[str]) -> Path:
    path = root / "proj" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_streamed_lines_fold_into_one_record_at_their_max(tmp_path):
    _write_log(tmp_path, "s1.jsonl", [
        _line("u1", "m1", out=1, cache_read=500, tool="Read"),
        _line("u2", "m1", out=420, cache_read=500, tool="Edit"),
        _line("u3", "m2", out=7),
    ])
    records, _stats, _intents, _errors = load_records(tmp_path)
    by_id = {r.message_id: r for r in records}
    assert len(records) == 2
    assert by_id["m1"].output_tokens == 420
    assert by_id["m1"].cache_read_tokens == 500      # max, not a sum of snapshots
    assert by_id["m1"].tool_names == ["Read", "Edit"]


def test_fast_mode_on_any_line_marks_the_response_fast(tmp_path):
    _write_log(tmp_path, "s1.jsonl", [
        _line("u1", "m1", out=1),
        _line("u2", "m1", out=90, speed="fast"),
    ])
    records, *_ = load_records(tmp_path)
    assert [r.speed for r in records] == ["fast"]


def test_a_line_mirrored_into_a_second_root_counts_once(tmp_path):
    lines = [_line("u1", "m1", out=1), _line("u2", "m1", out=50)]
    _write_log(tmp_path / "main", "s1.jsonl", lines)
    _write_log(tmp_path / "mirror", "s1.jsonl", lines)
    records, *_ = load_records(tmp_path / "main", extra_roots=[tmp_path / "mirror"])
    assert len(records) == 1 and records[0].output_tokens == 50


def test_scanning_one_tree_keeps_another_trees_cache(tmp_path, monkeypatch):
    """A test or a --projects-dir run once replaced the app's whole cache
    (hundreds of MB) with the few files of the tree it scanned."""
    monkeypatch.setattr(parser, "_CLAUDE_CACHE_PATH", tmp_path / "cache.json")
    a = _write_log(tmp_path / "a", "s1.jsonl", [_line("u1", "m1", out=5)])
    b = _write_log(tmp_path / "b", "s2.jsonl", [_line("u2", "m2", out=6)])

    load_records(tmp_path / "a")
    load_records(tmp_path / "b")
    cache = json.loads((tmp_path / "cache.json").read_text())
    assert {str(a), str(b)} <= set(cache)

    os.remove(a)                                # a gone → its entry may go too
    load_records(tmp_path / "b")
    cache = json.loads((tmp_path / "cache.json").read_text())
    assert str(a) not in cache and str(b) in cache


def test_an_unchanged_tree_does_not_rewrite_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "_CLAUDE_CACHE_PATH", tmp_path / "cache.json")
    _write_log(tmp_path / "logs", "s1.jsonl", [_line("u1", "m1", out=5)])
    saves = []
    real_save = parser._claude_save_cache
    monkeypatch.setattr(parser, "_claude_save_cache",
                        lambda cache: (saves.append(len(cache)), real_save(cache)))

    load_records(tmp_path / "logs")
    load_records(tmp_path / "logs")
    assert saves == [1]


def test_builds_from_the_cache_keep_tool_counts_and_the_cache_unchanged(tmp_path):
    # Merging a response's later line used to extend the CACHED record's list: every
    # build added the tools again (1500 "Read" calls became 7500 after 8 builds) and
    # the grown entry was saved back whenever another log had changed.
    lines = []
    for i in range(3):
        lines += [_line(f"a{i}", f"m{i}", out=1, tool="Read"), _line(f"b{i}", f"m{i}", out=9, tool="Edit")]
    _write_log(tmp_path, "s1.jsonl", lines)
    live = _write_log(tmp_path, "live.jsonl", [_line("l0", "x0", out=1)])
    for n in range(4):
        live.write_text(live.read_text() + _line(f"l{n + 1}", f"x{n + 1}", out=1) + "\n")
        records, *_ = load_records(tmp_path)
        tools = [t for r in records for t in r.tool_names]
        assert tools.count("Read") == 3 and tools.count("Edit") == 3, (n, tools)
    entry = parser._claude_load_cache()[str(tmp_path / "proj" / "s1.jsonl")]
    assert sum(len(d["tool_names"]) for d in entry["recs"]) == 6


def test_one_tree_spelled_two_ways_is_cached_once(tmp_path, monkeypatch):
    _write_log(tmp_path, "s1.jsonl", [_line("u1", "m1", out=1)])
    load_records(tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    load_records(Path(tmp_path.name))                 # the same tree, relative this time
    mine = os.path.normcase(str(tmp_path / "proj" / "s1.jsonl"))
    keys = [k for k in parser._claude_load_cache() if os.path.normcase(os.path.abspath(k)) == mine]
    assert len(keys) == 1, keys


def _user(text, **extra):
    return json.dumps({"type": "user", "timestamp": "2026-10-29T09:00:00.000Z", "sessionId": "s1",
                       "uuid": extra.pop("uuid", "h1"), "cwd": "/tmp/proj",
                       "message": {"role": "user", "content": extra.pop("content", text)}, **extra})


def test_the_goal_is_what_the_user_typed(tmp_path):
    # Claude Code logs role-"user" lines that aren't the user: undated request copies,
    # image notes, compaction summaries, tool results with "Tool loaded." appended.
    blob = json.dumps({"type": "api-request-blob", "sessionId": "s1", "hash": "x",
                       "message": {"role": "user", "content": [{"type": "text", "text": "Tool loaded."}]}})
    _write_log(tmp_path, "s1.jsonl", [
        blob,
        _user("[Image: original 1430x3442]", isMeta=True, uuid="h0"),
        _user("This session is being continued from a previous conversation", isCompactSummary=True, uuid="h2"),
        _user("", uuid="h3", content=[{"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
                                      {"type": "text", "text": "Tool loaded."}]),
        _user("[Request interrupted by user]", uuid="h3b"),
        _user("review burnmeter in detail", uuid="h4"),
        _line("u1", "m1", out=5),
    ])
    _records, _stats, intents, _errors = load_records(tmp_path)
    assert intents["s1"] == "review burnmeter in detail"


def _tool_error(uuid, tool_use_id, kind="user"):
    return json.dumps({"type": kind, "timestamp": "2026-10-29T09:30:00.000Z", "sessionId": "s1",
                       "uuid": uuid, "message": {"role": "user", "content": [
                           {"type": "tool_result", "tool_use_id": tool_use_id, "is_error": True,
                            "content": "Exit code 1"}]}})


def test_a_tool_error_counts_once_however_often_it_is_logged(tmp_path):
    _write_log(tmp_path, "s1.jsonl", [
        _tool_error("e1", "t1"),
        _tool_error("e1b", "t1", kind="api-request-blob"),   # the request copy
        _tool_error("e2", "t2"),
    ])
    _write_log(tmp_path, "fork.jsonl", [_tool_error("e1", "t1")])   # a fork inherits it
    _records, _stats, _intents, errors = load_records(tmp_path)
    assert sorted(e["tool_use_id"] for e in errors) == ["t1", "t2"]
