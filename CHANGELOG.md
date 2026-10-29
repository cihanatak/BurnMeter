# Changelog

## [0.3.6] — 2026-10-30

### Fixed
- **One-click update now runs the installer.** In 0.3.5 the Update button closed
  Burnmeter and nothing else happened. The update command reached Windows' cmd with
  its quotes escaped, so cmd could find neither the installer nor the app to reopen.
  The fix ships in this version, so 0.3.5 has to install 0.3.6 once by hand: run
  `BurnmeterSetup.exe` from this release. Updates from 0.3.6 on are one click.
- **The Chats page reaches every chat.** It stopped at 60 rows with no way to see the
  rest (60 of 195 on the maker's PC). It now shows 60 and a "Show all" button, and
  "Most $" ranks every chat, not only the newest 200. The table also fills the window:
  a later style rule held it to 320 px, about four rows. Costs of $10,000 and up are no
  longer cut off ("$10,4…").
- **The live card shows every chat that's running.** It had one row per model, so four
  chats on Opus 5.5 made one row, named after whichever chat wrote last and costed at
  all four. It's now "Live chats", with a row and a rate for each chat.
- **Chat titles are back on Windows.** The Claude desktop app moved its session list to
  `%LOCALAPPDATA%\Claude-Data`, so Chats, Recent activity and the live card showed
  folder names ("repo", "ClaudeWorkspace") instead of the titles in the app. Burnmeter
  reads the new place too.
- **Pro sync says why it stopped.** A refused push or pull used to fail silently. The
  Pro bar now shows when this device last synced, or why it couldn't: no Pro plan,
  an unverified email, the daily quota, the network, or a session that ended.
- **A killed server's build worker kept running** for minutes at 1.2–1.8 GB, and its
  temp files were never deleted. The worker now exits with its server.
- **The gauge's "your typical" label** read "ypical"; it now stays on the canvas.
- **The macOS release** gets its versioned dmg (`Burnmeter-0.3.6.dmg`) as well.

### Changed
- **Pro sync pushes only what changed.** It pushed every 60 s. It now checks every
  10 minutes, pushes a changed snapshot (and one every 6 hours), never twice within
  5 minutes, and doesn't pull while the window is hidden. The device name travels
  inside the encrypted snapshot only. "Online" means synced in the last 25 minutes.
- **Firebase gets a key derived from your Pro password, not the password.** An
  account made before 0.3.6 moves over at its next sign-in. Firebase then signs the
  account's other devices out, and they need 0.3.6 to sign back in.
- **Electron 45, with a hardened shell.** A splash shows while the server starts,
  the server is restarted if it stops, and navigation, permissions and page content
  are locked to the local dashboard. `npm audit` went from 18 findings to 0.

### Added
- **`firestore.rules`**, which keep sync to verified Pro accounts, cap a snapshot at
  256 KiB and allow one update per device every 5 minutes, with 50 tests that run on
  Google's rules engine. They take effect once deployed (`docs/FIRESTORE_RULES.md`).

## [0.3.5] — 2026-10-29

### Fixed
- **Claude 5.5 models were billed at older rates.** Opus 5.5 was priced as Opus 5,
  so its cache reads, the bulk of a Claude Code bill, cost 2.5x. Sonnet 5.5 cache
  reads cost 2x, and Haiku 5.5 was billed at Haiku 4.5 rates (10x). Model ids now
  match exactly. Haiku 5.5 prompts over 100K tokens bill at its higher tier, fast
  mode bills at 2x, and an unknown version is still flagged. On the maker's logs,
  October fell from $16,276 to $8,976.
- **Output tokens were under-counted by a third.** Claude Code logs a response's
  usage as it streams. Only the last line has the full count, and v0.3.4 kept the
  first. The lines of one response are now merged at their highest values. On the
  maker's logs, output tokens rose from 122.8M to 179.2M. With both fixes,
  all-time spend went from $42,141 to $35,447.
- **Days, weeks and months follow the local clock.** "Today" used to reset at UTC
  midnight, which is 03:00 at UTC+3, so it disagreed with the activity heatmap.
- **A device that stopped syncing kept adding to the fleet.** A Mac last synced in
  July still added its July burn rate, "today" and month to "All devices". An old
  snapshot now adds nothing to those three, and its card and chips show "—".
- **One-click update did nothing in the installed app.** It ran
  `burnmeter-server.exe -m burnmeter _update`, which exited at once, and the
  dashboard still said "✓ Updated" after 150 s. The app now downloads and runs the
  release installer for the version the dashboard offered. `/api/health` reports
  progress and failure, and a failed update can be retried from the pill.
- **Scanning another log tree wiped the shared parse cache.** This happened when
  running the tests or passing a second `--projects-dir`, and the next launch then
  re-read every log, which takes minutes on a big history. Entries for other trees
  are now kept, and an unchanged cache is not rewritten. The Codex cache works the
  same way.
- **Tool counts grew with uptime.** A build served from the parse cache added each
  multi-line response's tool calls again, which inflated the per-tool numbers and
  grew the cache file on every rebuild.
- **The Chats "goal" column showed "Tool loaded." or image notes.** Claude Code
  also logs request copies, meta lines and compaction summaries as user messages.
  The goal is now the first message the user typed. The model in use now comes
  first in the models column.
- **A forked or resumed session counted its tool errors twice.** Errors are now
  de-duplicated per tool call.
- **Cache savings** price each call at the rates it actually paid, including fast
  mode and Haiku 5.5's long prompts.
- **Smaller fixes:**
  - Model names keep their minor version ("Opus 5.5", not "Opus 5").
  - Model names and timestamps from synced devices are escaped before they reach
    the page.
  - The dashboard skips its charts instead of breaking when Chart.js can't load.
  - A log line stamped outside the OS clock's range no longer takes down the report.
  - The cache card counts cache writes as input, so its hit rate matches the rest
    of the dashboard. It read 100.0% next to 98.2% on the Insights page.
  - Chat costs and durations are no longer cut off: "$3,33…" now reads "$3,339",
    and a chat resumed over weeks shows its span in days.
  - The pace next to the burn gauge now uses the needle's own measure. It used to
    project a block's first minutes over all 5 hours, and could read "10.4×" next
    to a needle in the yellow.

### Changed
- **Charts work offline.** Chart.js 4.4.7 is now bundled, so the dashboard loads
  nothing from a CDN.
- **The statusline is instant while Burnmeter runs.** It asks the app instead of
  rebuilding the report: 0.5 s, against about 65 s on the maker's history. Without
  the app it reads only the last 26 hours, in about 9 s. It then blanks what
  26 hours can't show: the pace dot (⚪), "P90+", and the block when that day has
  no 5-hour pause. The line is now in English:
  `🟠 Opus 5.5 $4.20/h · block 4h24m · 0 err/24h · cache 97% · today $32`.
- The parse cache version was bumped, so the first launch after upgrading does one
  full re-read.

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
