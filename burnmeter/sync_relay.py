"""Burnmeter Pro — self-hostable sync relay.

A tiny zero-knowledge blob store: it holds per-device **ciphertext** snapshots and
hands them back to the same account's other devices. It never sees your passphrase
or plaintext usage. Run it on a small box (your own server / Tailscale node / VPS):

    burnmeter sync-relay --port 8899 --storage ~/.burnmeter-relay

Auth is a per-account Bearer token (any opaque string the account's devices share).

Two modes (one codebase):
  - SELF-HOST (default): every token is Pro — `_plan_for` returns pro/10 devices.
    The free, AGPL, "run your own relay for $0" path.
  - HOSTED billing (`BURNMETER_RELAY_BILLING=1`): `_plan_for` reads a plan-store
    JSON at `BURNMETER_RELAY_PLANS` ({token: {plan, device_limit}}); unknown tokens
    get no sync (device_limit 0). A Lemon Squeezy webhook (later, payment-last) just
    writes that JSON — the relay hot-reloads it by mtime. The webhook/billing wiring
    is the ONLY closed piece; this relay stays public AGPL.

Hardening for multi-tenant hosting (stdlib only): per-token + per-IP token-bucket
rate limiting (429 on abuse), Bearer-token validation, 256 KB blob cap. Still run
it behind a TLS reverse proxy (Caddy/Traefik) — it speaks plain HTTP.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import unicodedata
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BLOB = 256 * 1024   # 256 KB — snapshots are tiny; reject anything suspicious
MAX_TOKEN = 512         # reject absurd Bearer strings early

# Per-key token-bucket rate limit (requests). Defaults suit a client that pushes
# every ~5 min; tune via env for a busy hosted relay.
RATE_CAPACITY = int(os.environ.get("BURNMETER_RELAY_RATE", "60"))
RATE_REFILL = float(os.environ.get("BURNMETER_RELAY_RATE_REFILL", "1"))   # tokens/sec
_buckets: dict = {}
_buckets_lock = threading.Lock()

# Hot-reloaded plan-store cache (hosted mode).
_plans_cache: dict = {"path": None, "mtime": -1.0, "data": {}}


def _account_dir_hash(token: str) -> str:
    return hashlib.sha256(("burnmeter-acct:" + token).encode()).hexdigest()[:24]


def _account_dir(storage, token: str) -> Path:
    return Path(storage) / _account_dir_hash(token)


def _valid_token(t: str) -> bool:
    # Opaque Bearer string: non-empty, bounded, printable ASCII, no whitespace.
    return bool(t) and len(t) <= MAX_TOKEN and t.isascii() and t.isprintable() and not any(c.isspace() for c in t)


def _rl_key(token: str) -> str:
    # Never key the bucket by the raw token.
    return "tok:" + hashlib.sha256(token.encode()).hexdigest()[:16]


def _rate_ok(key: str) -> bool:
    now = time.monotonic()
    with _buckets_lock:
        tokens, last = _buckets.get(key, (float(RATE_CAPACITY), now))
        tokens = min(float(RATE_CAPACITY), tokens + (now - last) * RATE_REFILL)
        if tokens < 1.0:
            _buckets[key] = (tokens, now)
            return False
        _buckets[key] = (tokens - 1.0, now)
        return True


def _load_plans() -> dict:
    """Hosted mode plan-store: {token: {plan, device_limit}}. Hot-reloaded by mtime
    so a billing webhook can rewrite the file and the relay picks it up live."""
    path = os.environ.get("BURNMETER_RELAY_PLANS")
    if not path:
        return {}
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return {}
    if _plans_cache["path"] != path or _plans_cache["mtime"] != mtime:
        try:
            _plans_cache["data"] = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _plans_cache["data"] = {}
        _plans_cache["path"] = path
        _plans_cache["mtime"] = mtime
    return _plans_cache["data"]


# ---- accounts store (the identity layer over the bare plan-store) -------------
# An "account" is a CUSTOMER RECORD: who bought, which plan, what status. It
# supersedes the anonymous plans.json (token->plan) by adding identity (email) +
# lifecycle, so the operator can SEE who is a customer (the /admin screen) and so
# a future payment webhook can create accounts the SAME way `relay-account create`
# does (one shared create_account()).
#
# PRIVACY INVARIANT: this stores ACCOUNT METADATA ONLY (email, plan, status,
# timestamps). It never stores or exposes decrypted usage — the relay still cannot
# read usage (it has no passphrase). Admin endpoints surface only this metadata
# plus device counts / last-sync timestamps, never plaintext usage.
_DEFAULT_LIMITS = {"pro": 5, "team": 25, "supporter": 5, "license": 0, "free": 0}
_VALID_PLANS = set(_DEFAULT_LIMITS)
_VALID_STATUS = {"active", "canceled", "expired"}
# device_id is used as a filename — whitelist strictly (no slashes/backslashes/dots/
# colons) so a crafted path segment can never escape the account dir. (Windows treats
# backslashes as separators, and the raw URL path is NOT decoded by the stdlib server.)
_DEV_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Cache identity is (st_mtime_ns, st_size): coarse-mtime filesystems (FAT/exFAT/SMB)
# would otherwise miss a same-second edit; size catches list growth/shrink.
_accounts_cache: dict = {"path": None, "id": None, "data": {}}
_accounts_lock = threading.Lock()          # guards _accounts_cache
_accounts_write_lock = threading.Lock()    # serialises load->mutate->write (no lost updates)


def _accounts_path(storage) -> Path:
    override = os.environ.get("BURNMETER_RELAY_ACCOUNTS")
    return Path(override) if override else (Path(storage) / "accounts.json")


def _default_limit(plan: str) -> int:
    return _DEFAULT_LIMITS.get(plan, 5)


def _coerce_limit(value, plan: str) -> int:
    """Never trust device_limit from JSON / a hand-edited or webhook-written file."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return _default_limit(plan)


