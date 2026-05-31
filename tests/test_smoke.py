"""Smoke tests — exercise parser + analytics + server with synthetic data.

Run from the repo root:
    python -m tests.test_smoke
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests.generate_fixture import main as gen_main  # noqa: E402
from ccmeter.parser import load_records  # noqa: E402
from ccmeter.analytics import build_report  # noqa: E402
from ccmeter.pricing import estimate_cost_usd  # noqa: E402
from ccmeter import server as srv  # noqa: E402


def assert_eq(a, b, msg=""):
    if a != b:
        raise AssertionError(f"{msg}: expected {b!r}, got {a!r}")


def assert_true(cond, msg=""):
    if not cond:
        raise AssertionError(msg)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="ccmeter-smoke-"))
    try:
        # 1. Generate synthetic data.
        gen_main(["--out", str(tmp), "--days", "14", "--seed", "7"])
        files = list(tmp.rglob("*.jsonl"))
        assert_true(len(files) >= 5, f"expected several jsonl files, got {len(files)}")
        print(f"[ok] generated {len(files)} synthetic session files")

        # 2. Parser loads them. (load_records → records, stats, intents, error_events)
        records, stats, _intents, _error_events = load_records(tmp)
        assert_true(stats["files_scanned"] == len(files), "files_scanned mismatch")
        assert_true(stats["parse_errors"] == 0, f"unexpected parse errors: {stats}")
        assert_true(len(records) > 100, f"too few records: {len(records)}")
        print(f"[ok] parsed {len(records)} usage records, errors={stats['parse_errors']}")

        # 3. Pricing sanity.
        c = estimate_cost_usd("claude-sonnet-4-5", input_tokens=1_000_000)
        assert_eq(round(c, 4), 3.0, "Sonnet input pricing")
        c = estimate_cost_usd("claude-opus-4-5", output_tokens=1_000_000)
        assert_eq(round(c, 4), 25.0, "Opus output pricing")
        c = estimate_cost_usd("claude-haiku-4-5", cache_read_tokens=1_000_000)
        assert_eq(round(c, 4), 0.10, "Haiku cache-read pricing")
        c = estimate_cost_usd("unknown-model", input_tokens=999_999)
        assert_eq(c, 0.0, "unknown model pricing")
        print("[ok] pricing math matches table")

        # 4. Build report.
        report = build_report(records)
        keys = {"totals", "daily", "by_model", "by_project", "by_session",
                "by_tool", "windows", "current_window", "plan_inference",
                "baseline", "anomalies", "plan_limits", "industry_reference"}
        missing = keys - set(report.keys())
        assert_true(not missing, f"report missing keys: {missing}")
        assert_true(report["totals"]["total_tokens"] > 0, "no tokens in totals")
        assert_true(report["totals"]["cost_usd"] > 0, "no cost in totals")
        assert_true(0 <= report["totals"]["cache_hit_rate"] <= 1,
                    "cache_hit_rate out of range")
        assert_true(len(report["daily"]) >= 5, "expected several daily buckets")
        assert_true(report["plan_inference"]["guess"] in {"pro", "max5", "max20", "unknown"},
                    "bad plan inference")
        # The fixture deliberately injects an anomalous day; verify detector found it.
        assert_true(len(report["anomalies"]) >= 1,
                    "anomaly detector failed to catch the synthetic spike")
        print(f"[ok] report has {len(report['daily'])} days, "
              f"{len(report['windows'])} windows, "
              f"plan guess = {report['plan_inference']['guess']}, "
              f"{len(report['anomalies'])} anomaly day(s)")

        # 5. Spin up the server, hit the API.
        cache = srv._Cache(tmp, ttl_seconds=1)
        server = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.make_handler(cache))
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            # health
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
                health = json.loads(r.read())
            assert_true(health["ok"], "health not ok")

            # report
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/report", timeout=5) as r:
                api_report = json.loads(r.read())
            assert_true(api_report["totals"]["total_tokens"] > 0, "API report empty")

            # CSV
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/records.csv", timeout=5) as r:
                csv_body = r.read().decode("utf-8")
            assert_true(csv_body.startswith("timestamp,session_id,"), "bad CSV header")
            csv_lines = csv_body.strip().splitlines()
            assert_true(len(csv_lines) - 1 == len(records),
                        f"CSV row count mismatch: {len(csv_lines)-1} vs {len(records)}")

            # static
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
                html = r.read().decode("utf-8")
            assert_true("ccmeter" in html, "dashboard HTML missing")

            print(f"[ok] server alive on :{port}, health, report, CSV, dashboard all served")
        finally:
            server.shutdown()
            server.server_close()

        print("\nALL SMOKE TESTS PASSED ✓")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_smoke_suite():
    """pytest entrypoint — runs the full parser → analytics → server smoke suite."""
    main()


if __name__ == "__main__":
    main()
