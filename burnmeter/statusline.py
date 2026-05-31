"""Compact one-line status for terminal / Claude Code statusLine integration.

Single source of truth shared by:
  - the HTTP endpoint  GET /api/statusline   (server.py)
  - the CLI subcommand `burnmeter statusline`   (cli.py)

Both feed it the SAME `build_report(...)` dict, so the live web dashboard, the
HTTP pull, and the terminal line can never drift apart.
"""
from __future__ import annotations

import re

_MODEL_RE = re.compile(r"(opus|sonnet|haiku)-(\d+)-(\d+)")


def _active_model(report: dict) -> str:
    """Pretty name of the model used by the most recent session (e.g. 'Opus 4.8').

    Falls back to the raw model id for non-Claude families (Codex/gpt/o-series)
    so the line still shows *something* truthful rather than a blank.
    """
    sessions = report.get("by_session") or []
    if not (sessions and sessions[0].get("models")):
        return "—"
    raw = sessions[0]["models"][-1]
    if not raw or str(raw).startswith("<"):   # skip synthetic/placeholder model ids
        return "—"
    m = str(raw).lower()
    for fam in ("opus", "sonnet", "haiku"):
        if fam in m:
            hit = _MODEL_RE.search(m)
            if hit:
                return f"{hit.group(1).capitalize()} {hit.group(2)}.{hit.group(3)}"
    return raw


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

    return {
        "verdict": dot,
        "active_model": _active_model(report),
        "burn_rate_per_hour": round(burn, 2),
        "block_remaining_min": int(rem_min),
        "block_past_p90": bool(cw.get("block_already_past_p90")),
        "errors_24h": err.get("total", 0),
        "cache_hit_pct": round((totals.get("cache_hit_rate", 0) or 0) * 100, 1),
        "today_cost": round((fc.get("today", {}) or {}).get("so_far", 0) or 0, 2),
    }


def statusline_text(report: dict) -> str:
    """Single plain-text line, ready to print in a prompt / status bar."""
    s = build_statusline(report)
    rem = s["block_remaining_min"]
    rem_str = f"{rem // 60}h{rem % 60:02d}m" if rem >= 60 else f"{rem}dk"
    p90_flag = "⚠️ P90+" if s["block_past_p90"] else ""
    return (
        f"{s['verdict']} {s['active_model']} ${s['burn_rate_per_hour']:.0f}/h · "
        f"blok {rem_str} {p90_flag}· {s['errors_24h']}err/24h · "
        f"cache %{s['cache_hit_pct']:.0f} · gün ${s['today_cost']:.0f}"
    )