def _hash_token(token: str) -> str:
    """Stable hash to look up an account WITHOUT storing the raw token at rest."""
    return hashlib.sha256(("burnmeter-acct-tok:" + token).encode()).hexdigest()


def _verifier(auth_secret: str) -> str:
    """Server-side verifier stored in accounts.json. With a PEPPER (BURNMETER_RELAY_PEPPER,
    an env secret kept OUTSIDE the data dir) a stolen accounts.json alone is useless — an
    attacker would also need the pepper to crack passwords offline. Without a pepper
    (self-host default) it falls back to the plain hash. Set the pepper on a hosted relay."""
    pepper = os.environ.get("BURNMETER_RELAY_PEPPER")
    if pepper:
        return "hmac:" + hmac.new(pepper.encode(), auth_secret.encode(), hashlib.sha256).hexdigest()
    return _hash_token(auth_secret)


def _accounts_strict() -> bool:
    """Accounts store is AUTHORITATIVE (unknown token => denied) only when the operator
    explicitly runs a hosted/admin relay. Otherwise it's ADDITIVE over the self-host
    default, so a casual self-hoster who creates one account doesn't lock out their
    whole fleet."""
    return bool(os.environ.get("BURNMETER_RELAY_ADMIN_TOKEN")
                or os.environ.get("BURNMETER_RELAY_ACCOUNTS"))


def _load_accounts(storage) -> dict:
    """Return {account_id: account_record}, hot-reloaded by (mtime_ns, size). Thread-
    safe. Returns the cached dict by reference — callers MUST treat it read-only.
    Keyed by id (always present) — an account exists BEFORE it has an auth hash
    (i.e. before the customer activates with a password)."""
    path = _accounts_path(storage)
    try:
        st = path.stat()
        ident = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    with _accounts_lock:
        if _accounts_cache["path"] != str(path) or _accounts_cache["id"] != ident:
            data = {}
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                for rec in raw.get("accounts", []):
                    rid = rec.get("id")
                    if rid:
                        data[rid] = rec
            except (OSError, json.JSONDecodeError):
                data = {}
            _accounts_cache.update({"path": str(path), "id": ident, "data": data})
        return _accounts_cache["data"]


def _write_accounts(storage, accounts: dict) -> None:
    path = _accounts_path(storage)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Temp beside the target (same volume → atomic replace), created 0600 up front so
    # customer emails are never briefly world-readable. (chmod is a no-op on Windows;
    # keep the storage dir in a user-only location there.)
    tmp = path.parent / (path.name + ".tmp")
    payload = json.dumps({"accounts": list(accounts.values())}, indent=2)
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(str(tmp), str(path))         # atomic swap
    try:
        path.chmod(0o600)
    except OSError:
        pass
    # Invalidate the cache so the next read re-loads from the file (the source of
    # truth). We DON'T blank "data" — a concurrent reader keeps the last-good view
    # until its next stat-triggered reload, never an empty one.
    with _accounts_lock:
        _accounts_cache["path"] = None
        _accounts_cache["id"] = None


