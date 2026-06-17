# Hosting the Burnmeter Pro sync relay

This runs the **hosted** side of Pro cross-device sync: a tiny zero-knowledge relay
that stores per-device **ciphertext** snapshots and hands them back to the same
account's other devices. It never sees your passphrase or plaintext usage — the
relay can only store and return encrypted blobs.

Two audiences use this doc:
- **Self-hosters** — run your own relay and get full Pro sync for **$0** (the relay
  is AGPL). Skip the billing bits; the default treats every token as Pro.
- **Hosted operator (us)** — run the managed relay at `sync.burnmeter.dev` with a
  billing-fed allowlist so only paying Pro keys can sync.

> The relay speaks **plain HTTP** and must run behind a TLS reverse proxy
> (Caddy/Traefik). Never expose it directly.

---

## 0. Prereqs

- A small VPS — e.g. Hetzner CX22 (~€4/mo), any Ubuntu/Debian. 256 KB/snapshot, a
  handful of devices per account → tiny.
- Python 3.10+.
- A DNS record `sync.burnmeter.dev` → the VPS IP. On Cloudflare, set it **DNS-only
  (grey cloud)** so Caddy can fetch a Let's Encrypt cert over HTTP-01. (Orange-cloud
  works only with a CF Origin cert or Caddy's DNS-01 challenge — keep it simple.)

## 1. Install on the VPS

```bash
adduser --system --group --home /var/lib/burnmeter-relay burnmeter
pipx install burnmeter            # or: pip install burnmeter  (sync relay needs no extras)
# (the relay is pure stdlib; the [sync] extra/cryptography is only for CLIENTS)
```

## 2. Storage

```bash
install -d -o burnmeter -g burnmeter -m 700 /var/lib/burnmeter-relay
```

The relay manages `accounts.json` (customer records: email, plan, status, **hashed**
token) inside the storage dir — you don't pre-create it. Keep the dir user-only
(`700`): it holds customer emails. Raw tokens are **never** stored (only their hash),
so a leak of `accounts.json` can't impersonate a customer.

- **Self-host:** do nothing here — every token is Pro by default.
- **Hosted:** provision customers in §5 (the `/admin` console or the CLI).

## 3. systemd unit + env

`/etc/burnmeter/relay.env` (chmod 600 — may hold the future webhook secret):

```ini
# Authoritative accounts mode: enables the /admin console AND makes accounts.json the
# source of truth (unknown tokens get no sync). Generate a long random admin key:
#   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
BURNMETER_RELAY_ADMIN_TOKEN=paste-a-long-random-key-here
# PEPPER: a second long random secret. The relay stores HMAC(pepper, auth_secret) as the
# account verifier, so a stolen accounts.json ALONE can't be brute-forced — an attacker
# would also need this pepper. Keep it ONLY here (env / systemd EnvironmentFile), never in
# the storage dir or a backup. Generate: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
BURNMETER_RELAY_PEPPER=paste-another-long-random-key-here
# Behind a reverse proxy every request shares the proxy's IP, so raise the IP-keyed
# rate caps (per-token limits are unaffected):
BURNMETER_RELAY_RATE=240
BURNMETER_RELAY_RATE_REFILL=4
# Legacy alternative (anonymous token->plan allowlist instead of accounts):
#   BURNMETER_RELAY_BILLING=1
#   BURNMETER_RELAY_PLANS=/etc/burnmeter/plans.json
```

> Self-hosters: omit `BURNMETER_RELAY_ADMIN_TOKEN` entirely → no `/admin`, every token is Pro.

`/etc/systemd/system/burnmeter-relay.service`:

```ini
[Unit]
Description=Burnmeter sync relay
After=network-online.target

[Service]
User=burnmeter
Group=burnmeter
EnvironmentFile=/etc/burnmeter/relay.env
ExecStart=/usr/bin/burnmeter sync-relay --host 127.0.0.1 --port 8899 --storage /var/lib/burnmeter-relay
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/burnmeter-relay

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now burnmeter-relay
journalctl -u burnmeter-relay -f          # should log: mode HOSTED billing · rate 60/1/s
```

## 4. Caddy (auto-TLS)

`/etc/caddy/Caddyfile`:

```caddy
sync.burnmeter.dev {
    reverse_proxy 127.0.0.1:8899
}
```

