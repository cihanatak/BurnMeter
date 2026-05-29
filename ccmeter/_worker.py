"""Short-lived build worker — bounds the long-running server's RSS.

The server spawns this as a subprocess, sends a JSON config on stdin, and reads
the built report JSON from stdout. The worker loads ~half-a-million records,
builds the report (transiently spiking to ~800MB), prints the JSON, then EXITS
— so the OS reclaims that memory immediately. The long-running server process
only ever holds the small report JSON (~0.5MB), never the raw records.

This is a zero-logic-change RAM fix: the exact same load_records / build_report
run, just in a process that dies after each build. No streaming-aggregate
rewrite, no risk of subtly wrong numbers.

Usage: `python3 -m ccmeter._worker` then write config JSON to stdin:
  {"source":"codex","projects_dir":"...","extra_roots":[...],
   "codex_dir":"...","codex_extra_roots":[...],"plan":null}
Prints report JSON to stdout. Non-zero exit on error.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    try:
        cfg = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as e:
        sys.stderr.write(f"[worker] bad config: {e}\n")
        return 2

    source = cfg.get("source", "claude")
    plan = cfg.get("plan")

    from .analytics import build_report

    if source == "codex":
        from .codex_parser import load_codex_records, CODEX_SESSIONS_DIR
        root = Path(cfg["codex_dir"]) if cfg.get("codex_dir") else CODEX_SESSIONS_DIR
        extra = [Path(p) for p in (cfg.get("codex_extra_roots") or [])]
        records, stats, intents = load_codex_records(root, extra_roots=extra)
        errors = []
    else:
        from .parser import load_records, CLAUDE_PROJECTS_DIR
        root = Path(cfg["projects_dir"]) if cfg.get("projects_dir") else CLAUDE_PROJECTS_DIR
        extra = [Path(p) for p in (cfg.get("extra_roots") or [])]
        records, stats, intents, errors = load_records(root, extra_roots=extra)

    report = build_report(records, plan=plan, user_intents=intents,
                          error_events=errors, source=source)
    report["_meta"] = {**stats, "source": source}
    if source == "codex" and stats.get("rate_limits"):
        report["codex_rate_limits"] = stats["rate_limits"]

    # Release before serializing (records no longer needed).
    records = None
    sys.stdout.write(json.dumps(report, default=str))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
