"""Calendar buckets follow the user's local clock.

v0.3.4 cut days at UTC midnight, so a user at UTC+3 saw "today" reset at 03:00
while the activity heatmap, already local, disagreed with it. These tests hold
on any machine timezone: they compare against astimezone() rather than a fixed
offset.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from burnmeter.analytics import aggregate_by_day, forecast, local_date
from burnmeter.parser import UsageRecord


def _rec(ts: datetime, msg: str) -> UsageRecord:
    return UsageRecord(
        timestamp=ts, session_id="s", project_dir="/p", project_label="p", git_branch="",
        model="claude-sonnet-5", message_id=msg, input_tokens=1_000_000, output_tokens=100_000,
        cache_creation_tokens=0, cache_read_tokens=0,
    )


def test_local_date_matches_astimezone_across_slot_boundaries():
    start = datetime(2026, 3, 27, tzinfo=timezone.utc)      # spans the EU DST switch
    for i in range(0, 4 * 24 * 60, 7):                         # every 7 min for 4 days
        ts = start + timedelta(minutes=i, seconds=59)
        assert local_date(ts) == ts.astimezone().date(), ts


def test_days_are_keyed_by_local_date():
    stamps = [datetime(2026, 10, 28, h, 30, tzinfo=timezone.utc) for h in (0, 11, 22, 23)]
    daily = aggregate_by_day([_rec(ts, f"m{i}") for i, ts in enumerate(stamps)])
    assert [d["date"] for d in daily] == sorted({ts.astimezone().date().isoformat() for ts in stamps})


def test_today_so_far_is_the_local_day():
    now = datetime.now(timezone.utc)
    recs = [_rec(now - timedelta(seconds=1), "m1")]
    fc = forecast(recs, aggregate_by_day(recs))
    assert fc["today"]["so_far"] > 0
    assert local_date(recs[0].timestamp) == date.today()


def test_a_stamp_the_os_clock_cannot_convert_falls_back_to_its_utc_date():
    # astimezone() raises OSError on Windows for year 1 / 9999; one such line must
    # not take down the whole report.
    for ts in (datetime(1, 1, 1, tzinfo=timezone.utc), datetime(9999, 12, 31, tzinfo=timezone.utc)):
        try:
            want = ts.astimezone().date()
        except (OverflowError, OSError, ValueError):
            want = ts.date()
        assert local_date(ts) == want
