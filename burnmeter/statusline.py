"""Compact one-line status for terminal / Claude Code statusLine integration.

Single source of truth shared by:
  - the HTTP endpoint  GET /api/statusline   (server.py)
  - the CLI subcommand `burnmeter statusline`   (cli.py)

build_statusline() reduces a build_report() dict to a few fields and
format_statusline() renders them. The CLI asks a running Burnmeter for the
fields (its report is already built) and renders them itself, so the line
reads the same whichever side computed it.
"""
from __future__ import annotations

from datetime import date

from .pricing import display_name


def _active_model(report: dict) -> str:
    """Display name of the model the most recent session used last ('Opus 5.5').

    Ids outside the Claude families (Codex, gpt, o-series) are shown as they are,
    so the line still says something true rather than going blank.
    """
    sessions = report.get("by_session") or []
    if not sessions:
        return "—"
    # model_latest, not models[-1]: `models` is sorted by name, not by time.
    raw = sessions[0].get("model_latest") or (sessions[0].get("models") or [""])[-1]
    if not raw or str(raw).startswith("<"):   # skip synthetic/placeholder model ids
        return "—"
    return display_name(str(raw))


def build_statusline(report: dict) -> dict:
    """Structured statusline fields from a build_report() dict (source-agnostic)."""
    cw = report.get("current_window") or {}
    err = report.get("errors") or {}
    fc = report.get("forecast") or {}
    totals = report.get("totals") or {}

    burn = fc.get("burn_rate_per_hour_recent", cw.get("cost_per_hour", 0)) or 0
    zones = report.get("burn_rate_zones") or {}
    if burn >= zones.get("heavy", 25):
        dot = "🔴"
    elif burn >= zones.get("busy", 15):
        dot = "🟠"
    elif burn >= zones.get("typical", 5):
        dot = "🟡"
    else:
        dot = "🟢"

    rem_min = max(0, (cw.get("remaining_seconds", 0) or 0) // 60)
    # Today's hit rate says whether caching works right now; a day without
    # activity falls back to the all-time rate.
    today_iso = date.today().isoformat()
    day = next((d for d in reversed(report.get("daily") or []) if d.get("date") == today_iso), None)
    hit_rate = (day or totals).get("cache_hit_rate", 0) or 0

    return {
        "verdict": dot,
        "active_model": _active_model(report),
        "burn_rate_per_hour": round(burn, 2),
        "block_remaining_min": int(rem_min),
        "block_past_p90": bool(cw.get("block_already_past_p90")),
        "errors_24h": err.get("total", 0),
        "cache_hit_pct": round(hit_rate * 100, 1),
        "today_cost": round((fc.get("today", {}) or {}).get("so_far", 0) or 0, 2),
    }


def _money(v: float) -> str:
    return f"${v:.2f}" if v < 10 else f"${v:,.0f}"


def format_statusline(s: dict) -> str:
    """Single plain-text line from build_statusline() fields."""
    rem = s.get("block_remaining_min")
    if rem is None:                       # not knowable from the logs that were read
        block = "—"
    else:
        rem = int(rem)
        block = f"{rem // 60}h{rem % 60:02d}m" if rem >= 60 else f"{rem}m"
    p90 = " ⚠ P90+" if s.get("block_past_p90") else ""
    return (
        f"{s.get('verdict', '')} {s.get('active_model', '—')} "
        f"{_money(s.get('burn_rate_per_hour') or 0)}/h · block {block}{p90} · "
        f"{s.get('errors_24h', 0)} err/24h · cache {s.get('cache_hit_pct') or 0:.0f}% · "
        f"today {_money(s.get('today_cost') or 0)}"
    )


def statusline_text(report: dict) -> str:
    """Single plain-text line, ready to print in a prompt / status bar."""
    return format_statusline(build_statusline(report))