_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no I/O/0/1 confusion


def _gen_activation_code() -> str:
    """Short, unambiguous, one-time code: XXXX-XXXX-XXXX (~60 bits — infeasible to guess
    online even with a shared rate-limit bucket)."""
    return "-".join("".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))
                    for _ in range(3))


def _normalize_email(e) -> str:
    # NFKC so the operator-entered and customer-entered forms ALWAYS converge — the
    # client derives its scrypt salt from this same normalization, so any mismatch would
    # silently break login.
    return unicodedata.normalize("NFKC", (e or "")).strip().lower()


def _find_email_key(accounts: dict, email: str):
    email = _normalize_email(email)
    for k, rec in accounts.items():
        if _normalize_email(rec.get("email")) == email:
            return k
    return None


def _by_auth_hash(accounts: dict, h: str):
    for rec in accounts.values():
        if rec.get("token_hash") == h:
            return rec
    return None


def create_account(storage, email: str, plan: str = "pro", device_limit=None,
                   name: str = "", source: str = "manual", note: str = "") -> dict:
    """Create a customer account (email + plan). The customer ACTIVATES it on their
    first device with the returned one-time `activation_code` + a password they choose;
    after that they sign in with email + password on every device. No long token is
    ever shown. The future payment webhook calls THIS on a paid order.

    One account per email (re-create just refreshes the code). Returns the public
    record PLUS the one-time activation_code — surface it ONCE."""
    plan = plan if plan in _VALID_PLANS else "pro"
    email = _normalize_email(email)
    code = _gen_activation_code()
    rec = {
        "id": secrets.token_hex(8),         # non-secret stable handle
        "email": email,
        "name": (name or "").strip(),
        "plan": plan,
        "status": "active",
        "device_limit": _coerce_limit(device_limit, plan) if device_limit is not None else _default_limit(plan),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "note": note or "",
        # set at activation (email + password):
        "activation_code": code,
        "token_hash": None,                 # = sha256(password-derived auth_secret)
        "dir_hash": None,                   # where this account's ciphertext lives
        "activated_at": None,
    }
    with _accounts_write_lock:
        accounts = dict(_load_accounts(storage))
        prev = _find_email_key(accounts, email)
        if prev:
            old = dict(accounts[prev])
            if old.get("token_hash"):
                # ALREADY ACTIVATED → update in place (idempotent for webhook retries /
                # renewals). NEVER reset auth/dir/activation — that would lock the paying
                # customer out of every device and orphan their ciphertext.
                old["plan"] = plan
                old["device_limit"] = rec["device_limit"]
                old["status"] = "active"
                if name:
                    old["name"] = rec["name"]
                if note:
                    old["note"] = rec["note"]
                accounts[prev] = old
                _write_accounts(storage, accounts)
                return {**_public_record(old), "activation_code": None,
                        "message": "already activated — updated in place"}
            accounts.pop(prev, None)        # not yet activated → replace (refresh the code)
        accounts[rec["id"]] = rec
        _write_accounts(storage, accounts)
    return {**_public_record(rec), "activation_code": code}


def activate_account(storage, email: str, code: str, auth_secret: str) -> dict:
    """First-device claim: verify the one-time code, then bind the account to the
    password-derived auth_secret (stored only as a hash) and switch to email+password
    login. Returns {ok, plan, device_limit} or {ok: False, error}."""
    email = _normalize_email(email)
    # ONE generic failure for every reject path → no account-existence / lifecycle leak.
    generic = {"ok": False, "error": "invalid email or activation code"}
    with _accounts_write_lock:
        accounts = dict(_load_accounts(storage))
        key = _find_email_key(accounts, email)
        if not key:
            return generic
        rec = dict(accounts[key])
        if rec.get("token_hash"):
            # Already activated: if it's the SAME credentials (a 2nd device that used
            # activate instead of login), treat as success — else generic.
            if rec.get("status") == "active" and \
               hmac.compare_digest(rec["token_hash"], _verifier(auth_secret)):
                return {"ok": True, "plan": rec["plan"], "device_limit": rec["device_limit"]}
            return generic
        if rec.get("status") != "active":
            return generic
        want = rec.get("activation_code") or ""
        got = str(code or "").strip().upper()
        if not want or not hmac.compare_digest(got, want):
            return generic
        rec["token_hash"] = _verifier(auth_secret)
        rec["dir_hash"] = _account_dir_hash(auth_secret)
        rec["activation_code"] = None
        rec["activated_at"] = datetime.now(timezone.utc).isoformat()
        accounts[key] = rec
        _write_accounts(storage, accounts)
        return {"ok": True, "plan": rec["plan"], "device_limit": rec["device_limit"]}


