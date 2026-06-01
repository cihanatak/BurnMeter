"""Codex parser DATA-CORRECTNESS regression tests.

Locks the data bugs found 2026-05-31 (see basic-memory note "Codex replay dedup"):
  - replay/fork over-count: one logical session split across N rollout files that each
    REPLAY the same turns must count each event ONCE, not once-per-file (was 36.8x → burn
    rate ~700x). dedup key = (turn_id, fresh_input, output, cache_read).
  - earliest-timestamp wins so burn-rate time-windows stay correct (replays re-stamp old
    usage with resume-time; we must keep the original burn time).
  - per-response delta from last_token_usage, NOT cumulative total_token_usage.
  - consecutive duplicate streaming events collapse.

A metrics product lives or dies on the numbers being right; these are the guard rails.

Run:  pytest tests/test_codex_correctness.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import burnmeter.codex_parser as cp
from burnmeter.codex_parser import load_codex_records


def write_rollout(path: Path, sid: str, turns, cwd: str = "/proj", model: str = "gpt-5.5") -> None:
    """Write a minimal-but-real Codex rollout-*.jsonl.

    turns: list of (turn_id, input_tokens, cached_input_tokens, output_tokens, ts_iso).
    Emits session_meta, then for each turn a turn_context (when the turn_id changes) and a
    token_count event_msg carrying last_token_usage (the per-response delta). total_token_usage
    is deliberately set far larger to prove the parser reads the DELTA, not the cumulative.
    """
    lines = [{"timestamp": turns[0][4], "type": "session_meta",
              "payload": {"id": sid, "cwd": cwd, "git": {"branch": "main"}}}]
    last_tid = None
    for tid, intok, cached, out, ts in turns:
        if tid != last_tid:
            lines.append({"timestamp": ts, "type": "turn_context",
                          "payload": {"turn_id": tid, "model": model, "cwd": cwd}})
            last_tid = tid
        lines.append({"timestamp": ts, "type": "event_msg",
                      "payload": {"type": "token_count", "rate_limits": None,
                                  "info": {
                                      "last_token_usage": {"input_tokens": intok,
                                                           "cached_input_tokens": cached,
                                                           "output_tokens": out},
                                      "total_token_usage": {"input_tokens": intok + 99_999,
                                                            "cached_input_tokens": cached,
                                                            "output_tokens": out + 99_999}}}})
    path.write_text("\n".join(json.dumps(o) for o in lines) + "\n")


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Never read/write the real ~/.codex/.burnmeter_codex_cache.json during tests
    (patch BOTH the current path AND the pre-rename path the migration shim looks at)."""
    monkeypatch.setattr(cp, "_CACHE_PATH", tmp_path / "_cache.json")
    monkeypatch.setattr(cp, "_OLD_CACHE_PATH", tmp_path / "_old_cache.json")


def test_replay_dedup_across_rollout_files(tmp_path):
    """Session resumed/forked into 2 rollout files (same session_id) that REPLAY the same
    turns → each logical event counted ONCE, not once per file (the 36.8x inflation bug)."""
    sid = "019e0000-0000-7000-8000-00000000aaaa"
    write_rollout(tmp_path / "rollout-2026-05-01T10-00-00-fileA.jsonl", sid, [
        ("T1", 100, 0, 50, "2026-05-01T10:00:00.000Z"),
        ("T2", 200, 50, 80, "2026-05-01T10:01:00.000Z"),
    ])
    write_rollout(tmp_path / "rollout-2026-05-01T11-00-00-fileB.jsonl", sid, [
        ("T1", 100, 0, 50, "2026-05-01T11:00:00.000Z"),    # replay
        ("T2", 200, 50, 80, "2026-05-01T11:00:01.000Z"),   # replay
        ("T3", 300, 100, 90, "2026-05-01T11:05:00.000Z"),  # genuinely new
    ])
    records = load_codex_records(tmp_path)[0]
    # Naive (buggy) = 5 (2 + 3). Deduped by (turn_id, fresh_in, out, cache_read) = 3 unique.
    assert len(records) == 3, f"replay over-count: {len(records)} records (expected 3 unique)"
    assert {r.turn_id for r in records} == {"T1", "T2", "T3"}


def test_replay_dedup_keeps_earliest_timestamp(tmp_path):
    """Deduped event keeps the EARLIEST timestamp (original burn time) so a replay 10h later
    does not make old usage look 'recent' and blow up the burn-rate window."""
    sid = "019e0000-0000-7000-8000-00000000bbbb"
    write_rollout(tmp_path / "rollout-2026-05-01T10-00-00-A.jsonl", sid,
                  [("T1", 100, 0, 50, "2026-05-01T10:00:00.000Z")])
    write_rollout(tmp_path / "rollout-2026-05-01T20-00-00-B.jsonl", sid,
                  [("T1", 100, 0, 50, "2026-05-01T20:00:00.000Z")])  # replay, 10h later
    records = load_codex_records(tmp_path)[0]
    assert len(records) == 1
    assert records[0].timestamp.isoformat().startswith("2026-05-01T10:00"), \
        f"kept replay ts instead of earliest: {records[0].timestamp}"


def test_uses_last_token_usage_delta_not_cumulative(tmp_path):
    """Record reflects the per-response DELTA (last_token_usage), not cumulative total."""
    sid = "019e0000-0000-7000-8000-00000000cccc"
    write_rollout(tmp_path / "rollout-2026-05-01T10-00-00-D.jsonl", sid,
                  [("T1", 100, 20, 50, "2026-05-01T10:00:00.000Z")])
    r = load_codex_records(tmp_path)[0][0]
    assert r.input_tokens == 80, f"fresh input wrong: {r.input_tokens} (100-20 expected)"
    assert r.cache_read_tokens == 20
    assert r.output_tokens == 50
    # cumulative would be ~100k; assert we did NOT read total_token_usage
    assert r.input_tokens < 1_000 and r.output_tokens < 1_000


def test_consecutive_duplicate_events_collapse(tmp_path):
    """Codex can stream the identical last_token_usage twice in a row → count once."""
    sid = "019e0000-0000-7000-8000-00000000dddd"
    write_rollout(tmp_path / "rollout-2026-05-01T10-00-00-E.jsonl", sid, [
        ("T1", 100, 0, 50, "2026-05-01T10:00:00.000Z"),
        ("T1", 100, 0, 50, "2026-05-01T10:00:00.500Z"),   # exact duplicate
        ("T1", 120, 0, 60, "2026-05-01T10:00:01.000Z"),   # distinct
    ])
    records = load_codex_records(tmp_path)[0]
    assert len(records) == 2, f"duplicate not collapsed: {len(records)} (expected 2)"


def test_distinct_sessions_not_deduped(tmp_path):
    """Guard against over-aggressive dedup: two DIFFERENT sessions with identical token
    shapes but different turn_ids must both count (dedup key includes turn_id)."""
    write_rollout(tmp_path / "rollout-2026-05-01T10-00-00-S1.jsonl",
                  "019e0000-0000-7000-8000-000000001111",
                  [("A1", 100, 0, 50, "2026-05-01T10:00:00.000Z")])
    write_rollout(tmp_path / "rollout-2026-05-01T10-00-00-S2.jsonl",
                  "019e0000-0000-7000-8000-000000002222",
                  [("B1", 100, 0, 50, "2026-05-01T10:00:00.000Z")])
    records = load_codex_records(tmp_path)[0]
    assert len(records) == 2, f"distinct sessions wrongly merged: {len(records)}"
