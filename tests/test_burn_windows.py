"""The burn-rate gauge's windows reach 12, 24 and 48 hours (5 min to 6 h before 0.3.7)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from burnmeter.analytics import build_report, record_cost_usd
from burnmeter.parser import UsageRecord


def _rec(ts: datetime, tid: str) -> UsageRecord:
    return UsageRecord(
        timestamp=ts, session_id="s", project_dir="/p", project_label="proj",
        git_branch="main", model="claude-opus-4-8", message_id=tid,
        input_tokens=200_000, output_tokens=20_000, cache_creation_tokens=0,
        cache_read_tokens=20_000, tool_names=[], source_file="", uuid=tid,
        parent_uuid="", turn_id=tid, device="mac",
    )


def test_the_gauge_reads_12_24_and_48_hours():
    now = datetime.now(timezone.utc)
    ages = {"a": 1, "b": 10, "c": 20, "d": 30, "e": 47, "f": 60}     # hours ago
    recs = {k: _rec(now - timedelta(hours=h), k) for k, h in ages.items()}
    rep = build_report(list(recs.values()))

    def want(hours):
        return sum(record_cost_usd(r) for k, r in recs.items() if ages[k] < hours) / hours

    byh = rep["forecast"]["burn_rates_by_hours"]
    assert set(byh) == {"0.0833", "0.25", "1", "2", "4", "6", "12", "24", "48"}
    device = rep["by_device"]["mac"]["burn_rates_by_hours"]
    for h in (12, 24, 48):
        assert abs(byh[str(h)] - want(h)) <= 0.01, h
        assert abs(device[str(h)] - want(h)) <= 0.01, h
    assert byh["48"] > 0 and want(48) < want(12)          # the 60 h old record isn't in 48 h
    assert [m["model_id"] for m in rep["forecast"]["burn_rates_by_hours_by_model"]["48"]] == ["claude-opus-4-8"]
