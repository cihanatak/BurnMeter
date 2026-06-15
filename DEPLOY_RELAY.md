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

## 2. Storage + plan-store

```bash
install -d -o burnmeter -g burnmeter -m 700 /var/lib/burnmeter-relay
install -d -m 750 /etc/burnmeter
echo '{}' > /etc/burnmeter/plans.json        # token -> {plan, device_limit}
chmod 640 /etc/burnmeter/plans.json
```

- **Self-host:** leave billing off (next step) and you can ignore `plans.json`.
- **Hosted:** `plans.json` is the allowlist. Hot-reloaded by mtime — edit it and the
  relay picks it up live, no restart.

## 3. systemd unit + env

`/etc/burnmeter/relay.env` (chmod 600 — may hold the future webhook secret):

```ini
BURNMETER_RELAY_BILLING=1
BURNMETER_RELAY_PLANS=/etc/burnmeter/plans.json
BURNMETER_RELAY_RATE=60
BURNMETER_RELAY_RATE_REFILL=1
```

> Self-hosters: omit `BURNMETER_RELAY_BILLING` entirely → every token is Pro.

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

Until the Lemon Squeezy webhook is wired, provisioning is one line.

```bash
# mint a high-entropy account key
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
# -> e.g. 7sN2...   (this is the customer's Pro key)
```

Add it to the allowlist (the relay hot-reloads):

```json
{
  "7sN2_customerKeyHere": { "plan": "pro", "device_limit": 10 }
}
```

Email the key to the customer. Unknown keys get `device_limit: 0` → no sync (HTTP 402).

> **Seat = person, not device.** For a Team org, set `device_limit` to roughly
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

- **Rate limiting** is per-token (and per-IP for `/health`). Behind Caddy the per-IP
  key sees the proxy IP, so **token keying is the real protection**; if you need true
  per-client IP limits, have Caddy pass `X-Forwarded-For` and extend the relay to read it.
- The relay holds **ciphertext only** — a compromise leaks encrypted blobs, not usage.
- Treat account keys as bearer secrets. `device_limit` and plan come from `plans.json`.

## 8. Later: automate billing (payment phase)

Replace manual `plans.json` edits with a tiny **private** webhook service: a Lemon
Squeezy `order_created`/`subscription_*` webhook (verify the HMAC signature) writes
`{ key: { plan, device_limit } }` into `plans.json`. The relay hot-reloads it. That
webhook + the key-issuance flow is the **only closed-source piece**; this relay stays
public AGPL.
