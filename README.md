# ccmeter

**One dashboard for every AI coding agent — see exactly where your tokens go.**

> 🚧 **Rebranding in progress.** This started as *ccmeter* (Claude Code meter) but
> now tracks **Claude Code + OpenAI Codex** (more agents coming), so the name is
> moving to something vendor-neutral. Candidates & decision in
> [`NAMING.md`](./NAMING.md).

A **local-first** dashboard that reads the session logs your AI coding agents
already write to disk and turns them into a real-time view of token usage, cost,
burn-rate, and — most importantly — **how close you are to your plan's rate
limit**. No API keys. No cloud account. No phone-home. Your data never leaves
your machine.

---

## Why this exists

Neither Anthropic nor OpenAI exposes a real-time, per-token usage API for
**individual** Pro / Max / ChatGPT-subscription plans. The in-app usage page and
the CLI's `/usage` command are point-in-time only — not historical, not
programmatic. So if you're on a flat-fee subscription, **your local session logs
are the only accurate source of truth** for what you're actually burning.

ccmeter reads them — `~/.claude/projects/**/*.jsonl` and
`~/.codex/sessions/**/*.jsonl` — and answers the one question that matters:
**"Am I about to hit the wall?"**

## Features

- **Binding-constraint hero** — the single most-urgent number, front and center.
  For subscription plans that's your **rate-limit %** (5-hour + weekly); for API
  it's burn-rate $. Red ring at 90%+ means "you're about to get throttled."
- **Real per-model pricing** — Opus / Sonnet / Haiku and GPT-5.5 / 5.4 / Codex /
  mini, each at its actual rate. Output costs 5–8× input — the model mix *is* the
  cost story.
- **Cache-efficiency panel** — cache reads cost ~10% of fresh input. ccmeter
  shows your hit rate and the **dollars cache saved you** vs. an un-cached run
  (often the "I'm getting $50k of value for $200" number).
- **Per-project attribution** — which repo is eating your quota.
- **Burn-rate trend, daily spend, per-turn activity, model split** — the full
  "where did it go" picture, with sparklines and clean charts.
- **Multi-device** — sync a second machine's logs in (e.g. via Syncthing) and see
  per-device breakdowns.
- **Tiny footprint** — the long-running server holds ~30 MB; heavy parsing runs
  in a short-lived worker so memory never balloons.

## Install

> Packaging for Homebrew / npm / PyPI is in progress (see [`NAMING.md`](./NAMING.md)).
> For now, run from source (Python 3.10+, no third-party deps):

```bash
git clone <repo-url> ccmeter && cd ccmeter
python3 -m ccmeter serve --port 9876
# open http://127.0.0.1:9876
```

Multi-source (Claude + Codex) and a synced second machine:

```bash
python3 -m ccmeter serve --port 9876 \
  --extra-projects-dir ~/.claude/projects-pc \
  --codex-extra-dir   ~/.codex/sessions-pc
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

Everything stays on your disk. ccmeter binds to `127.0.0.1` only. There is no
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
