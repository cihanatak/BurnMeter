# Firestore rules: what makes Pro sync Pro

The Burnmeter client is open source, so any check it makes can be taken out. Pro sync is
enforced by the deployed copy of [`firestore.rules`](../firestore.rules) and nothing else.

## What the rules allow

| Path | Read | Write |
| --- | --- | --- |
| `users/{uid}` (`plan`, `status`) | the owner (get, not list) | nobody: admin credentials only (billing, `scripts/pro_admin.py grant`) |
| `users/{uid}/devices/{id}` | the owner, with a verified email, `plan == "pro"` and `status == "active"` | the same, and the write must pass every check below |
| delete a device doc | | the owner, on any plan |
| anything else | nobody | nobody |

A device write must meet all of these:

- the id is 12 lowercase hex characters (`uuid4().hex[:12]`);
- the fields are `blob` (a string of 1 byte to 256 KiB) and `updated_at` (a timestamp), plus
  `label` (a string of at most 200 characters), which only 0.3.5 and older send;
- `updated_at` is the request time (0.3.6), or within 2 minutes of it (0.3.5, which sends its own clock);
- an update comes more than 5 minutes after the stored `updated_at`.

A missing `users/{uid}` document, or one without `plan`, counts as free. A missing `status` counts as active.

## Tests

[`tests/firestore_rules_cases.json`](../tests/firestore_rules_cases.json) has 50 cases, with
ALLOW and DENY for every operation. Run them on Google's own rules engine through the Rules
API `projects:test` endpoint. It deploys nothing, and it needs no emulator or Java, only a
gcloud login with access to the project:

```bash
python scripts/pro_admin.py rules-test
```

`BURNMETER_RULES_TEST=1 pytest tests/test_firestore_rules.py` runs the same cases inside pytest.
Without that variable, only the offline checks of the case file run.

Results on 2026-10-29:

| Rules | Cases passed | Notes |
| --- | --- | --- |
| These rules | 50/50 | |
| Rules live since 2026-06-17 (ruleset `f302b9d0-…`) | 27/50 | Any verified account could sync, with no check on plan, size, fields, device id or write rate |

## The client side (0.3.6)

**Pushes.** They go through `firebase_sync.AutoPush`:

- it looks for a change every 10 minutes;
- it pushes only when the snapshot changed (`generated_at` aside), plus a heartbeat every 6 hours;
- it never pushes within 5 min 10 s of the last push. Push now and a rename are queued until then.

**The write.** It is a `documents:commit` that sends only `blob`. Firestore itself stamps
`updated_at` with `REQUEST_TIME`, so a wrong local clock can't get a write refused. The
device name travels inside the ciphertext.

**Refusals.** The dashboard says why a request was refused: the plan, an unverified email,
the daily quota, the network, or a session that ended. After a plan or quota refusal the
client waits an hour before trying again.

**Caches.**

| What | How long it is cached |
| --- | --- |
| Pulls | 5 min (30 min while the plan or the quota is the problem) |
| `/api/pro/status` | 10 min |

A hidden window doesn't pull at all.

**The auth key.** Firebase Auth gets `derive_auth_secret(email, password)`, not the password.
An account made before 0.3.6 moves over at its next sign-in:

1. Firebase refuses the derived secret.
2. The password itself works.
3. `accounts:update` replaces the password with the derived secret.

Firebase then revokes the account's other sessions:

- on 0.3.6, those devices say "signed out, sign in again";
- 0.3.5 can't sign in to a migrated account and stops syncing until it is updated.

The password and the E2E key stay the same, so the synced snapshots stay readable.

## Quota

The free Spark plan has 20,000 writes and 50,000 reads a day, shared by every account:

| Per device or dashboard | 0.3.5 | 0.3.6 with these rules |
| --- | --- | --- |
| Writes per device per day | 1,440 (one every 60 s) | at most 144 while busy, 4 when idle |
| Reads per write | 0 | 1 (the rules look up the plan) |
| Status reads per open dashboard per day | up to about 11,500 | 144 |
| Pulls per open dashboard per day (each reads every device doc) | up to 4,320 | 288, none while hidden |

Before 0.3.6, about 14 devices pushing all day would use up the write quota. Beyond a few
dozen devices that are busy all day, the next step is the Blaze plan. That is a billing
decision.

## Deploy

**Before you start:**

1. **Grant Pro.** Every account that should keep syncing needs `plan: "pro"`. On 2026-10-29
   there was one account, and it had Pro.
   - `python scripts/pro_admin.py stats` shows the counts only.
   - `python scripts/pro_admin.py grant <email> --yes` grants Pro to another account.
2. **Update the devices.** Ideally, install 0.3.6 on your devices first. 0.3.5 keeps syncing
   under these rules, but most of its once-a-minute pushes are refused. That is harmless,
   but it wastes requests.
3. **Fix the sign-up copy.** The sign-up form says "free while billing is in beta". Once these
   rules are live, a new account syncs only after a grant, so that text has to change.

**Steps:**

```bash
python scripts/pro_admin.py rules-test
```

```bash
python scripts/pro_admin.py rules-deploy
```

```bash
python scripts/pro_admin.py rules-deploy --yes
```

```bash
python scripts/pro_admin.py rules-show
```

1. `rules-test` should report 50/50.
2. `rules-deploy` without `--yes` is a dry run. It tests again and prints the live ruleset id,
   which you need for a rollback.
3. `rules-deploy --yes` uploads the rules and makes them live.
4. `rules-show` prints the live ruleset, so you can confirm the switch.

**After deploying,** check that the dashboard's Pro bar reads "synced N min ago" with no
warning.

**Other ways to deploy:**

- In the Firebase console: Firestore → Rules, paste `firestore.rules`, then Publish.
- With the Firebase CLI: `firebase deploy --only firestore:rules --project burnmeter-4e5f3`.
  `firebase.json` points the CLI at the file.

## Rollback

The ruleset that was live before this change is
`projects/burnmeter-4e5f3/rulesets/f302b9d0-39b5-449f-ac09-2128ea0636ab`, from 2026-06-17.

```bash
python scripts/pro_admin.py rules-rollback f302b9d0-39b5-449f-ac09-2128ea0636ab
```

```bash
python scripts/pro_admin.py rules-rollback f302b9d0-39b5-449f-ac09-2128ea0636ab --yes
```

The first command is a dry run; the second switches the live rules back. In the console, open
Firestore → Rules, pick the 2026-06-17 version in the history and publish it. The old rules
apply within about a minute. A rollback needs no client change, because 0.3.6 also works
under the old rules.

## Known limits

- **Devices per account.** The rules can't count documents, so the number of devices per account
  isn't capped. A Pro account could create many device docs, each limited to one update every
  5 minutes. To stop an account, suspend it:
  `python scripts/pro_admin.py grant <email> --status suspended --yes`.
- **Creating a device doc.** A create has no rate limit, because there is no stored time to compare
  against. A Pro owner can also delete a device doc and create it again to skip the wait.
- **The 0.3.5 allowances.** The own clock ±2 min and the plaintext `label` should go once no
  0.3.5 is left. Then `stampedNow()` becomes `request.resource.data.updated_at == request.time`
  and `label` leaves `hasOnly`. Docs written by 0.3.5 keep their plaintext label until 0.3.6
  rewrites them.
- **Permissions.** `scripts/pro_admin.py` runs with your gcloud login, which must be allowed to
  test and release rules and to read and write Firestore and Auth for the project (an owner can).
