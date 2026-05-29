# Pricing model — Obsidian-style (free OSS core + paid services)

**Principle:** never paywall *capability* (multi-agent support, the local
dashboard). Paywall *convenience that needs infrastructure the local app can't
provide alone* — sync, alerts, team aggregation. This is the Obsidian/Raycast
lesson: the free tier must be genuinely excellent; it's the distribution + trust
engine. All direct competitors (ccusage, CodexBar, tokscale) are free — so the
free tier is table stakes, and the paid tier must earn its price on services.

## Free — OSS core (forever, AGPL-3.0)
- Real-time local web dashboard (all panels)
- **Every** supported agent (Claude Code, Codex, + future) — never gated
- Per-session / daily / per-project / per-model breakdowns
- Rate-limit hero (5h + weekly), burn-rate, cost projection
- Cache-efficiency panel
- Last 30 days of local history (on disk, user-controlled)
- CSV/JSON export
- Config-file alert rules (self-hosted)

## Pro — ~$7/mo or $60/yr (launch: $99 one-time "lifetime/supporter")
*For the individual power user across multiple machines.*
- **Sync** — E2E-encrypted usage history across all your machines (the Obsidian
  Sync analog; we already prototype this with Syncthing — Pro productizes it)
- **Unlimited history** — beyond the 30-day local window (provider logs get pruned)
- **Smart alerts** — Slack / email / webhook push at custom thresholds
  ("90% of weekly cap", "this block exhausts in 20 min")
- **Auto project attribution** — map spend to git repos/branches
- **Budget envelopes** — per-project / per-agent monthly caps
- Priority support + early access

*Anchoring: Obsidian Sync $4, Raycast Pro $8 → $7 sits right between.*

## Team — ~$12/seat/mo (min 3 seats)
*For the eng manager who expenses $200 Max/Pro seats and needs oversight.*
- Everything in Pro
- **Team dashboard** — aggregated cost across members (metrics only, never code/content)
- **Cost-attribution reports** — per-dev / per-project / per-sprint, CSV export
- **Admin** — team-wide budget caps, alert routing
- **SSO / SAML** (~10+ seats), invoice billing

*Gap we fill: Anthropic's Enterprise Analytics API only serves Enterprise orgs —
small teams on individual Max/Pro seats have no aggregation tool. That's us.*

## What NOT to gate
Multi-agent support and the local dashboard. Those are the reason people install.
Gate sync / alerts / team views — convenience, not capability.