def login_account(storage, email: str, auth_secret: str) -> dict:
    """Subsequent-device sign in: verify email + password (via the auth_secret hash).
    Returns {ok, plan, device_limit} or {ok: False, error}."""
    email = _normalize_email(email)
    accounts = _load_accounts(storage)
    key = _find_email_key(accounts, email)
    if not key:
        # SELF-HOST (non-strict, no account record): everyone is Pro — bootstrap the
        # device with no pre-created account so `sync-relay` + `sync login` works for $0.
        if not _accounts_strict():
            return {"ok": True, "plan": "pro", "device_limit": 10, "selfhost": True}
        return {"ok": False, "error": "wrong email or password"}
    rec = accounts[key]
    # Verify ownership FIRST; only after a proven password do we disclose a non-active
    # status (telling a stranger "canceled" would leak account existence).
    if rec.get("token_hash") and hmac.compare_digest(rec["token_hash"], _verifier(auth_secret)):
        if rec.get("status") != "active":
            return {"ok": False, "error": f"subscription {rec.get('status')}"}
        return {"ok": True, "plan": rec["plan"], "device_limit": rec["device_limit"]}
    return {"ok": False, "error": "wrong email or password"}


def _resolve_key(accounts: dict, ref: str):
    """Map an id / email / raw auth_secret bearer to the accounts-dict key (id)."""
    if ref in accounts:                     # already an id
        return ref
    h = _verifier(ref)                       # a raw auth_secret bearer
    for k, rec in accounts.items():
        if rec.get("token_hash") == h:
            return k
    return _find_email_key(accounts, ref)    # an email


def set_account_status(storage, ref: str, status: str) -> bool:
    """Change an account's status. `ref` may be a raw token, token_hash, or id."""
    status = status if status in _VALID_STATUS else "canceled"
    with _accounts_write_lock:
        accounts = dict(_load_accounts(storage))
        key = _resolve_key(accounts, ref)
        if key is None:
            return False
        rec = dict(accounts[key])            # copy before mutate (don't touch the cache)
        rec["status"] = status
        accounts[key] = rec
        _write_accounts(storage, accounts)
        return True


def _device_stats(storage, dir_hash: str):
    """(device_count, last_seen_iso) from the account's ciphertext dir — metadata ONLY;
    uses file mtime (never decodes the blob, never parses tz-fragile timestamps)."""
    if not dir_hash:
        return 0, None
    adir = Path(storage) / dir_hash
    if not adir.exists():
        return 0, None
    count, last = 0, None
    for f in adir.glob("*.json"):
        count += 1
        try:
            m = f.stat().st_mtime
        except OSError:
            continue
        if last is None or m > last:
            last = m
    last_iso = datetime.fromtimestamp(last, timezone.utc).isoformat() if last else None
    return count, last_iso


def _public_record(rec: dict) -> dict:
    """Admin-facing view: account metadata only — NEVER the auth hash, the password,
    or any decrypted usage."""
    out = {k: rec.get(k) for k in (
        "id", "email", "name", "plan", "status", "device_limit",
        "created_at", "activated_at", "source", "note")}
    out["activated"] = bool(rec.get("token_hash"))
    return out


def list_accounts(storage) -> list:
    """Public account records enriched with device_count + last_seen (for /admin)."""
    out = []
    for rec in _load_accounts(storage).values():
        count, last = _device_stats(storage, rec.get("dir_hash", ""))
        item = _public_record(rec)
        item["device_count"] = count
        item["last_seen"] = last
        out.append(item)
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return out


