# Changelog

## [0.3.4] — 2026-09-06

### Fixed
- **Security — DNS rebinding.** `POST /api/update` treated a custom header as CSRF
  proof. That holds for ordinary cross-origin requests but not against DNS
  rebinding, where the browser makes an attacker's page same-origin and page JS
  can set any header — so a malicious tab could trigger an install and relaunch.
  The same gap left `/api/report`, `/api/records.csv`, `/api/sync/devices` and
  `/api/statusline` readable (project paths, branch names, chat titles, per-session
  cost, and for Pro users the decrypted cross-device snapshot). Every `/api/*`
  request now validates the `Host` header. Serving a LAN deliberately still works;
  `BURNMETER_ALLOWED_HOSTS` covers reverse proxies.
- **Pricing — missing OpenAI tiers.** `gpt-6-astra` was billed at GPT-5.5 rates
  (half its real cost) and the three GPT-5.6 tiers collapsed into one row, which
  reported `gpt-5.6-luna` at roughly 25x its actual price. `gpt-5.5-pro` and
  `gpt-5.4-nano` were also mispriced. Unresolved Codex turns no longer get a
  fabricated `gpt-5` id.
- **First run no longer grades absent data.** A fresh install showed a red
  "Cache hit 0.0% — low" verdict and an orange "Room to tighten per-unit
  efficiency" headline beside "No usage data yet". Zero usage now reads
  "Waiting for your first session".
- **Cost column could scroll out of view.** Long, space-free project names widened
  the projects/commits/models tables past their card. Names now ellipsize.

## [0.3.3] — 2026-09-06

### Fixed
- **1-hour cache writes were billed at the 5-minute rate.** Claude Code writes most
  of its prompt cache with the 1h TTL, which costs 2.0x input versus 1.25x, so real
  spend was under-reported (68.9% of writes in a heavy month). The parse cache
  version was bumped, so the first launch after upgrading does one full re-read.

## [0.3.2] — 2026-09-06

### Fixed
- **Sonnet 5 and Fable 5.1 were billed at their predecessors' rates.** Prices were
  looked up by family name, so a newer model in a known family inherited the older
  rate: Sonnet 5 was charged Sonnet 4.x rates (+50%) and Fable 5.1 cache reads were
  charged 4x. Price rows are now per version, verified against the providers' live
  pages.

### Added
- **Honest pricing coverage.** A model whose version isn't in the table is still
  costed at family rates, but the models card now names it, shows the spend
  involved, and dates the last price check. Models with no match are counted in
  tokens and excluded from cost — also named, instead of silently vanishing.

## [0.3.1] — 2026-07-10

### Fixed
- **Live data froze after long uptime.** One hung request could wedge the refresh
  loop indefinitely; the app looked alive while every number stood still until the
  user clicked something. Requests now time out, a stuck poll is taken over, and
  the app refreshes on wake-from-sleep, screen unlock, window focus and network
  reconnect. macOS: opted out of App Nap.
- Narrow-window topbar jitter (fixed-height header).

## [0.3.0] — 2026-07-08 — "Doors"

### Added
- **Chats view** — sessions by name, with cost and recency.
- **Alerts UI** — configure Slack / webhook / email destinations and send a test.
- **Insights** — anomalies and cost-per-commit.

### Fixed
- The Chats nav button silently did nothing (section list is now derived from the DOM).

## [0.2.1] — 2026-07-07

### Added
- Sidebar account chip (sign-in state, plan, devices).

### Fixed
- Black window on cold boot; instant first paint from cache.

## [0.2.0] — 2026-07-07 — Electron desktop app

### Changed
- **The Windows app is now an Electron shell** over the same local Python server:
  native window chrome, single instance, tray lifecycle, remembered window state,
  one-click NSIS installer. macOS remains the pip + pywebview build for now.

## [Unreleased] — 2026-05-30 (commercial transform)

### Added
- **Multi-agent**: OpenAI Codex source alongside Claude Code, with a header
  toggle (Claude Code ↔ Codex). Same dashboard, source-aware data + branding.
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
- Rebranded ccmeter → **Burnmeter** — vendor-neutral (tracks Claude Code + OpenAI Codex, more agents planned). See NAMING.md.
- Costs are client-side estimates; for billing see your provider's console.
