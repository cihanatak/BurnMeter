"""Snapshot summary carries a compact, bounded top-projects list (P3 cross-device
Projects rollup). Only aggregates leave the machine — never raw logs."""
import burnmeter.sync as sync


def _report_with_projects(n):
    return {
        "record_count": 100,
        "forecast": {"burn_rate_per_hour_recent": 1.0, "today": {"so_far": 2.0}, "month": {"so_far": 3.0}},
        "totals": {"cost_usd": 9.0, "total_tokens": 1000, "cache_hit_rate": 0.5},
        "by_project": [
            {"project_dir": f"/p/{i}", "project_label": f"proj{i}",
             "cost_usd": float(n - i), "total_tokens": (n - i) * 10, "messages": (n - i)}
            for i in range(n)
        ],
    }


def test_summary_includes_by_project_compact():
    s = sync._summarize(_report_with_projects(3))
    assert "by_project" in s
    p = s["by_project"][0]
    assert set(p.keys()) == {"project", "cost", "tokens", "msgs"}     # compact fields only
    assert p["project"] == "proj0" and p["cost"] == 3.0              # preserves order + label


def test_summary_caps_projects():
    s = sync._summarize(_report_with_projects(50))
    assert len(s["by_project"]) == sync.SNAPSHOT_PROJECTS_MAX        # bounded (no full list leaks)


def test_summary_no_projects_is_empty_list():
    r = {"forecast": {}, "totals": {}}
    s = sync._summarize(r)
    assert s["by_project"] == []
