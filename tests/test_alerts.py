"""Alert threshold logic — incl. the budget-pace branch (v0.1.74): analytics always
computed weekly_cap + forecast.month, but evaluate() never read them."""
from burnmeter.alerts import evaluate, WEEK_OVER_P95_MULT


def test_budget_pace_fires_when_week_beats_p95():
    rep = {
        "weekly_cap": {"rolling_7d_cost": 1300.0, "historical_7d_p95": 1000.0},
        "forecast": {"month": {"projected_eom": 4200.0}},
    }
    level, msg = evaluate(rep, "claude")
    assert level == 2
    assert "1,300" in msg and "1,000" in msg and "4,200" in msg   # week, p95, projection


def test_budget_pace_quiet_below_threshold():
    rep = {"weekly_cap": {"rolling_7d_cost": 1000.0 * WEEK_OVER_P95_MULT - 1, "historical_7d_p95": 1000.0}}
    assert evaluate(rep, "claude") == (0, "")


def test_budget_pace_needs_history():
    # No p95 history (new user) → never fire on pace (no invented baselines).
    rep = {"weekly_cap": {"rolling_7d_cost": 5000.0, "historical_7d_p95": 0}}
    assert evaluate(rep, "claude") == (0, "")


def test_codex_rate_limit_still_wins_over_pace():
    rep = {
        "codex_rate_limits": {"mac": {"primary": {"used_percent": 96}, "secondary": {"used_percent": 10}}},
        "weekly_cap": {"rolling_7d_cost": 1300.0, "historical_7d_p95": 1000.0},
    }
    level, msg = evaluate(rep, "codex")
    assert level == 3 and "96%" in msg          # crit beats the pace warn


def test_codex_pace_fires_when_no_rate_limit_pressure():
    rep = {
        "codex_rate_limits": {"mac": {"primary": {"used_percent": 10}, "secondary": {"used_percent": 5}}},
        "weekly_cap": {"rolling_7d_cost": 1300.0, "historical_7d_p95": 1000.0},
    }
    level, msg = evaluate(rep, "codex")
    assert level == 2 and "Spending pace" in msg