def _plan_for(token: str, storage=None) -> dict:
    # 1. Accounts store (identity-aware), looked up by token HASH. Status-aware: a
    #    non-active subscription keeps its plan label but gets 0 devices (=> 402).
    if storage is not None:
        accounts = _load_accounts(storage)
        if accounts:
            acc = _by_auth_hash(accounts, _verifier(token))
            if acc:
                status = acc.get("status", "active")
                if status != "active":
                    return {"plan": acc.get("plan", "pro"), "device_limit": 0, "status": status}
                return {"plan": acc.get("plan", "pro"),
                        "device_limit": _coerce_limit(acc.get("device_limit"), acc.get("plan", "pro")),
                        "status": "active"}
            # Token not in the store: deny only in authoritative (hosted/admin) mode;
            # otherwise fall through so a casual self-hoster isn't locked out.
            if _accounts_strict():
                return {"plan": "free", "device_limit": 0, "status": "none"}
    # 2. HOSTED legacy plan-store (token->plan), billing-fed allowlist.
    if os.environ.get("BURNMETER_RELAY_BILLING") == "1":
        entry = _load_plans().get(token)
        if not entry:
            return {"plan": "free", "device_limit": 0}
        return {"plan": entry.get("plan", "pro"),
                "device_limit": _coerce_limit(entry.get("device_limit", 10), entry.get("plan", "pro"))}
    # 3. SELF-HOST default: everyone Pro. (BURNMETER_RELAY_FREE_TOKENS gates a token.)
    free = {t for t in os.environ.get("BURNMETER_RELAY_FREE_TOKENS", "").split(",") if t}
    if token in free:
        return {"plan": "free", "device_limit": 0}
    return {"plan": "pro", "device_limit": 10}


# Self-contained admin page (no external assets). It's inert without the admin key:
# every data call sends Authorization: Bearer <key> and the relay enforces it. The
# key is entered in the browser and kept in localStorage. Customer emails are
# rendered via textContent (never innerHTML) to avoid injection.
_ADMIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Burnmeter · Admin</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--b:#262b36;--t:#e6e9ef;--t2:#9aa3b2;--accent:#ff7a1a;--ok:#37b87a;--bad:#e0556b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;padding:24px;max-width:1100px}
h1{font-size:18px;margin:0 0 2px}.sub{color:var(--t2);font-size:12.5px;margin-bottom:18px}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:16px}
input,select,button{background:var(--card);color:var(--t);border:1px solid var(--b);border-radius:8px;padding:8px 10px;font-size:13px}
button{cursor:pointer}button.primary{background:var(--accent);border-color:var(--accent);color:#1a1205;font-weight:600}
button.ghost{background:transparent}
.card{background:var(--card);border:1px solid var(--b);border-radius:12px;padding:14px;margin-bottom:16px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--b);font-size:13px;white-space:nowrap}
th{color:var(--t2);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.04em}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11.5px;border:1px solid var(--b)}
.pill.active{color:var(--ok);border-color:#1f5e44}.pill.canceled,.pill.expired{color:var(--bad);border-color:#5e2530}
.tok{font-family:ui-monospace,monospace;color:var(--t2);font-size:12px;cursor:pointer}
.muted{color:var(--t2)}.err{color:var(--bad)}.right{margin-left:auto}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px}
</style></head><body>
<h1>Burnmeter · Admin</h1>
<div class="sub">Customers &amp; Pro entitlements. The relay stores account metadata only — it can never read your end-to-end encrypted usage.</div>
<div class="bar">
  <input id="key" type="password" placeholder="Admin key (BURNMETER_RELAY_ADMIN_TOKEN)" style="min-width:320px">
  <button class="primary" onclick="saveKey()">Connect</button>
  <span id="status" class="muted"></span>
  <button class="ghost right" onclick="load()">↻ Refresh</button>
</div>
<div class="card">
  <div style="font-weight:600;margin-bottom:10px">Create account</div>
  <div class="grid">
    <input id="c_email" placeholder="email" type="email">
    <input id="c_name" placeholder="name (optional)">
    <select id="c_plan"><option value="pro">pro</option><option value="team">team</option><option value="supporter">supporter</option><option value="license">license</option></select>
    <input id="c_limit" placeholder="device limit (auto)" type="number" min="0">
    <button class="primary" onclick="create()">+ Create</button>
  </div>
  <div id="created" class="muted" style="margin-top:8px"></div>
</div>
<div class="card">
  <table><thead><tr><th>Email</th><th>Plan</th><th>Status</th><th>Activated</th><th>Devices</th><th>Last sync</th><th>Created</th><th></th></tr></thead>
  <tbody id="rows"><tr><td colspan="8" class="muted">Enter your admin key and Connect.</td></tr></tbody></table>
