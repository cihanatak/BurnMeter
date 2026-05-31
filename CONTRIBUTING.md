# Contributing

Thanks for helping. The highest-leverage contribution is **adding a new agent**.

## Architecture (how a new agent plugs in)

The analytics pipeline is **source-agnostic**. Everything downstream works on a
normalized `UsageRecord` (see `burnmeter/parser.py`), so a new agent only needs a
parser that emits those.

```
<agent log files>  →  parse → list[UsageRecord]  →  build_report()  →  report JSON  →  dashboard
                     (your job)                    (shared, reused)
```

`UsageRecord` fields that matter: `timestamp, session_id, project_dir,
project_label, model, input_tokens, output_tokens, cache_creation_tokens,
cache_read_tokens, turn_id, device`. Tokens are split so pricing can weight cache
reads at ~10% of fresh input.

## Add an agent in 3 steps

1. **Write `burnmeter/<agent>_parser.py`** with a `load_<agent>_records(root,
   extra_roots=None) -> (records, stats, intents)` function. Model it on
   `codex_parser.py`:
   - Discover the agent's session files.
   - Extract per-turn token usage → `UsageRecord`.
   - **Keep it RAM-cheap**: for large/old logs, use a compact on-disk cache keyed
     by `(path, mtime, size)` and store compact tuples, not per-record dicts
     (see `codex_parser`'s cache — it took the Codex cache from 198 MB to 52 MB).

2. **Add pricing** in `burnmeter/pricing.py`: a `ModelPrice` entry per model family
   and a branch in `family_from_model()`. Cache-read rate is ~10% of input;
   `effective_tokens()` derives the cost-weighting automatically.

3. **Wire the source** in `burnmeter/server.py` (`pick_cache` + a `_Cache` with a
   `worker_config`) and `burnmeter/_worker.py` (a branch for your `source`). Add the
   toggle button in `static/dashboard.html` and a brand color in `dashboard.css`
   (`.pill.<family>`) + `modelToFamily()` in `dashboard.js`.

## Conventions
- **No third-party Python deps** in the core (stdlib only — keeps install trivial).
- **Costs are estimates** — always labeled as such; never imply they're billing.
- **Local-first** — no network calls from Python, bind `127.0.0.1`.
- **RAM discipline** — heavy work runs in the build worker (`_worker.py`); the
  server process must stay small. Don't hold full record lists in the server.
- **Verify on screen** — UI changes must be checked in a browser, not just by
  DOM probes. Numbers are the product; a wrong number is worse than a missing one.

## Dev loop
```bash
python3 -m burnmeter serve --port 9876        # run
node --check static/dashboard.js            # JS sanity
python3 -c "import burnmeter.server"          # import sanity
echo '{"source":"codex"}' | python3 -m burnmeter._worker | python3 -m json.tool | head
```
