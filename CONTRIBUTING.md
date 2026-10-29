# Contributing

Thanks for helping. Bug reports, fixes and docs are all welcome. The
highest-leverage code contribution is **adding a new agent** (see below).

Please read the [Code of Conduct](./CODE_OF_CONDUCT.md). Security problems go
through [`SECURITY.md`](./SECURITY.md), not public issues.

## Contributor License Agreement

Burnmeter is AGPL-3.0-or-later, and the maintainer plans to offer a commercial
license next to it ([`LICENSING.md`](./LICENSING.md)). That only works if the
project has the right to relicense contributions, so pull requests need a signed
[Contributor License Agreement](./.github/CLA.md). You keep the copyright in your
work.

Signing takes one comment. On your first pull request a bot posts a link to the
CLA; reply on the pull request with:

```
I have read the CLA Document and I hereby sign the CLA
```

The CLA check turns green, and later pull requests are covered. If the bot
doesn't pick up your signature, comment `recheck`. Every commit in the pull
request must be authored by a GitHub account that has signed, so make sure the
email in your commits is linked to your GitHub account.

## Development setup

You need Python 3.10+ and, for the frontend tests, Node.js (CI uses Node 24).

```bash
git clone https://github.com/cihanatak/BurnMeter
cd BurnMeter
python -m venv .venv
# macOS / Linux: source .venv/bin/activate     Windows: .venv\Scripts\activate
python -m pip install -e ".[sync]" pytest Pillow
python -m burnmeter serve --port 9876 --no-shortcut
```

`[sync]` adds `cryptography`, which the Pro sync tests need; `Pillow` is used by
the tray icon test. The core itself has no third-party dependencies.

## Tests

```bash
python -m pytest                         # whole Python suite
node tests/dashboard_render.test.mjs     # dashboard.js render invariants
```

`tests/generate_fixture.py` writes synthetic Claude Code logs if you need data
to look at without using your own.

CI ([`.github/workflows/test.yml`](./.github/workflows/test.yml)) runs both on
Windows, macOS and Linux with Python 3.10 and 3.12 for every pull request. Please
make sure they pass locally first.

## Architecture: how a new agent plugs in

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
reads and writes at their own rates.

### Add an agent in 3 steps

1. **Write `burnmeter/<agent>_parser.py`** with a `load_<agent>_records(root,
   extra_roots=None) -> (records, stats, intents)` function. Model it on
   `codex_parser.py`:
   - Discover the agent's session files.
   - Extract per-turn token usage → `UsageRecord`.
   - **Keep it RAM-cheap**: for large or old logs, use a compact on-disk cache
     keyed by `(path, mtime, size)` and store compact tuples, not per-record dicts
     (see `codex_parser`'s cache, which took the Codex cache from 198 MB to 52 MB).

2. **Add pricing** in `burnmeter/pricing.py`: a `ModelPrice` row per model
   *version* in `PRICES`, and a mapping in `resolve_price()` from the agent's
   model ids to those rows. Link the provider's pricing page in the pull request.
   If you re-checked the whole table, update `PRICES_VERIFIED_AT`.

3. **Wire the source** in `burnmeter/server.py` (`pick_cache` and a `_Cache` with
   a `worker_config`) and `burnmeter/_worker.py` (a branch for your `source`). Add
   the toggle button in `burnmeter/static/dashboard.html`, a brand color in
   `dashboard.css` and a case in `modelToFamily()` in `dashboard.js`.

## Conventions

- **No third-party Python dependencies in the core** (stdlib only). Optional
  features go behind an extra (`[sync]`, `[tray]`, `[app]`) and import lazily.
- **Costs are estimates.** Always label them as such; never imply they are a
  bill.
- **Local first.** The server binds `127.0.0.1`. Usage data leaves the machine
  only through features the user turns on (Pro sync, alerts). The default
  outbound requests (the version check and the Chart.js CDN) carry no usage data.
  Document any new outbound request in the README's privacy section.
- **RAM discipline.** Heavy work runs in the build worker (`_worker.py`); the
  server process must stay small. Don't hold full record lists in the server.
- **Check UI changes on screen**, in a browser or the app, not only through DOM
  probes. Numbers are the product; a wrong number is worse than a missing one.
- **Commits:** short [Conventional Commits](https://www.conventionalcommits.org/)
  style (`fix(pricing): …`, `docs: …`). Keep behavior, docs and formatting
  changes in separate commits where you can.

## Quick checks

```bash
node --check burnmeter/static/dashboard.js                 # JS syntax
python -c "import burnmeter.server"                        # import sanity
echo '{"source":"codex"}' | python -m burnmeter._worker | python -m json.tool | head
```
