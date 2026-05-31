"""Command line interface for terminal-only users.

Subcommands:
    status   one-screen summary (today + active 5h window)
    daily    daily breakdown table
    models   per-model totals
    sessions recent sessions
    serve    start the local web dashboard
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .parser import CLAUDE_PROJECTS_DIR, load_records
from .analytics import (
    build_report,
    aggregate_by_day,
    aggregate_by_model,
    aggregate_by_session,
    aggregate_total,
    detect_billing_windows,
    current_window_status,
    infer_plan,
    PLAN_LIMITS,
    INDUSTRY_REFERENCE,
)


# ANSI helpers — degrade gracefully if not a tty.
def _supports_color() -> bool:
    return sys.stdout.isatty() and sys.platform != "win32"


def _c(code: str, s: str) -> str:
    if not _supports_color():
        return s
    return f"\033[{code}m{s}\033[0m"


def bold(s): return _c("1", s)
def dim(s): return _c("2", s)
def green(s): return _c("32", s)
def yellow(s): return _c("33", s)
def red(s): return _c("31", s)
def cyan(s): return _c("36", s)


def _fmt_int(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def _fmt_money(n) -> str:
    try:
        return f"${float(n):,.2f}"
    except (TypeError, ValueError):
        return str(n)


def _fmt_pct(n) -> str:
    try:
        return f"{float(n) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(n)


def _bar(value: float, total: float, width: int = 30) -> str:
    if total <= 0:
        return "[" + " " * width + "]"
    pct = max(0.0, min(1.0, value / total))
    fill = int(round(pct * width))
    bar = "█" * fill + "░" * (width - fill)
    color = green
    if pct >= 0.95:
        color = red
    elif pct >= 0.75:
        color = yellow
    return "[" + color(bar) + "]"


def _print_header(title: str):
    print()
    print(bold(cyan(f"━━━ {title} ━━━")))


def cmd_status(args):
    records, stats, _, _ = load_records(Path(args.projects_dir))
    if not records:
        print(red("No usage records found."))
        print(dim(f"Looked in: {stats['root']} (exists={stats['root_exists']})"))
        print(dim(f"  files scanned: {stats['files_scanned']}"))
        print(dim(f"  parse errors:  {stats['parse_errors']}"))
        return 1

    report = build_report(records, plan=args.plan)

    today_iso = datetime.now(timezone.utc).date().isoformat()
    today = next((d for d in report["daily"] if d["date"] == today_iso), None)
    totals = report["totals"]
    baseline = report["baseline"]
    cw = report["current_window"]
    plan_inf = report["plan_inference"]

    _print_header("burnmeter status")
    print(f"  data: {_fmt_int(report['record_count'])} usage records "
          f"across {_fmt_int(stats['files_scanned'])} files")

    _print_header("today")
    if today:
        print(f"  tokens: {bold(_fmt_int(today['total_tokens']))}   "
              f"cost: {bold(_fmt_money(today['cost_usd']))}   "
              f"cache hit: {bold(_fmt_pct(today['cache_hit_rate']))}")
        if baseline.get("samples", 0) >= 3:
            p50 = baseline["cost_p50"]
            p90 = baseline["cost_p90"]
            ratio = today["cost_usd"] / p50 if p50 else 0
            verdict = green("normal")
            if today["cost_usd"] > p90:
                verdict = red(f"high (>P90 of {_fmt_money(p90)})")
            elif ratio >= 1.5:
                verdict = yellow(f"elevated ({ratio:.1f}x your median)")
            elif ratio <= 0.5:
                verdict = dim("light")
            print(f"  vs baseline (P50={_fmt_money(p50)}, P90={_fmt_money(p90)}): {verdict}")
        ref = INDUSTRY_REFERENCE
        print(dim(
            f"  reference: avg ent. dev = {_fmt_money(ref['avg_cost_per_active_day_usd'])}/active day, "
            f"P90 = {_fmt_money(ref['active_day_p90_cost_usd'])}/active day"
        ))
    else:
        print(dim("  no activity today"))

    _print_header("active 5-hour window")
    if cw.get("active"):
        print(f"  plan: {bold(cw['plan'].upper())}  "
              f"(inferred from P90 over last {plan_inf['samples']} windows: "
              f"{_fmt_int(plan_inf['p90_window_tokens'])} tokens)")
        used = cw["tokens_used"]
        limit = cw["plan_limit"]
        print(f"  usage: {_fmt_int(used)} / {_fmt_int(limit)}  "
              f"({_fmt_pct(cw['utilization'])})")
        print(f"         {_bar(used, limit)}")
        mins_left = cw["remaining_seconds"] // 60
        print(f"  burn rate: {_fmt_int(int(cw['burn_tokens_per_min']))} tokens/min   "
              f"time left: {mins_left} min")
        if cw.get("projected_overage", 0) > 0:
            print(red(f"  ⚠ projected to exceed limit by "
                      f"{_fmt_int(cw['projected_overage'])} tokens at window close"))
        else:
            print(green(f"  ✓ projected close: "
                        f"{_fmt_int(cw['projected_close_tokens'])} tokens"))
    else:
        print(dim("  no active window"))

    _print_header("lifetime")
    print(f"  {_fmt_int(totals['total_tokens'])} tokens, "
          f"{_fmt_money(totals['cost_usd'])} (estimated)")
    print(f"  cache hit rate: {bold(_fmt_pct(totals['cache_hit_rate']))}  ", end="")
    chr_ = totals["cache_hit_rate"]
    if chr_ >= 0.7:
        print(green("(excellent — keep doing this)"))
    elif chr_ >= 0.4:
        print(yellow("(ok — there's headroom to save more)"))
    else:
        print(red("(low — most input is uncached; expect higher bills)"))

    if report["anomalies"]:
        _print_header("recent anomalies (last 14 days)")
        for a in report["anomalies"][-5:]:
            print(yellow(
                f"  {a['date']}: {_fmt_int(a['tokens'])} tokens "
                f"({_fmt_money(a['cost_usd'])}) — {', '.join(a['flags'])}"
            ))
    return 0


def cmd_daily(args):
    records, _, _, _ = load_records(Path(args.projects_dir))
    daily = aggregate_by_day(records)
    if not daily:
        print("(no data)")
        return 1
    print(f"{'date':<12}{'tokens':>14}{'cost':>10}{'cache hit':>12}{'msgs':>8}")
    print("-" * 56)
    for d in daily[-args.limit:]:
        print(
            f"{d['date']:<12}"
            f"{_fmt_int(d['total_tokens']):>14}"
            f"{_fmt_money(d['cost_usd']):>10}"
            f"{_fmt_pct(d['cache_hit_rate']):>12}"
            f"{_fmt_int(d['messages']):>8}"
        )
    return 0


def cmd_models(args):
    records, _, _, _ = load_records(Path(args.projects_dir))
    rows = aggregate_by_model(records)
    if not rows:
        print("(no data)")
        return 1
    print(f"{'model':<10}{'msgs':>8}{'tokens':>14}{'cost':>10}{'cache hit':>12}")
    print("-" * 54)
    for r in rows:
        print(
            f"{r['model_family']:<10}"
            f"{_fmt_int(r['messages']):>8}"
            f"{_fmt_int(r['total_tokens']):>14}"
            f"{_fmt_money(r['cost_usd']):>10}"
            f"{_fmt_pct(r['cache_hit_rate']):>12}"
        )
    return 0


def cmd_sessions(args):
    records, _, _, _ = load_records(Path(args.projects_dir))
    rows = aggregate_by_session(records)[: args.limit]
    if not rows:
        print("(no data)")
        return 1
    for s in rows:
        models = ",".join({m.split("-")[1] if "-" in m else m for m in s["models"]}) or "?"
        print(
            f"{s['started_at'][:19]}  "
            f"{s['project_label'][:20]:<20}  "
            f"{models:<8}  "
            f"{_fmt_int(s['total_tokens']):>10} tok  "
            f"{_fmt_money(s['cost_usd']):>8}  "
            f"{s['session_id'][:8]}"
        )
    return 0


def cmd_serve(args):
    from .server import serve
    extras = [Path(p).expanduser() for p in (args.extra_projects_dir or [])]
    codex_extras = [Path(p).expanduser() for p in (getattr(args, "codex_extra_dir", []) or [])]
    codex_dir = Path(args.codex_dir).expanduser() if getattr(args, "codex_dir", None) else None
    serve(host=args.host, port=args.port,
          projects_dir=Path(args.projects_dir), ttl_seconds=args.ttl,
          extra_roots=extras,
          codex_dir=codex_dir,
          codex_extra_roots=codex_extras)
    return 0


def cmd_statusline(args):
    """One short line for a prompt / Claude Code statusLine.command.

    Computes standalone (no running server needed) so it works even before
    `burnmeter serve` is up. Shares burnmeter/statusline.py with the HTTP endpoint,
    so the terminal line and the web pull are identical.
    """
    from .statusline import build_statusline, statusline_text
    src = (getattr(args, "source", "claude") or "claude").lower()
    if src == "codex":
        from .codex_parser import CODEX_SESSIONS_DIR, load_codex_records
        root = (Path(args.codex_dir).expanduser()
                if getattr(args, "codex_dir", None) else CODEX_SESSIONS_DIR)
        records, _stats, intents = load_codex_records(root)
        errors: list = []
    else:
        records, _stats, intents, errors = load_records(Path(args.projects_dir))

    # A statusLine command must ALWAYS print exactly one short line and exit 0,
    # otherwise Claude Code surfaces it as an error in the prompt.
    if not records:
        print(f"○ burnmeter: {src} verisi yok")
        return 0

    report = build_report(records, plan=args.plan, user_intents=intents,
                          error_events=errors, source=src)
    if getattr(args, "json", False):
        import json
        print(json.dumps(build_statusline(report), ensure_ascii=False))
    else:
        print(statusline_text(report))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="burnmeter")
    parser.add_argument("--projects-dir", default=str(CLAUDE_PROJECTS_DIR))
    parser.add_argument("--plan", default=None,
                        choices=["pro", "max5", "max20"],
                        help="override plan inference")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="one-screen overview")

    p_daily = sub.add_parser("daily", help="per-day breakdown")
    p_daily.add_argument("--limit", type=int, default=14)

    sub.add_parser("models", help="per-model totals")

    p_sessions = sub.add_parser("sessions", help="recent sessions")
    p_sessions.add_argument("--limit", type=int, default=20)

    p_serve = sub.add_parser("serve", help="start the local web dashboard")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--ttl", type=int, default=15)
    p_serve.add_argument("--extra-projects-dir", action="append", default=[],
                         help="ek Claude JSONL dizinleri (PC Syncthing mirror'ı vb.). "
                              "Birden çok için flag'i tekrarla.")
    p_serve.add_argument("--codex-dir", default=None,
                         help="Codex sessions kökü (default ~/.codex/sessions)")
    p_serve.add_argument("--codex-extra-dir", action="append", default=[],
                         help="ek Codex sessions dizinleri (PC mirror'ı vb.).")

    p_sl = sub.add_parser("statusline",
                          help="tek satır canlı durum (Claude Code statusLine.command için)")
    p_sl.add_argument("--source", default="claude", choices=["claude", "codex"],
                      help="hangi araç (default claude)")
    p_sl.add_argument("--json", action="store_true",
                      help="plain text yerine JSON çıktı")
    p_sl.add_argument("--codex-dir", default=None,
                      help="Codex sessions kökü (default ~/.codex/sessions)")

    args = parser.parse_args(argv)

    handlers = {
        "status": cmd_status,
        "daily": cmd_daily,
        "models": cmd_models,
        "sessions": cmd_sessions,
        "serve": cmd_serve,
        "statusline": cmd_statusline,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
