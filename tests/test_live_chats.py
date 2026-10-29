"""The live card has a row per chat.

Rows were per model: four chats on Opus showed as one row, named after whichever chat
wrote last and carrying the cost of all four.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from burnmeter.analytics import live_active_models
from burnmeter.parser import UsageRecord
from burnmeter.pricing import record_cost_usd

NOW = datetime.now(timezone.utc)


def _rec(ago_s, sid, model="claude-opus-5-5", project="repo", out=500, device="pc"):
    t = NOW - timedelta(seconds=ago_s)
    return UsageRecord(
        timestamp=t, session_id=sid, project_dir="F:/" + project, project_label=project,
        git_branch="main", model=model, message_id=f"{sid}-{ago_s}-{model}",
        input_tokens=1000, output_tokens=out, cache_creation_tokens=0, cache_read_tokens=20000,
        device=device,
    )


def test_two_chats_on_one_model_are_two_rows_with_their_own_cost():
    recs = [_rec(30, "a", project="LR"), _rec(90, "a", project="LR"),
            _rec(10, "b", project="repo", out=4000)]
    rows = live_active_models(recs, lookback_min=5)["models"]
    assert [(r["session_id"], r["project"], r["messages"]) for r in rows] == [("b", "repo", 1), ("a", "LR", 2)]
    assert all(r["model_id"] == "claude-opus-5-5" for r in rows)
    hours = 5 / 60
    want = {"a": sum(record_cost_usd(r) for r in recs[:2]) / hours, "b": record_cost_usd(recs[2]) / hours}
    for r in rows:
        assert abs(r["cost_per_hour"] - want[r["session_id"]]) < 0.01


def test_a_subagent_on_another_model_does_not_rename_the_chat():
    recs = [_rec(20, "a"), _rec(5, "a", model="claude-haiku-5-5", out=50)]   # the newest is Haiku
    rows = live_active_models(recs, lookback_min=5)["models"]
    assert len(rows) == 1 and rows[0]["model_id"] == "claude-opus-5-5" and rows[0]["messages"] == 2


def test_a_record_without_a_chat_gets_a_row_per_model():
    rows = live_active_models([_rec(5, ""), _rec(6, "", model="claude-haiku-5-5")], lookback_min=5)["models"]
    assert sorted(r["model_id"] for r in rows) == ["claude-haiku-5-5", "claude-opus-5-5"]
    assert all(r["session_id"] is None for r in rows)


def test_the_same_chat_on_two_devices_stays_two_rows_and_old_turns_drop():
    recs = [_rec(5, "a", device="pc"), _rec(8, "a", device="mac"), _rec(400, "c")]
    rows = live_active_models(recs, lookback_min=5)["models"]
    assert [(r["session_id"], r["device"]) for r in rows] == [("a", "pc"), ("a", "mac")]