</div>
<script>
const $=id=>document.getElementById(id);
let KEY=sessionStorage.getItem('bm_admin_key')||'';
if(KEY){$('key').value=KEY;}
function saveKey(){KEY=$('key').value.trim();sessionStorage.setItem('bm_admin_key',KEY);load();}
async function api(path,opts){opts=opts||{};opts.headers=Object.assign({'Authorization':'Bearer '+KEY},opts.headers||{});const r=await fetch(path,opts);if(!r.ok){throw new Error('HTTP '+r.status);}return r.json();}
function fmtDate(s){if(!s)return '—';try{return new Date(s).toLocaleString();}catch(e){return s;}}
function cell(txt,cls){const td=document.createElement('td');td.textContent=(txt==null||txt==='')?'—':txt;if(cls)td.className=cls;return td;}
async function load(){
  if(!KEY){$('status').textContent='no key';return;}
  $('status').textContent='loading…';
  try{
    const data=await api('/admin/api/accounts');
    const rows=$('rows');rows.innerHTML='';
    const list=data.accounts||[];
    if(!list.length){rows.innerHTML='<tr><td colspan="8" class="muted">No accounts yet. Create one above.</td></tr>';}
    for(const a of list){
      const tr=document.createElement('tr');
      tr.appendChild(cell(a.email));
      tr.appendChild(cell(a.plan));
      const st=document.createElement('td');const sp=document.createElement('span');sp.className='pill '+(a.status||'');sp.textContent=a.status||'—';st.appendChild(sp);tr.appendChild(st);
      tr.appendChild(cell(a.activated?'yes':'no', a.activated?'':'muted'));
      tr.appendChild(cell((a.device_count||0)+' / '+(a.device_limit||0)));
      tr.appendChild(cell(fmtDate(a.last_seen)));
      tr.appendChild(cell(fmtDate(a.created_at)));
      const act=document.createElement('td');const b=document.createElement('button');b.className='ghost';
      if(a.status==='active'){b.textContent='Revoke';b.onclick=()=>setStatus(a.id,'canceled');}
      else{b.textContent='Reactivate';b.onclick=()=>setStatus(a.id,'active');}
      act.appendChild(b);tr.appendChild(act);
      rows.appendChild(tr);
    }
    $('status').textContent=list.length+' account(s)';
  }catch(e){$('status').innerHTML='<span class="err">'+e.message+' — check admin key</span>';}
}
async function create(){
  const email=$('c_email').value.trim();if(!email){$('created').innerHTML='<span class="err">email required</span>';return;}
  const body={email:email,plan:$('c_plan').value,name:$('c_name').value.trim()};
  const lim=$('c_limit').value.trim();if(lim!=='')body.device_limit=parseInt(lim,10);
  try{
    const rec=await api('/admin/api/accounts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const c=$('created');c.textContent='✓ created · activation code (give it to the customer with their email): ';const s=document.createElement('span');s.className='tok';s.textContent=rec.activation_code;s.onclick=()=>navigator.clipboard.writeText(rec.activation_code);c.appendChild(s);
    $('c_email').value='';$('c_name').value='';$('c_limit').value='';
    load();
  }catch(e){$('created').innerHTML='<span class="err">'+e.message+'</span>';}
}
async function setStatus(id,status){
  try{await api('/admin/api/account/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,status:status})});load();}
  catch(e){$('status').innerHTML='<span class="err">'+e.message+'</span>';}
}
if(KEY)load();
</script>
</body></html>"""


def make_handler(storage: Path):
    storage.mkdir(parents=True, exist_ok=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            sys.stderr.write("[burnmeter-relay] %s - %s\n" % (self.address_string(), fmt % args))

        # ---- helpers ----
        def _token(self):
            auth = self.headers.get("Authorization", "")
            return auth[7:].strip() if auth.startswith("Bearer ") else None

        def _json(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _device_id(self):
            # path /v1/snap/<device_id> — whitelist the segment so it can NEVER be a
            # path-traversal (backslashes survive on Windows; the URL isn't decoded).
            parts = self.path.split("?")[0].rstrip("/").split("/")
            if len(parts) >= 4 and parts[-2] == "snap" and _DEV_RE.match(parts[-1]):
                return parts[-1]
            return None

        def _rate_limited(self, key) -> bool:
            if _rate_ok(key):
                return False
            self._json(429, {"error": "rate limit exceeded; slow down"})
            return True

        def _auth(self):
            """Validate + rate-limit an authenticated request. Returns the token, or
            None if an error response was already sent."""
            token = self._token()
            if not token:
                self._json(401, {"error": "missing Bearer token"}); return None
            if not _valid_token(token):
                self._json(400, {"error": "malformed token"}); return None
            if self._rate_limited(_rl_key(token)):
                return None
            return token

        def _html(self, code, html, headers=None):
            body = html.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _admin_ok(self):
            """True iff the request carries the relay admin token. Admin is DISABLED
            unless BURNMETER_RELAY_ADMIN_TOKEN is set (secure default — never exposed
            by accident). Constant-time compare; rate-limited per IP."""
            admin = os.environ.get("BURNMETER_RELAY_ADMIN_TOKEN")
            if not admin:
                self._json(404, {"error": "admin not enabled"}); return False
            if self._rate_limited("adm:" + self.address_string()):
                return False
            provided = self._token() or ""
            if not (len(provided) == len(admin) and hmac.compare_digest(provided, admin)):
                self._json(401, {"error": "admin auth required"}); return False
            return True

        def _read_json_body(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length <= 0 or length > MAX_BLOB:
                return None
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return None

        # ---- routes ----
        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/") or "/"
            if path == "/health":
                if self._rate_limited("ip:" + self.address_string()):
                    return
                return self._json(200, {"ok": True, "service": "burnmeter-sync-relay"})
            # Admin: serve the console only when admin is enabled (don't advertise its
            # existence otherwise). The page is inert without the key; the API beneath
            # it requires BURNMETER_RELAY_ADMIN_TOKEN.
            if path == "/admin":
                if not os.environ.get("BURNMETER_RELAY_ADMIN_TOKEN"):
                    return self._json(404, {"error": "not found"})
                return self._html(200, _ADMIN_HTML, {
                    "X-Frame-Options": "DENY",
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "no-store",
                    "Referrer-Policy": "no-referrer",
                })
            if path == "/admin/api/accounts":
                if not self._admin_ok():
                    return
                return self._json(200, {"accounts": list_accounts(storage)})
            token = self._auth()
            if token is None:
                return
            adir = _account_dir(storage, token)
            if path == "/v1/account":
                n = len(list(adir.glob("*.json"))) if adir.exists() else 0
                plan = _plan_for(token, storage)
                return self._json(200, {"plan": plan["plan"], "device_count": n,
                                        "device_limit": plan["device_limit"],
                                        "status": plan.get("status", "active")})
            if path == "/v1/snaps":
                out = {}
                if adir.exists():
                    for f in adir.glob("*.json"):
                        try:
                            out[f.stem] = json.loads(f.read_text())
                        except (OSError, json.JSONDecodeError):
                            continue
                return self._json(200, out)
            return self._json(404, {"error": "not found"})

        def do_PUT(self):
            token = self._auth()
            if token is None:
                return
            dev = self._device_id()
            if not dev:
                return self._json(400, {"error": "bad path; expected /v1/snap/<device_id>"})
            plan = _plan_for(token, storage)
            adir = _account_dir(storage, token)
            existing = len(list(adir.glob("*.json"))) if adir.exists() else 0
            is_new = not (adir / f"{dev}.json").exists()
            if plan["device_limit"] <= 0:
                msg = ("subscription inactive" if plan.get("status") in ("canceled", "expired")
                       else "Pro required for sync")
                return self._json(402, {"error": msg, "plan": plan["plan"],
                                        "status": plan.get("status")})
            if is_new and existing >= plan["device_limit"]:
                return self._json(402, {"error": "device limit reached",
                                        "device_limit": plan["device_limit"]})
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > MAX_BLOB:
                return self._json(413, {"error": "blob too large or empty"})
            blob = self.rfile.read(length)
            adir.mkdir(parents=True, exist_ok=True)
            (adir / f"{dev}.json").write_text(json.dumps({
                "blob_b64": base64.b64encode(blob).decode("ascii"),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }))
            return self._json(201 if is_new else 200, {"ok": True, "device_id": dev})

        def do_DELETE(self):
            token = self._auth()
            if token is None:
                return
            dev = self._device_id()
            if not dev:
                return self._json(400, {"error": "bad path"})
            f = _account_dir(storage, token) / f"{dev}.json"
            try:
                f.unlink()
            except FileNotFoundError:
                pass
            return self._json(200, {"ok": True, "deleted": dev})

        def do_POST(self):
            # Admin-only: create a customer account, or change its status. The future
            # payment webhook will hit create_account() via the same path. Wrapped so
            # malformed input can never leak a stack trace or drop the connection.
            try:
                path = self.path.split("?")[0].rstrip("/") or "/"
                # ---- public customer auth (email + password) ----
                if path in ("/v1/activate", "/v1/login"):
                    if self._rate_limited("ip:" + self.address_string()):
                        return
                    d = self._read_json_body() or {}
                    email, secret = d.get("email"), d.get("auth_secret")
                    if not email or not secret:
                        return self._json(400, {"error": "email and auth_secret required"})
                    if not _valid_token(secret):
                        return self._json(400, {"error": "bad auth_secret"})
                    if path == "/v1/activate":
                        if not d.get("code"):
                            return self._json(400, {"error": "activation code required"})
                        res = activate_account(storage, email, d["code"], secret)
                        return self._json(200 if res.get("ok") else 403, res)
                    res = login_account(storage, email, secret)
                    return self._json(200 if res.get("ok") else 401, res)
                # ---- admin (operator) ----
                if path == "/admin/api/accounts":
                    if not self._admin_ok():
                        return
                    data = self._read_json_body()
                    if not data or not data.get("email"):
                        return self._json(400, {"error": "email required"})
                    rec = create_account(storage, email=data["email"],
                                         plan=data.get("plan", "pro"),
                                         device_limit=data.get("device_limit"),
                                         name=data.get("name", ""), note=data.get("note", ""),
                                         source="admin")
                    return self._json(201, rec)   # rec includes the one-time activation_code
                if path == "/admin/api/account/status":
                    if not self._admin_ok():
                        return
                    data = self._read_json_body() or {}
                    ref = data.get("id") or data.get("token")
                    status = data.get("status", "canceled")
                    if not ref:
                        return self._json(400, {"error": "id or token required"})
                    ok = set_account_status(storage, ref, status)
                    return self._json(200 if ok else 404, {"ok": ok})
                return self._json(404, {"error": "not found"})
            except Exception:
                try:
                    self._json(500, {"error": "internal error"})
                except Exception:
                    pass

    return Handler


def run_relay(host: str = "127.0.0.1", port: int = 8899, storage: Path = None) -> None:
    storage = storage or (Path.home() / ".burnmeter-relay")
    httpd = ThreadingHTTPServer((host, port), make_handler(storage))
    billing = os.environ.get("BURNMETER_RELAY_BILLING") == "1"
    sys.stderr.write(f"[burnmeter-relay] zero-knowledge sync relay → http://{host}:{port}\n")
    sys.stderr.write(f"[burnmeter-relay] storage: {storage} (ciphertext only)\n")
    accounts_path = _accounts_path(storage)
    accounts_exist = accounts_path.exists()
    if accounts_exist and _accounts_strict():
        mode = "accounts (identity store, AUTHORITATIVE — unknown tokens denied)"
    elif accounts_exist:
        mode = "accounts (additive over self-host)"
    elif billing:
        mode = "HOSTED billing (plan-store)"
    else:
        mode = "self-host (everyone Pro)"
    sys.stderr.write(f"[burnmeter-relay] mode: {mode} · rate {RATE_CAPACITY}/{RATE_REFILL}/s\n")
    if accounts_exist:
        sys.stderr.write(f"[burnmeter-relay] accounts: {accounts_path}\n")
    admin_on = bool(os.environ.get("BURNMETER_RELAY_ADMIN_TOKEN"))
    sys.stderr.write(f"[burnmeter-relay] admin: {'ENABLED → ' + f'http://{host}:{port}/admin' if admin_on else 'disabled (set BURNMETER_RELAY_ADMIN_TOKEN to enable /admin)'}\n")
    if billing and not accounts_exist and not os.environ.get("BURNMETER_RELAY_PLANS"):
        sys.stderr.write("[burnmeter-relay] WARNING: BURNMETER_RELAY_BILLING=1 but BURNMETER_RELAY_PLANS unset → all tokens denied\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\n[burnmeter-relay] shutting down\n")
        httpd.shutdown()
