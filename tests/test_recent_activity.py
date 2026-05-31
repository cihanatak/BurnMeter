"""recent_turns DATA-QUALITY regression test (bug found 2026-05-31).

The "Son aktivite" card showed 2 rows with a big empty gap below it. Cause: the top-30
recent_turns cut counted synthetic/placeholder records (model "<synthetic>", 0 tokens —
traces of HS scheduler events logging usage:0) BEFORE the frontend hid them, so real turns
got crowded out of the top-30. Fix: drop synthetic rows before the top-30 cut.

Run:  pytest tests/test_recent_activity.py -q
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from burnmeter.analytics import build_report
from burnmeter.parser import UsageRecord


def _rec(model: str, ts: datetime, tokens: int, tid: str, sid: str = "sess") -> UsageRecord:
    return UsageRecord(
        timestamp=ts, session_id=sid, project_dir="/p", project_label="proj",
        git_branch="main", model=model, message_id=tid,
        input_tokens=tokens, output_tokens=max(1, tokens // 10),
        cache_creation_tokens=0, cache_read_tokens=tokens * 2,
        tool_names=[], source_file="", uuid=tid, parent_uuid="",
        turn_id=tid, device="mac",
    )


def _dataset():
    now = datetime.now(timezone.utc)
    recs = []
    # real turns spread over 3 days (gives build_report real daily buckets)
    for d in range(3):
        for i in range(5):
            ts = now - timedelta(days=d, minutes=10 * i + 1)
            recs.append(_rec("claude-opus-4-8", ts, 5_000, tid=f"real-{d}-{i}"))
    # 5 SYNTHETIC 0-token placeholders with the MOST RECENT timestamps — would otherwise
    # occupy the top of the recency-sorted top-30 and crowd out the real turns.
    for i in range(5):
        recs.append(_rec("<synthetic>", now - timedelta(seconds=i), 0, tid=f"syn-{i}"))
    return recs


def test_recent_turns_excludes_synthetic():
    rt = build_report(_dataset())["recent_turns"]
    assert rt, "recent_turns is empty"
    leaked = [t["model"] for t in rt if str(t["model"]).startswith("<")]
    assert not leaked, f"synthetic turns leaked into recent_turns: {leaked}"


def test_recent_turns_shows_real_activity():
    rt = build_report(_dataset())["recent_turns"]
    assert len(rt) >= 5, f"real activity crowded out — only {len(rt)} rows"
    assert all(t.get("total_tokens", 0) > 0 for t in rt), "zero-token row in recent_turns"
