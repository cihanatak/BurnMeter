"""The gauge compares like with like, names no guessed plan, and two time bugs (0.3.9).

Synthetic users only; nothing reads real logs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import count

from burnmeter.analytics import build_report, record_cost_usd
from burnmeter.parser import UsageRecord

MODEL = "claude-sonnet-4-5"
_ids = count()


def _rec(ts: datetime, *, inp=0, out=0, cache_read=0) -> UsageRecord:
    tid = f"t{next(_ids)}"
    return UsageRecord(
        timestamp=ts, session_id="s", project_dir="/p", project_label="proj",
        git_branch="main", model=MODEL, message_id=tid,
        input_tokens=inp, output_tokens=out, cache_creation_tokens=0,
        cache_read_tokens=cache_read, tool_names=[], source_file="", uuid=tid,
        parent_uuid="", turn_id=tid, device="pc",
    )


def _usd(ts: datetime, usd: float) -> UsageRecord:
    """A record costing `usd` (input tokens scaled from the model's own price)."""
    unit = record_cost_usd(_rec(ts, inp=1_000_000))
    return _rec(ts, inp=int(round(usd / unit * 1_000_000)))


NOW = lambda: datetime.now(timezone.utc)  # noqa: E731
STARTING = {"typical": 8.0, "busy": 18.0, "heavy": 35.0, "max": 60.0}


def _strings(obj):
    """Every string VALUE in the report (keys such as "max" are zone names, not plans)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def _zones(rep, key):
    return rep["burn_rate_zones_by_hours"][key]


def test_light_user_is_not_put_on_a_plan():
    now = NOW()
    recs = []
    for day in range(10):
        start = now - timedelta(days=day, hours=1)
        for p in range(3):
            recs.append(_rec(start + timedelta(minutes=10 * p), inp=2_000, out=1_000, cache_read=60_000))
    rep = build_report(recs, git=False)
    assert rep["chosen_plan"] == "unknown"
    assert rep["plan_inference"]["guess"] == "unknown"
    assert rep["plan_limits"] == {}
    cw = rep["current_window"]
    assert cw["active"] and cw["plan"] == "unknown"
    assert cw["plan_limit"] is None and cw["utilization"] is None
    assert rep["market_zones"]["plan"] == "unknown"
    values = list(_strings(rep))
    for name in ("pro", "max", "ultra", "max5", "max20"):
        assert name not in values, name
    assert not [v for v in values if "Max 5" in v or "Max 20" in v]

    named = build_report(recs, plan="max20", git=False)      # the old spelling still works
    assert named["chosen_plan"] == "ultra" and named["current_window"]["plan"] == "ultra"
    assert named["current_window"]["plan_limit"] is None


def test_bursty_user_reads_typical_while_working():
    # 20 days, each $4 spent within 30 minutes (a real $8/h working pace).
    now = NOW()
    recs = []
    for day in range(1, 21):
        start = now - timedelta(days=day, hours=2)
        recs += [_usd(start + timedelta(minutes=5 * i), 4 / 6) for i in range(6)]
    # Today: the same burst, ending now.
    recs += [_usd(now - timedelta(minutes=25.5 - 5 * i), 4 / 6) for i in range(6)]
    rep = build_report(recs, git=False)
    byh = rep["forecast"]["burn_rates_by_hours"]
    assert set(rep["burn_rate_zones_by_hours"]) == set(byh)
    assert rep["burn_rate_zones"] == _zones(rep, "2")
    for key in ("0.0833", "1"):
        z = _zones(rep, key)
        assert not z["insufficient_history"] and z["blend_ratio"] == 1.0, z
        reading = byh[key]
        assert 0.5 * z["typical"] <= reading <= 2 * z["typical"], (key, reading, z)
        assert reading < z["heavy"], (key, reading, z)


def test_steady_user_typical_is_their_hourly_pace():
    # 14 days, 6 hours a day at $10/h (a record every 5 minutes).
    now = NOW()
    recs = []
    for day in range(1, 15):
        start = now - timedelta(days=day, hours=8)
        recs += [_usd(start + timedelta(minutes=5 * i), 10 / 12) for i in range(72)]
    rep = build_report(recs, git=False)
    z = _zones(rep, "1")
    assert abs(z["typical"] - 10) <= 2, z
    assert z["samples"] >= 96


def test_new_user_gets_the_starting_scale():
    now = NOW()
    recs = [_usd(now - timedelta(minutes=5 * i), 0.5) for i in range(30)]
    rep = build_report(recs, git=False)
    for key, z in rep["burn_rate_zones_by_hours"].items():
        assert z["insufficient_history"] is True, key
        assert {k: z[k] for k in STARTING} == STARTING
    assert rep["burn_rate_zones"]["insufficient_history"] is True
    assert {k: rep["market_zones"][k] for k in STARTING} == STARTING


def test_weekly_pace_uses_calendar_weeks():
    # $100 on one day every week for 9 weeks; today is one of those days.
    now = NOW()
    recs = [_usd(now - timedelta(days=7 * w, minutes=1), 100.0) for w in range(9)]
    rep = build_report(recs, git=False)
    wk = rep["weekly_cap"]
    assert abs(wk["rolling_7d_cost"] - 100) < 0.5, wk
    assert abs(wk["historical_7d_median"] - 100) < 0.5, wk
    assert wk["verdict_label"] == "a typical week", wk
    assert rep["anomalies"] == []       # one day a week is not a spike


def test_future_record_does_not_take_the_current_block():
    now = NOW()
    recs = [_usd(now - timedelta(minutes=30), 1.0), _usd(now - timedelta(minutes=10), 1.0),
            _usd(now + timedelta(days=30), 5.0)]
    rep = build_report(recs, git=False)
    cw = rep["current_window"]
    assert cw["active"] is True
    started = datetime.fromisoformat(cw["started_at"])
    assert now - timedelta(minutes=31) <= started <= now, cw["started_at"]
    assert abs(cw["cost_usd"] - 2.0) < 0.05, cw
    assert rep["future_records_dropped"] == 1
    assert rep["record_count"] == 2
