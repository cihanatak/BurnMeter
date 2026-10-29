# Pricing

Burnmeter is open-core: the local app is free and open source (AGPL-3.0), and
paid options cover what one machine can't do alone, or a different license.

**None of the paid options are on sale yet.** The prices below are the planned
launch prices.

## The rule

Capability is never paywalled. The local dashboard and every supported agent
stay free. Paid options sell services that need infrastructure (hosted sync, a
team server) or a license other than the AGPL.

## Free: the open-source app

**$0, AGPL-3.0-or-later.**

- The full local dashboard, every panel
- Every supported agent: Claude Code, Codex and future ones, never gated
- Burn rate against your own history, and cost projections
- Codex plan limits (5-hour and weekly) where Codex logs them
- Breakdowns per chat, day, project, model and git commit
- Cache efficiency and savings
- Monthly budget with a month-end projection
- Alerts to Slack, a webhook or email, sent from your machine
- Terminal statusline and CSV export
- History as far back as your agents' logs on disk go (Codex: the last 90 days
  by default)

## Pro: $6/month (planned)

For people who use more than one machine.

- **Hosted sync:** each machine's usage summary, end-to-end encrypted, in one
  Devices view. What leaves your machine is described in the README's
  [privacy section](./README.md#privacy-what-stays-local-and-what-goes-out).
- Priority support and early access

Planned for Pro but **not built yet**: durable history that outlives the
agents' own log pruning (today a sync snapshot carries only the last 14 days of
daily cost).

## Supporter: $99 one-time (planned)

A way to back development. What it includes will be announced at launch.

## Commercial license: $49/year (planned)

For organizations that can't or don't want to use Burnmeter under the AGPL. The
AGPL already allows commercial use; this license is the alternative for teams
whose policy rules out AGPL code. See [`LICENSING.md`](./LICENSING.md).

## Team: $12/seat/month, minimum 3 seats (planned, not built)

For engineering leads who pay for several Claude or ChatGPT seats and want one
view of the cost.

- Team dashboard: aggregated cost across members (metrics only, never code or
  chat content)
- Cost-attribution reports per developer, project and sprint, with CSV export
- Admin: team budget caps and alert routing
- SSO / SAML from about 10 seats, invoice billing

Team needs a server that combines members' usage, so unlike the local app and
Pro sync, Team data would be processed on that server.
