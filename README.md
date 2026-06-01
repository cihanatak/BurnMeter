# Burnmeter

**One dashboard for every AI coding agent — see exactly where your tokens go.**

A **local-first** dashboard that reads the session logs your AI coding agents
already write to disk and turns them into a real-time view of token usage, cost,
burn-rate, and — where the agent actually logs it (e.g. Codex) — **how close you
are to your plan's rate limit**. No API keys. No cloud account. No phone-home.
Your data never leaves your machine.

---

## Why this exists

Neither Anthropic nor OpenAI exposes a real-time, per-token usage API for
**individual** Pro / Max / ChatGPT-subscription plans. The in-app usage page and
the CLI's `/usage` command are point-in-time only — not historical, not
programmatic. So if you're on a flat-fee subscription, **your local session logs
are the only accurate source of truth** for what you're actually burning.

Burnmeter reads them — `~/.claude/projects/**/*.jsonl` and
`~/.codex/sessions/**/*.jsonl` — and answers the one question that matters:
**"Am I about to hit the wall?"**

## Features

- **Binding-constraint hero** — the single most-urgent number, front and center.
  **Codex** logs real rate-limit usage, so its hero is your **rate-limit %** (5-hour +
  weekly) — red ring at 90%+ means "about to get throttled." **Claude Code** exposes
  *no* account-level limit (Anthropic doesn't write one to disk), so its hero is
  burn-rate $ + live local usage — honestly labelled, never a guessed limit.
- **Real per-model pricing** — Opus / Sonnet / Haiku and GPT-5.5 / 5.4 / Codex /
  mini, each at its actual rate. Output costs 5–8× input — the model mix *is* the
  cost story.
- **Cache-efficiency panel** — cache reads cost ~10% of fresh input. burnmeter
  shows your hit rate and the **dollars cache saved you** vs. an un-cached run
  (often the "I'm getting $50k of value for $200" number).
- **Per-project attribution** — which repo is eating your quota.
- **Burn-rate trend, daily spend, per-turn activity, model split** — the full
  "where did it go" picture, with sparklines and clean charts.
- **Multi-device** — sync a second machine's logs in (e.g. via Syncthing) and see
  per-device breakdowns.
- **Terminal statusline** — `burnmeter statusline` prints a one-liner for your shell
  prompt or Claude Code's status bar (verdict · model · $/h · block left · errors ·
  cache · today). Same numbers as the dashboard, no server needed.
- **Tiny footprint** — the long-running server holds ~30 MB; heavy parsing runs
  in a short-lived worker so memory never balloons.

## Install

Python 3.10+, **zero third-party dependencies** (Chart.js / GridStack load from a CDN in
the browser). Recommended: [pipx](https://pipx.pypa.io) for an isolated install.

```bash
pipx install git+https://github.com/cihanatak/BurnMeter   # PyPI release coming → `pipx install burnmeter`
burnmeter serve --port 9876
# open http://127.0.0.1:9876
```

Or with pip, or straight from a source checkout:

```bash
pip install git+https://github.com/cihanatak/BurnMeter && burnmeter serve
# — or —
git clone https://github.com/cihanatak/BurnMeter && cd BurnMeter
python3 -m burnmeter serve --port 9876
```

Multi-source (Claude + Codex) and a synced second machine:

```bash
burnmeter serve --port 9876 \
  --extra-projects-dir ~/.claude/projects-pc \
  --codex-extra-dir   ~/.codex/sessions-pc
```

## Running on another machine

The whole app is in this repo — clone and run. Everything else is **machine-local by
design** (not committed):

```bash
git clone https://github.com/cihanatak/BurnMeter && cd BurnMeter
python -m pip install -e .          # free core, zero third-party deps
# Pro cross-device sync (optional): adds `cryptography`
python -m pip install -e ".[sync]"
python -m burnmeter serve --port 9876
```

- **It reads *that* machine's own logs** — `~/.claude/projects` and `~/.codex/sessions`
  (via `Path.home()`, so it works on macOS, Linux, and Windows: `C:\Users\<you>\.claude`).
  No data is copied between machines.
- **Auto-start is OS-specific and not in the repo.** macOS uses a launchd LaunchAgent;
  on Windows use Task Scheduler or just run `python -m burnmeter serve`. (Caches under
  `~/.claude` / `~/.codex` regenerate on first run.)
- **Pro sync to link two machines:** install the `[sync]` extra on both, run a relay
  somewhere (`burnmeter sync-relay`), then on each machine
  `burnmeter sync login --relay <url> --token <same-token>` with the **same passphrase**,
  and `burnmeter sync push`. Each shows up in the other's **Cihazlar** card. The relay
  only ever stores ciphertext; the passphrase never leaves your machines.

## Terminal statusline

A one-line live readout for your prompt or Claude Code's status bar — verdict
dot, active model, $/h burn, block time left, 24h errors, cache hit, today's spend:

```bash
burnmeter statusline
# 🟠 Sonnet 4.6 $48/h · blok 1h34m · 12err/24h · cache %97 · gün $42

burnmeter statusline --source codex   # Codex instead of Claude
burnmeter statusline --json           # machine-readable, same numbers
```

It computes standalone (no running server needed) and shares its logic with the
`/api/statusline` HTTP endpoint, so the terminal line and the dashboard never drift.

Wire it into **Claude Code** (`~/.claude/settings.json`) so it refreshes in your prompt:

```json
{
  "statusLine": { "type": "command", "command": "burnmeter statusline" }
}
```

## How it works

```
~/.claude/projects/**/*.jsonl  ─┐
~/.codex/sessions/**/*.jsonl   ─┤→ parser → normalized UsageRecord
                                │            ↓ (in a short-lived worker)
                                └──────────→ analytics → report JSON
                                                         ↓
                                       local web dashboard (127.0.0.1)
```

No network calls except loading Chart.js from a CDN (vendor it for fully
air-gapped use). Costs are **client-side estimates** based on published pricing —
for billing, see your provider's console.

## Privacy / local-first

Everything stays on your disk. Burnmeter binds to `127.0.0.1` only. There is no
telemetry, no account, no upload. The optional Pro **sync** feature (see
[`PRICING.md`](./PRICING.md)) is end-to-end encrypted and entirely opt-in.

## Supported agents

| Agent | Source | Status |
|-------|--------|--------|
| Claude Code | `~/.claude/projects` | ✅ |
| OpenAI Codex | `~/.codex/sessions` | ✅ |
| Cursor / Copilot / Gemini / others | — | 🛣️ planned |

Adding an agent is a single parser module — see [`CONTRIBUTING.md`](./CONTRIBUTING.md).

## Pricing

The local dashboard is **free and open source forever**. A Pro tier adds
cross-device sync, unlimited history, and pre-limit alerts; a Team tier adds
cost aggregation & attribution. Details in [`PRICING.md`](./PRICING.md).

## License

[AGPL-3.0-or-later](./LICENSE) for the open-source core, with a commercial
dual-license available for organizations. Free for individuals, including
commercial use.