```bash
systemctl reload caddy
curl https://sync.burnmeter.dev/health     # -> {"ok": true, "service": "burnmeter-sync-relay"}
```

## 5. Provision a Pro customer (manual — payment-last)

Two ways, both provider-agnostic. The future payment webhook (§8) calls the SAME
core, so nothing here is throwaway.

**A) The `/admin` console.** Open `https://sync.burnmeter.dev/admin`, paste your
`BURNMETER_RELAY_ADMIN_TOKEN`, enter email + plan, **Create**. The full account token
is shown **once** — copy it and email it to the customer. The table lists every
customer (email, plan, status, devices, last sync) with Revoke / Reactivate.

**B) The CLI** (on the relay box):

```bash
sudo -u burnmeter burnmeter relay-account create \
    --email customer@example.com --plan pro \
    --storage /var/lib/burnmeter-relay
#   ↳ prints: token: bm_xxxxxxxx…   (email this to the customer — shown once)
sudo -u burnmeter burnmeter relay-account list   --storage /var/lib/burnmeter-relay
sudo -u burnmeter burnmeter relay-account revoke --id <id> --storage /var/lib/burnmeter-relay
```

Unknown / revoked tokens get `device_limit 0` → no sync (HTTP 402). The raw token is
shown only at creation and is never stored in recoverable form (only its hash).

> **Seat = person, not device.** For a Team org, set `--device-limit` to roughly
> 2× the seat count (people often run 2 machines).

## 6. Client side (what the customer runs on EACH machine)

```bash
pip install "burnmeter[sync]"
burnmeter sync login --relay https://sync.burnmeter.dev --token <THEIR-KEY>
#   ↳ prompts for an E2E passphrase — SAME on all their machines
burnmeter serve          # auto-pushes every ~5 min; the dashboard's "Cihazlar"
                         # card shows all their devices, decrypted locally
```

## 7. Backups + ops

```bash
# nightly ciphertext backup (zero-knowledge — only encrypted blobs)
0 3 * * *  restic -r <repo> backup /var/lib/burnmeter-relay
```

- **Privacy model (be honest in marketing):** the relay only ever stores **ciphertext**
  of usage + account metadata (email, plan). It **cannot read your usage** — the E2E key
  is derived from the customer's password on their device and never sent. Caveat to state
  plainly: against a *breached* relay (someone steals accounts.json **and** the pepper),
  usage secrecy is then "as strong as the customer's password" — which is why the client
  enforces a 10+ char password and uses a slow, memory-hard KDF (scrypt n=2¹⁷). The
  pepper means a file-only leak is not enough.
- **Known hardening TODO (pre-scale):** per-account random salt (vs the current
  email-derived salt) and per-email login/activation attempt caps. The activation code is
  ~60 bits + one-time; login is throttled per-IP (see below).
- **Rate limiting** is per-token (and per-IP for `/health`, `/v1/login`, `/v1/activate`).
  Behind Caddy the per-IP key sees the proxy IP, so it becomes one shared bucket — raise
  the caps and, if you need true per-client limits, have Caddy pass `X-Forwarded-For` and
  extend the relay to read it. **Token keying is the real protection** for sync.
- The relay holds **ciphertext only** — a compromise leaks encrypted blobs, not usage.
- Treat account keys as bearer secrets. `device_limit` and plan come from `plans.json`.

## 8. Later: automate billing (payment phase)

Replace manual provisioning with a small webhook that fires on a paid order and calls
the **same** core the `/admin` console and `relay-account create` use:
`burnmeter.sync_relay.create_account(storage, email=..., plan="pro", source="<provider>")`
(or POST `/admin/api/accounts` with the admin token). Verify the provider's webhook
signature first. The relay hot-reloads `accounts.json`. That webhook + key delivery is
the **only closed-source piece**; this relay stays public AGPL.

> **Provider (TBD by Cihan).** For a Turkey-based seller, Lemon Squeezy's bank payout
> to TR is unconfirmed and PayPal doesn't operate there. The realistic Merchant-of-
> Record options are **Polar.sh** (pays TRY to a Turkish bank, native license keys +
> webhooks) or **Paddle** ($15 SWIFT). Whichever is chosen, it only needs to call
> `create_account` on purchase and flip status on cancel/refund.
