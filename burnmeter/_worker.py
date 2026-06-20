"""Short-lived build worker — bounds the long-running server's RSS.

The server spawns this as a subprocess, sends a JSON config on stdin, and reads
the built report JSON from stdout. The worker loads ~half-a-million records,
builds the report (transiently spiking to ~800MB), prints the JSON, then EXITS
— so the OS reclaims that memory immediately. The long-running server process
only ever holds the small report JSON (~0.5MB), never the raw records.

This is a zero-logic-change RAM fix: the exact same load_records / build_report
run, just in a process that dies after each build. No streaming-aggregate
rewrite, no risk of subtly wrong numbers.

Usage: `python3 -m burnmeter._worker` then write config JSON to stdin:
  {"source":"codex","projects_dir":"...","extra_roots":[...],
   "codex_dir":"...","codex_extra_roots":[...],"plan":null}
Prints report JSON to stdout. Non-zero exit on error.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    # Config in / report out go through FILES (env vars) when set — a frozen
    # --windowed (no-console) process has no usable stdin/stdout, so writing the
    # report to stdout raised OSError [Errno 22]. Fall back to stdin/stdout for
    # plain `python -m burnmeter._worker` use.
    src = os.environ.get("BURNMETER_WORKER_IN")
    raw = Path(src).read_text(encoding="utf-8") if src else (sys.stdin.read() or "{}")
    try:
        cfg = json.loads(raw or "{}")
    except json.JSONDecodeError as e:
        try:
            sys.stderr.write(f"[worker] bad config: {e}\n")
        except Exception:
            pass
        return 2

    source = cfg.get("source", "claude")
    plan = cfg.get("plan")

    from .analytics import build_report

    if source == "codex":
        from .codex_parser import load_codex_records, CODEX_SESSIONS_DIR
        root = Path(cfg["codex_dir"]) if cfg.get("codex_dir") else CODEX_SESSIONS_DIR
        extra = [Path(p) for p in (cfg.get("codex_extra_roots") or [])]
        records, stats, intents = load_codex_records(
            root, extra_roots=extra, since_days=int(cfg.get("codex_since_days", 90)))
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
    out = json.dumps(report, default=str)
    dest = os.environ.get("BURNMETER_WORKER_OUT")
    if dest:
        Path(dest).write_text(out, encoding="utf-8")
    else:
        sys.stdout.write(out)
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
