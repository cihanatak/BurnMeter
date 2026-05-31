# Changelog

## [Unreleased] — 2026-05-30 (commercial transform)

### Added
- **Multi-agent**: OpenAI Codex source alongside Claude Code, with a header
  toggle (burnmeter ↔ codex_meter). Same dashboard, source-aware data + branding.
- **Modern dashboard** (Linear-anchored dark design system): hero binding-
  constraint ring, KPI strip with sparklines, plan-limit meters, cache-efficiency
  panel, burn-rate trend area chart, model donut, daily bars, per-project /
  per-model / recent-activity tables.
- **Burn-rate efficiency gauge** ("çok mu yakıyorsun?"): current $/hr against
  personal typical/busy/heavy reference zones + "Nx faster than your normal".
- **Codex rate-limit hero**: real per-device 5h + weekly `used_percent` from
  Codex session logs (the binding constraint on a flat-fee plan).
- **Real per-model pricing**: Opus/Sonnet/Haiku + GPT-5.5/5.4/mini/Codex.
- **Cache-efficiency metric**: real $ saved vs an un-cached run.
- **Per-project attribution** + multi-device (Syncthing) support.
- AGPL-3.0 license, README, PRICING, NAMING, CONTRIBUTING.

### Changed
- **RAM**: build now runs in a short-lived worker subprocess → server RSS
  **850 MB → ~30 MB** (bounded; OS reclaims transient parse memory). Zero
  analytics-logic change.
- Codex parser cache compacted (198 MB → 52 MB) via meta-hoisting + tuples;
  `UsageRecord` uses `__slots__`.

### Notes
- Rebranding in progress (see NAMING.md) — "burnmeter" is a working title.
- Costs are client-side estimates; for billing see your provider's console.
