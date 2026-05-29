# Naming — decision doc

**Status: open. Cihan decides.** This captures the research so the final pick is informed.

## Why "ccmeter" should change
- **Vendor-locked**: `cc` = Claude Code. We now track Codex too (and more coming).
- **Saturated suffix**: the `-meter` space is crowded — *TokenMeter*, *AgentMeter*,
  *VibeMeter*, *Meterly* are all taken. The whole `cc*` namespace (ccusage, ccseva,
  ccflare, cctray, ccstatusline) reads as Claude-only.

## Checked and TAKEN (avoid)
- **FuelGauge** — already a Claude Code status-line plugin (exact-space collision).
- **Tollgate** — PyPI package + OpenTollGate (Bitcoin router) project.
- **Gauge** — `@getgauge/cli` test framework owns it.
- **Burndown** — saturated + Agile-chart connotation (wrong meaning).
- **BurnRate** — taken by a *direct competitor* (`getburnrate.io`, AI cost tracker).
- **Ampere** — Ampere Computing (chip company).

## Top candidates (clean availability)
| Name | Why | Availability |
|------|-----|--------------|
| **Driftline** ⭐ | "spend is drifting — here's the line." Vendor-neutral, premium, short. `driftline.dev` likely free. Zero npm/PyPI/GitHub collisions found. | HIGH |
| **Headroom** | Engineers already say "we have headroom on the budget." Self-explanatory. `headroom.js` (old scroll lib) inactive. | MED-HIGH |
| **Tideline** | High-water-mark metaphor. Clean on npm/PyPI/GitHub. More abstract. | HIGH |

Cihan leaned toward a **fuel/meter** vibe ("Token-Fuel-Meter"-ish). If staying in
that lane, **FuelGauge is out** (taken) — alternatives that keep the energy/fuel
metaphor without collision: **Headroom**, **Reserve/Reservoir**, **Throttle**,
**Redline** (the "you're in the redline" gauge metaphor — worth checking).

## Recommendation
1. **Driftline** — strongest brand + cleanest availability.
2. **Headroom** — most self-explanatory, fits the "how much is left" framing.
3. **Tideline** — distinctive, clean.

## Before finalizing
- [ ] Reserve GitHub org + npm + PyPI + Homebrew tap name **together**.
- [ ] Grab the `.dev` domain (short dictionary `.com`s are mostly parked/expensive).
- [ ] One find-replace across `README.md`, `dashboard.html` `<title>`/wordmark,
      `app-name` default in `dashboard.js`, and `pyproject`/package metadata.
      (The UI wordmark is already isolated for easy swap.)
