"""Burnmeter — local, privacy-preserving pre-limit alerts.

Runs ENTIRELY on the user's machine: the local `burnmeter` process evaluates the
same thresholds the dashboard shows and pushes a notification to the user's OWN
webhook / Slack / email when a binding constraint is about to be hit. Nothing is
ever sent to a Burnmeter-operated server — the local-first promise holds (no
phone-home). This is what lets an alert reach you when the dashboard tab is
closed: the local server keeps evaluating in a background thread.

Threshold logic is a faithful port of dashboard.js `alertLevelOf`, so the
terminal/server alerts and the in-browser toast never disagree.

Config: ~/.config/burnmeter/alerts.json (chmod 600 — may hold SMTP creds).
Senders use only the Python standard library (urllib, smtplib).

Example alerts.json:
    {
      "enabled": true,
      "sources": ["claude", "codex"],
      "destinations": {
        "webhook_url": "https://example.com/hook",
        "slack_webhook_url": "https://hooks.slack.com/services/...",
        "email": {"host": "smtp.gmail.com", "port": 587, "tls": true,
                   "username": "you@gmail.com", "password": "app-password",
                   "from": "you@gmail.com", "to": "you@gmail.com"}
      }
    }
"""
from __future__ import annotations

import json
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path.home() / ".config" / "burnmeter" / "alerts.json"

# Thresholds — keep in sync with dashboard.js alertLevelOf.
RATE_LIMIT_WARN = 85     # % of account rate-limit (Codex)
RATE_LIMIT_CRIT = 95     # %
BURN_HEAVY_MULT = 1.6    # Claude: warn when burn-rate >= 'heavy' zone * this

LEVEL_NAMES = {0: "ok", 2: "warn", 3: "crit"}


# ---------- config ----------
def load_config(path: Path = CONFIG_PATH) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)   # may contain an SMTP password
    except OSError:
        pass


def is_enabled(cfg: Optional[dict] = None) -> bool:
    c = cfg if cfg is not None else load_config()
    return bool(c.get("enabled")) and bool(c.get("destinations"))


def _money(n) -> str:
    try:
        return f"${float(n):,.0f}"
    except (TypeError, ValueError):
        return str(n)


# ---------- evaluate (faithful port of dashboard.js alertLevelOf) ----------
def evaluate(report: dict, source: str) -> tuple[int, str]:
    """Return (level, message). level: 0 safe, 2 warn, 3 critical.

    - Codex (has account rate-limits): max(primary, secondary) used_percent —
      >=95 critical, >=85 warning.
    - Claude (no account limit): burn-rate vs the 'heavy' reference zone —
      >= heavy * 1.6 warning.
    """
    if source == "codex":
        rls = report.get("codex_rate_limits") or {}
        if rls:
            dev = rls.get("mac") or next(iter(rls.values()), {}) or {}
            primary = (dev.get("primary") or {}).get("used_percent") or 0
            secondary = (dev.get("secondary") or {}).get("used_percent") or 0
            bp = max(primary, secondary)
            if bp >= RATE_LIMIT_CRIT:
                return 3, f"\U0001f534 Codex limiti %{round(bp)} — throttle ÇOK YAKIN"
            if bp >= RATE_LIMIT_WARN:
                return 2, f"\U0001f7e0 Codex limiti %{round(bp)} — duvara yaklaşıyorsun"
        return 0, ""

    # claude — no account limit, so we warn on excessive burn rate
    fc = report.get("forecast") or {}
    zones = report.get("burn_rate_zones") or {}
    by_hours = fc.get("burn_rates_by_hours") or {}
    rate = by_hours.get("2")
    if rate is None:
        rate = fc.get("burn_rate_per_hour_recent") or 0
    heavy = zones.get("heavy")
    if heavy and rate >= heavy * BURN_HEAVY_MULT:
        return 2, f"\U0001f7e0 Burn rate çok yüksek: {_money(rate)}/sa"
    return 0, ""


# ---------- senders (stdlib only) ----------
def send_webhook(url: str, payload: dict, timeout: int = 10) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "burnmeter-alerts"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()


def send_slack(url: str, text: str, timeout: int = 10) -> None:
    # Slack Incoming Webhooks accept {"text": ...}.
    send_webhook(url, {"text": text}, timeout=timeout)


def send_email(smtp: dict, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp.get("from") or smtp.get("username") or "burnmeter@localhost"
    msg["To"] = smtp["to"]
    msg.set_content(body)
    host = smtp.get("host", "localhost")
    port = int(smtp.get("port", 587))
    with smtplib.SMTP(host, port, timeout=20) as s:
        if smtp.get("tls", True):
            s.starttls(context=ssl.create_default_context())
        if smtp.get("username"):
            s.login(smtp["username"], smtp.get("password", ""))
        s.send_message(msg)


def dispatch(cfg: dict, level: int, msg: str, source: str) -> list[str]:
    """Send `msg` to every configured destination. Never raises — returns a list
    of "<dest>:ok" / "<dest>:err:<detail>" strings so callers can log results."""
    dests = (cfg or {}).get("destinations") or {}
    results: list[str] = []
    subject = f"Burnmeter {LEVEL_NAMES.get(level, '')} · {source}"
    if dests.get("webhook_url"):
        try:
            send_webhook(dests["webhook_url"], {
                "source": source, "level": level,
                "level_name": LEVEL_NAMES.get(level), "message": msg,
            })
            results.append("webhook:ok")
        except Exception as e:
            results.append(f"webhook:err:{e}")
    if dests.get("slack_webhook_url"):
        try:
            send_slack(dests["slack_webhook_url"], msg)
            results.append("slack:ok")
        except Exception as e:
            results.append(f"slack:err:{e}")
    if dests.get("email"):
        try:
            send_email(dests["email"], subject, msg)
            results.append("email:ok")
        except Exception as e:
            results.append(f"email:err:{e}")
    return results


def check_and_fire(reports: dict, cfg: Optional[dict] = None,
                   state: Optional[dict] = None) -> dict:
    """Evaluate each source's report and dispatch on an UPWARD threshold crossing.

    `reports`: {source: report_dict}. `state`: {source: last_level}, mutated and
    returned. De-dup matches dashboard.js: fire only when level >= 2 AND it is
    higher than the previous level for that source (so a sustained warning emits
    once, and a warn->crit escalation re-fires).
    """
    cfg = cfg if cfg is not None else load_config()
    state = state if state is not None else {}
    if not is_enabled(cfg):
        return state
    sources = cfg.get("sources") or list(reports.keys())
    for source in sources:
        rep = reports.get(source)
        if not rep:
            continue
        level, msg = evaluate(rep, source)
        prev = state.get(source, 0)
        state[source] = level
        if level >= 2 and level > prev:
            dispatch(cfg, level, msg, source)
    return state


def send_test(cfg: Optional[dict] = None) -> list[str]:
    """Send a test alert to every configured destination (ignores enabled/level
    gating) so users can verify their setup."""
    cfg = cfg if cfg is not None else load_config()
    return dispatch(cfg, 2,
                    "\U0001f514 Burnmeter test uyarısı — bildirim hedeflerin çalışıyor.",
                    "test")
