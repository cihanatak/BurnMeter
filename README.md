# ccmeter

Local-only token & cost meter for Claude Code. No `pip install`, no
network calls from Python, nothing leaves your machine.

> Built for developers who want **honest, evidence-based** answers to:
> *"Am I burning too many tokens? Is my cache working? What's normal?"*

## What you actually see

Six layers, each answering a real question:

1. **Today vs your own baseline** — is today's spend in the normal band,
   elevated, or above your personal P90?
2. **Active 5-hour window** — how full is the current Claude Code billing
   window, what's the burn rate (tok/min), and what's the projected
   close vs your plan limit?
3. **Cache hit rate** — fraction of input-side tokens served from cache.
   Cache reads cost ~10% of standard input, so this metric maps directly
   to your bill. Below 40% is "too low", above 70% is excellent.
4. **Daily trend** — stacked input/output/cache-write/cache-read bars
   with a cost line. Anomalous days are auto-flagged (z-score ≥ 2 or
   cost ratio ≥ 2× trailing median).
5. **Model breakdown** — Opus / Sonnet / Haiku share, with per-family
   token & cost rollups. Spotting an "all-Opus week" is the fastest
   way to find a 3× cost win.
6. **Tools, projects, sessions** — drill down by tool name, project
   directory, or individual session.

Plus a **personal baseline** (P50/P90/P99 of your own daily cost) and a
table of **industry reference values** taken from Anthropic's own cost
docs (avg enterprise dev ≈ $13/active day; P90 ≤ $30/active day).

## Install

Requires only **Python 3.10+** (uses `tomllib`-style stdlib idioms).
Nothing to install.

```bash
git clone <wherever you put this>
cd ccmeter
python3 -m ccmeter status
```

## Usage

### Web dashboard

```bash
python3 -m ccmeter serve
# → http://127.0.0.1:8765
```

The page auto-refreshes every 30 s (matching the server's JSONL re-scan
TTL). The only external resource is Chart.js loaded from a CDN; if you
need fully air-gapped operation, drop `chart.umd.min.js` into `static/`
and edit `dashboard.html` to reference it.

### Terminal

```bash
python3 -m ccmeter status              # one-screen overview
python3 -m ccmeter daily --limit 30    # last 30 days
python3 -m ccmeter models              # rollup by model family
python3 -m ccmeter sessions --limit 50 # recent sessions
```

All commands accept `--projects-dir` to point at a non-default location
(e.g. for testing or for inspecting an exported transcript folder).
The `serve` command accepts `--host`, `--port`, `--ttl`.

### CSV export

`http://127.0.0.1:8765/api/records.csv` returns every parsed assistant
turn. Useful for ad-hoc analysis in pandas/DuckDB.

## How it works

Claude Code writes one JSONL file per session under
`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`. Each line is a
JSON record; assistant messages contain a `message.usage` block with
`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, and
`cache_read_input_tokens`, plus `message.model`. ccmeter:

1. Walks the projects tree and parses every `.jsonl`.
2. Deduplicates by `message.id` (parallel tool calls produce multiple
   records with identical usage; counting them all would inflate
   totals — Anthropic's SDK docs explicitly warn about this).
3. Computes per-day, per-session, per-project, per-model, per-tool
   aggregates plus 5-hour billing-window clusters.
4. Estimates cost using a built-in price table for Opus / Sonnet / Haiku.
5. Caches the result for 15 s (`--ttl`) so the dashboard polls cheaply.

## Honest caveats

- **Costs are client-side estimates**, not authoritative billing. The
  Anthropic Agent SDK docs say the same about its own `total_cost_usd`
  field.
- **Plan limits are approximations**. Anthropic now describes them in
  relative terms; the 44k/88k/220k token anchors come from community
  reverse-engineering and are widely cited but not contractual. They're
  also weighted internally (cache reads cost less than fresh input), so
  the raw token count you see here may exceed the budget while you
  haven't actually hit a limit.
- **Tool token attribution is approximate**. Output tokens for a turn
  cover both prose and tool_use blocks together; we split the turn's
  output evenly across the tool_use blocks present, which is good
  enough for ranking but not for exact accounting.
- **Pricing tables drift**. The price table sits in `ccmeter/pricing.py`
  — verify against [claude.com/pricing](https://claude.com/pricing) before
  relying on the cost numbers for invoicing.

## Layout

```
ccmeter/
├── ccmeter/
│   ├── __init__.py
│   ├── __main__.py        # python -m ccmeter
│   ├── analytics.py       # aggregations, anomaly detection, plan inference
│   ├── cli.py             # status / daily / models / sessions / serve
│   ├── parser.py          # JSONL → UsageRecord
│   ├── pricing.py         # model price table & cost estimator
│   └── server.py          # stdlib HTTP server, /api/report, CSV export
├── static/
│   ├── dashboard.html
│   ├── dashboard.css
│   └── dashboard.js       # vanilla JS + Chart.js
└── tests/
    ├── generate_fixture.py  # synthetic JSONL for offline testing
    └── test_smoke.py        # parser → analytics → server end-to-end
```

## Run the tests

```bash
python3 -m tests.test_smoke
```

The smoke test generates 14 days of synthetic Claude Code sessions
(including one deliberately anomalous day), runs the full pipeline,
spins up the HTTP server on a random port, and asserts that every
endpoint returns sane data.

## License

MIT.
