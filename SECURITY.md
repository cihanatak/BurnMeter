# Security policy

## Reporting a vulnerability

Please **don't open a public issue** for security problems. Report them
privately through GitHub:

**[Report a vulnerability](https://github.com/cihanatak/BurnMeter/security/advisories/new)**
(or the *Security* tab → *Report a vulnerability*).

Include what you found, how to reproduce it, which version and OS you used, and
what an attacker could do with it. A proof of concept helps.

Burnmeter is maintained by one person, so responses are best-effort. You'll get
a reply in the advisory thread, and fixes ship as a new release with credit to
the reporter unless you'd rather stay anonymous.

## Supported versions

Only the latest release gets security fixes. Check
[Releases](https://github.com/cihanatak/BurnMeter/releases) and update before
reporting if you can.

## What's in scope

- The local server (`burnmeter serve`, default `127.0.0.1:7654`): anything that
  lets another website, another user on the machine, or the network read your
  usage data or trigger actions such as `/api/update`.
- The Windows installer and the in-app updater.
- Pro sync: the end-to-end encryption, and access control on the stored
  snapshots.
- Alerts: how webhook, Slack and email settings are stored and sent.

Not a vulnerability:

- The Firebase web config in `burnmeter/firebase_config.py`. A Firebase web API
  key identifies the project and is public by design; access is enforced by
  Firebase Auth and Firestore security rules.
- Windows SmartScreen or macOS Gatekeeper warnings. Release builds are not code
  signed yet; this is known.
