"""Aggregations and analytics over UsageRecord lists.

Produces the data structures the dashboard renders. Pure functions, no I/O.

Important methodological notes:

1. **Cache hit rate** is computed as
       cache_read / (cache_read + input + cache_creation)
   i.e. "of all the input-side tokens that went to the model, what fraction
   was satisfied from cache?". This is the metric that maps directly to
   cost savings (cache reads are 90% cheaper than fresh input).

2. **5-hour billing windows** are detected by clustering session activity
   into rolling 5-hour buckets keyed off the *first* assistant message
   timestamp of the cluster. Anthropic's window starts on first message,
   not at midnight.

3. **Anomalies** are flagged with both an absolute threshold (more than
   2x plan budget) and a relative one (z-score > 2 vs the user's own
   trailing-7-day baseline). We expose both signals so the UI can show
   *why* something was flagged.

4. **Plan inference** uses a P90 of the user's per-window token usage
   over the last 8 days, matching the convention popularized by the
   Claude-Code-Usage-Monitor project.
"""
from __future__ import annotations

import math
import statistics
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from .parser import UsageRecord
from .pricing import estimate_cost_usd, family_from_model, effective_tokens, price_for


# Anthropic plan token budgets per 5-hour window (approximate; Anthropic
# describes these in relative terms now, but these remain widely cited
# anchor numbers).
PLAN_LIMITS = {
    "pro": 44_000,
    "max5": 88_000,
    "max20": 220_000,
}

# Industry reference points from Anthropic's own cost-management docs.
INDUSTRY_REFERENCE = {
    "avg_cost_per_active_day_usd": 13.0,
    "monthly_cost_p90_usd": 250.0,
    "active_day_p90_cost_usd": 30.0,
}


def _bucket_total(rec: UsageRecord) -> int:
    """All token throughput (input + output + cache creation + cache read)."""
    return (
        rec.input_tokens
        + rec.output_tokens
        + rec.cache_creation_tokens
        + rec.cache_read_tokens
    )


def _bucket_billable(rec: UsageRecord) -> int:
    """Tokens that represent real new work, excluding cache reads.

    Cache reads are ~10x cheaper and don't represent fresh inference effort,
    so they're a poor signal for "how hard are we pushing the model right
    now". We use this for the pace/baseline comparison.
    """
    return rec.input_tokens + rec.output_tokens + rec.cache_creation_tokens


@dataclass
class Totals:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    messages: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
        )

    @property
    def cache_hit_rate(self) -> float:
        denom = (
            self.cache_read_tokens
            + self.input_tokens
            + self.cache_creation_tokens
        )
        if denom <= 0:
            return 0.0
        return self.cache_read_tokens / denom

    def add(self, rec: UsageRecord) -> None:
        self.input_tokens += rec.input_tokens
        self.output_tokens += rec.output_tokens
        self.cache_creation_tokens += rec.cache_creation_tokens
        self.cache_read_tokens += rec.cache_read_tokens
        self.cost_usd += estimate_cost_usd(
            rec.model,
            input_tokens=rec.input_tokens,
            output_tokens=rec.output_tokens,
            cache_read_tokens=rec.cache_read_tokens,
            cache_creation_tokens=rec.cache_creation_tokens,
        )
        self.messages += 1

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 4),
            "messages": self.messages,
            "cache_hit_rate": round(self.cache_hit_rate, 4),
        }


def aggregate_total(records: Iterable[UsageRecord]) -> Totals:
    t = Totals()
    for r in records:
        t.add(r)
    return t


def aggregate_by_day(records: Iterable[UsageRecord]) -> list[dict]:
    buckets: dict[str, Totals] = defaultdict(Totals)
    for r in records:
        key = r.timestamp.astimezone(timezone.utc).date().isoformat()
        buckets[key].add(r)
    return [
        {"date": k, **buckets[k].to_dict()}
        for k in sorted(buckets.keys())
    ]


def aggregate_by_model(records: Iterable[UsageRecord]) -> list[dict]:
    buckets: dict[str, Totals] = defaultdict(Totals)
    for r in records:
        fam = family_from_model(r.model) or "unknown"
        buckets[fam].add(r)
    return [
        {"model_family": k, **buckets[k].to_dict()}
        for k in sorted(buckets.keys())
    ]


def aggregate_by_project(records: Iterable[UsageRecord]) -> list[dict]:
    buckets: dict[str, Totals] = defaultdict(Totals)
    labels: dict[str, str] = {}
    for r in records:
        key = r.project_dir or r.project_label or "unknown"
        labels[key] = r.project_label or key
        buckets[key].add(r)
    out = []
    for k, t in buckets.items():
        d = {"project_dir": k, "project_label": labels[k], **t.to_dict()}
        out.append(d)
    out.sort(key=lambda x: x["cost_usd"], reverse=True)
    return out


def aggregate_by_session(
    records: Iterable[UsageRecord],
    user_intents: Optional[dict[str, str]] = None,
    recent_cutoff: Optional[datetime] = None,
) -> list[dict]:
    """Session başına aggregation.

    `recent_cutoff` verilirse her session item'ına ek olarak
    `recent_window_tokens` / `recent_window_cost_usd` / `recent_window_messages`
    field'ları eklenir — bu, sadece `r.timestamp >= recent_cutoff` olan
    record'ların subset toplamıdır. Cihan'ın paradigm itirazı (2026-05-28):
    "SON AKTİVİTE" panelinde 5 milyar token görünüyordu çünkü session
    LIFETIME toplam render ediliyordu. Recent-window subset gerçek "son
    aktivite" yakımını verir — UI bu yeni field'ı render eder.
    """
    buckets: dict[str, Totals] = defaultdict(Totals)
    recent_buckets: dict[str, Totals] = defaultdict(Totals)
    meta: dict[str, dict] = {}
    for r in records:
        sid = r.session_id or "unknown"
        if sid not in meta:
            meta[sid] = {
                "session_id": sid,
                "project_label": r.project_label,
                "git_branch": r.git_branch,
                "started_at": r.timestamp,
                "ended_at": r.timestamp,
                "models": set(),
                "model_latest": r.model,        # model of the latest record
                "_latest_ts": r.timestamp,      # internal: track the latest record's ts
                # LAST TURN snapshot — gerçek "son aktivite" tek bir turn'de
                # ne yakıldığını gösterir (Cihan paradigm 2026-05-29:
                # "34M token harcamak diye bişi var mı" — 2h window aggregate
                # değil, tek son turn'ün fresh token'ı lazım).
                "_last_record": r,
            }
        # Track the model on the latest record so the UI can show "what was
        # actually running at the end" instead of every model that ever
        # touched this session (which conflates with old Haiku detours).
        if r.timestamp > meta[sid]["_latest_ts"]:
            meta[sid]["_latest_ts"] = r.timestamp
            meta[sid]["model_latest"] = r.model
            meta[sid]["_last_record"] = r
        meta[sid]["ended_at"] = max(meta[sid]["ended_at"], r.timestamp)
        meta[sid]["started_at"] = min(meta[sid]["started_at"], r.timestamp)
        if r.model:
            meta[sid]["models"].add(r.model)
        buckets[sid].add(r)
        if recent_cutoff is not None and r.timestamp >= recent_cutoff:
            recent_buckets[sid].add(r)

    intents = user_intents or {}
    out = []
    for sid, t in buckets.items():
        m = meta[sid]
        duration = (m["ended_at"] - m["started_at"]).total_seconds()
        item = {
            **{k: v for k, v in m.items() if not k.startswith("_")},
            "started_at": m["started_at"].isoformat(),
            "ended_at": m["ended_at"].isoformat(),
            "duration_seconds": int(duration),
            "models": sorted(m["models"]),
            "intent": intents.get(sid, ""),    # first user prompt (the goal)
            **t.to_dict(),
        }
        if recent_cutoff is not None:
            rt = recent_buckets[sid].to_dict()
            # Sadece UI'nin ihtiyacı olan 4 alan — recent_window_* prefix
            # ile session lifetime toplamlarıyla isim çakışması yok.
            item["recent_window_tokens"]   = rt["total_tokens"]
            item["recent_window_cost_usd"] = rt["cost_usd"]
            item["recent_window_messages"] = rt["messages"]
            item["recent_window_cache_hit_rate"] = rt["cache_hit_rate"]
        # LAST TURN — son tek mesaj/turn'ün token'ları (Cihan paradigm
        # 2026-05-29 v3: "maliyet ağırlıklı giriş eşdeğeri token").
        #
        # last_turn_effective_tokens — maliyet ağırlıklı "yakım" sayısı.
        # Anthropic 4 kategoriyi farklı oranda ücretlendirir; ham total
        # cache_read'i 10x şişirir (yanlış). Input-equivalent:
        #     input + output*5 + cache_creation*1.25 + cache_read*0.10
        # Oranlar Opus/Sonnet/Haiku için identik (5x out, 1.25x cw, 0.10x cr).
        #
        # last_turn_tokens = sadece input+output (fresh); referans.
        # last_turn_total_tokens = 4'ünün ham toplamı; "Anthropic ne raporladı".
        # last_turn_cost_usd = gerçek USD.
        lr = m["_last_record"]
        lt = Totals()
        lt.add(lr)
        ltd = lt.to_dict()
        item["last_turn_tokens"]            = lr.input_tokens + lr.output_tokens
        item["last_turn_total_tokens"]      = ltd["total_tokens"]
        item["last_turn_effective_tokens"]  = effective_tokens(
            lr.model,
            input_tokens=lr.input_tokens,
            output_tokens=lr.output_tokens,
            cache_read_tokens=lr.cache_read_tokens,
            cache_creation_tokens=lr.cache_creation_tokens,
        )
        item["last_turn_cost_usd"]       = ltd["cost_usd"]
        item["last_turn_cache_hit_rate"] = ltd["cache_hit_rate"]
        item["last_turn_iso"]            = lr.timestamp.isoformat()
        out.append(item)
    # Sort by *ended_at* (last turn) so "most recently active" lands on top —
    # a session that started 4h ago but had a turn 30sec ago is still active.
    out.sort(key=lambda x: x["ended_at"], reverse=True)
    return out


def aggregate_by_specific_model(records: Iterable[UsageRecord]) -> list[dict]:
    """Keep specific model IDs (claude-opus-4-7 vs claude-sonnet-4-5-...) separate."""
    buckets: dict[str, Totals] = defaultdict(Totals)
    for r in records:
        key = r.model or "unknown"
        buckets[key].add(r)
    out = []
    for k, t in buckets.items():
        out.append({"model_id": k, **t.to_dict()})
    out.sort(key=lambda x: x["cost_usd"], reverse=True)
    return out


def aggregate_by_tool(records: Iterable[UsageRecord]) -> list[dict]:
    """Attribute *output* tokens to the tools used in the same turn.

    Caveat: Claude's output tokens for a turn cover both the prose AND
    the tool_use blocks together — we cannot perfectly split them. We
    therefore split the turn's output tokens evenly across the tool_use
    blocks present in that turn (or attribute fully to "(no tool)" if
    none). This is an approximation and labelled as such in the UI.
    """
    counts: dict[str, int] = defaultdict(int)
    output_tokens: dict[str, float] = defaultdict(float)
    for r in records:
        if not r.tool_names:
            counts["(no tool)"] += 1
            output_tokens["(no tool)"] += r.output_tokens
            continue
        share = r.output_tokens / len(r.tool_names) if r.tool_names else 0
        for name in r.tool_names:
            counts[name] += 1
            output_tokens[name] += share

    out = [
        {
            "tool": name,
            "calls": counts[name],
            "approx_output_tokens": int(output_tokens[name]),
        }
        for name in counts
    ]
    out.sort(key=lambda x: x["approx_output_tokens"], reverse=True)
    return out


# --- 5-hour billing windows ------------------------------------------------

WINDOW = timedelta(hours=5)


def detect_billing_windows(records: list[UsageRecord]) -> list[dict]:
    """Group records into Anthropic-style 5-hour windows.

    A window opens on the first record after >= 5h of inactivity and
    closes 5h after that opening timestamp. We emit one dict per window.
    """
    if not records:
        return []
    windows: list[dict] = []
    sorted_recs = sorted(records, key=lambda r: r.timestamp)

    current_start: Optional[datetime] = None
    current_end: Optional[datetime] = None
    current_totals = Totals()
    current_window_tokens = 0   # all tokens (incl. cache reads)
    current_billable_tokens = 0 # input + output + cache_creation (real new work)
    current_models: set[str] = set()

    def flush():
        nonlocal current_totals, current_window_tokens, current_billable_tokens
        nonlocal current_models, current_start, current_end
        if current_start is None:
            return
        windows.append({
            "started_at": current_start.isoformat(),
            "ends_at": (current_start + WINDOW).isoformat(),
            "last_activity_at": current_end.isoformat() if current_end else current_start.isoformat(),
            "window_tokens": current_window_tokens,
            "billable_tokens": current_billable_tokens,
            "models": sorted(current_models),
            **current_totals.to_dict(),
        })
        current_totals = Totals()
        current_window_tokens = 0
        current_billable_tokens = 0
        current_models = set()
        current_start = None
        current_end = None

    for r in sorted_recs:
        if current_start is None or r.timestamp >= current_start + WINDOW:
            flush()
            current_start = r.timestamp
        current_end = r.timestamp
        current_totals.add(r)
        current_window_tokens += _bucket_total(r)
        current_billable_tokens += _bucket_billable(r)
        if r.model:
            current_models.add(family_from_model(r.model))
    flush()
    return windows


def infer_plan(windows: list[dict], lookback_days: int = 8) -> dict:
    """Return a guess at the user's plan based on their P90 window usage.

    We pick the smallest plan whose limit is >= the P90 observed over the
    lookback period. This matches how Claude-Code-Usage-Monitor's "auto"
    mode classifies plans (P90 over the last 192 hours).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    recent = [
        w["window_tokens"]
        for w in windows
        if datetime.fromisoformat(w["started_at"]) >= cutoff
    ]
    if not recent:
        return {
            "guess": "unknown",
            "p90_window_tokens": 0,
            "samples": 0,
            "reason": f"No activity in the last {lookback_days} days.",
        }

    p90 = _percentile(sorted(recent), 0.90)
    if p90 <= PLAN_LIMITS["pro"]:
        guess = "pro"
    elif p90 <= PLAN_LIMITS["max5"]:
        guess = "max5"
    else:
        guess = "max20"
    return {
        "guess": guess,
        "p90_window_tokens": int(p90),
        "samples": len(recent),
        "reason": (
            f"P90 of last {len(recent)} windows = {int(p90):,} tokens. "
            "Smallest plan whose limit is greater wins."
        ),
    }


def window_baseline(windows: list[dict]) -> dict:
    """Personal 'normal' for 5-hour windows.

    Uses billable_tokens (excludes cache reads) so the comparison reflects
    *actual* work intensity, not how much was served from cache. Excludes
    the current (last) window from the baseline so we compare today against
    history, not against itself. Also includes cost percentiles so the UI
    can show "$X is your typical window cost" — dollars are easier to read
    than token counts.
    """
    if len(windows) < 2:
        return {"samples": 0}
    past = windows[:-1]
    billable = sorted(w.get("billable_tokens", 0) for w in past if w.get("billable_tokens", 0) > 0)
    costs = sorted(w.get("cost_usd", 0.0) for w in past if w.get("cost_usd", 0.0) > 0)
    if len(billable) < 3:
        return {"samples": len(billable)}
    return {
        "samples": len(billable),
        "billable_p50": int(_percentile(billable, 0.5)),
        "billable_p90": int(_percentile(billable, 0.9)),
        "cost_p50": round(_percentile(costs, 0.5), 2) if costs else 0.0,
        "cost_p90": round(_percentile(costs, 0.9), 2) if costs else 0.0,
        "typical_billable_per_window": int(_percentile(billable, 0.5)),
    }


def current_window_status(windows: list[dict], plan_name: Optional[str]) -> dict:
    """How are we doing in the *current* 5-hour window?

    Compares billable (cache-read-excluded) usage against the user's own
    historical median window — that's the most honest "are you burning
    faster than usual?" signal. The plan-limit comparison is reported but
    de-emphasized because Anthropic's documented limits are heuristic.
    """
    if not windows:
        return {"active": False}
    last = windows[-1]
    started = datetime.fromisoformat(last["started_at"])
    now = datetime.now(timezone.utc)
    elapsed = now - started
    # Guard against clock skew / future-dated records: treat negatives as 0.
    if elapsed.total_seconds() < 0:
        elapsed = timedelta(seconds=0)
    if elapsed >= WINDOW:
        return {"active": False, "last_window": last}

    plan = (plan_name or "max5").lower()
    limit = PLAN_LIMITS.get(plan, PLAN_LIMITS["max5"])
    used_total = last["window_tokens"]
    used_billable = last.get("billable_tokens", 0)
    used_cost = last.get("cost_usd", 0.0)
    seconds_remaining = int((WINDOW - elapsed).total_seconds())
    elapsed_min = elapsed.total_seconds() / 60.0
    elapsed_hr = elapsed.total_seconds() / 3600.0

    burn_total_per_min = 0.0
    burn_billable_per_min = 0.0
    cost_per_hour = 0.0
    if elapsed.total_seconds() > 60:
        burn_total_per_min = used_total / elapsed_min
        burn_billable_per_min = used_billable / elapsed_min
        cost_per_hour = used_cost / elapsed_hr if elapsed_hr > 0 else 0

    projected_total = used_total + burn_total_per_min * (seconds_remaining / 60.0)
    projected_billable = used_billable + burn_billable_per_min * (seconds_remaining / 60.0)
    projected_cost = used_cost + cost_per_hour * (seconds_remaining / 3600.0)

    # Compare projected billable to user's own median window.
    base = window_baseline(windows)
    vs_baseline = None
    verdict_kind = "neutral"
    verdict_label = "not enough history yet"
    if base.get("samples", 0) >= 3 and base.get("billable_p50"):
        vs_baseline = projected_billable / base["billable_p50"]
        if vs_baseline >= 3.0:
            verdict_kind = "bad"
            verdict_label = f"{vs_baseline:.1f}x faster than your normal"
        elif vs_baseline >= 1.5:
            verdict_kind = "warn"
            verdict_label = f"{vs_baseline:.1f}x your normal pace"
        elif vs_baseline <= 0.5:
            verdict_kind = "neutral"
            verdict_label = "lighter than your normal"
        else:
            verdict_kind = "good"
            verdict_label = "normal pace for you"

    return {
        "active": True,
        "started_at": last["started_at"],
        "ends_at": last["ends_at"],
        "elapsed_seconds": int(elapsed.total_seconds()),
        "remaining_seconds": seconds_remaining,
        "tokens_used": used_total,
        "billable_tokens_used": used_billable,
        "plan": plan,
        "plan_limit": limit,
        "utilization": round(used_total / limit, 4) if limit else 0,
        "burn_tokens_per_min": round(burn_total_per_min, 1),
        "burn_billable_per_min": round(burn_billable_per_min, 1),
        "projected_close_tokens": int(projected_total),
        "projected_close_billable": int(projected_billable),
        "projected_overage": max(0, int(projected_total - limit)),
        "baseline_billable_p50": base.get("billable_p50"),
        "baseline_billable_p90": base.get("billable_p90"),
        "baseline_cost_p50": base.get("cost_p50"),
        "baseline_cost_p90": base.get("cost_p90"),
        "vs_baseline_ratio": round(vs_baseline, 2) if vs_baseline is not None else None,
        "cost_per_hour": round(cost_per_hour, 2),
        "projected_close_cost": round(projected_cost, 2),
        "verdict_kind": verdict_kind,
        "verdict_label": verdict_label,
        "models": last["models"],
        "cost_usd": round(used_cost, 2),
        **_block_eta(used_cost, cost_per_hour, base.get("cost_p50"), base.get("cost_p90"), seconds_remaining, now),
    }


def _block_eta(used_cost, cost_per_hour, p50, p90, seconds_remaining, now):
    """Compute when the current 5h block will hit user's P50/P90 spend at
    the current burn rate. Returns dict with eta_p50_iso, eta_p90_iso,
    eta_p50_minutes_from_now, eta_p90_minutes_from_now, will_hit_p90, etc.

    "Will hit" = the projected spend would exceed that threshold before the
    block window closes. If burn is zero or threshold already passed, those
    cases are flagged separately.
    """
    result = {
        "block_eta_p50_iso": None,
        "block_eta_p90_iso": None,
        "block_minutes_to_p50": None,
        "block_minutes_to_p90": None,
        "block_will_hit_p50": False,
        "block_will_hit_p90": False,
        "block_already_past_p50": False,
        "block_already_past_p90": False,
    }
    if not cost_per_hour or cost_per_hour <= 0:
        return result

    for label, target in (("p50", p50), ("p90", p90)):
        if not target or target <= 0:
            continue
        if used_cost >= target:
            result[f"block_already_past_{label}"] = True
            continue
        delta = target - used_cost
        hours_to = delta / cost_per_hour
        seconds_to = hours_to * 3600
        if seconds_to > seconds_remaining:
            # Block ends before reaching this threshold.
            continue
        eta = now + timedelta(seconds=seconds_to)
        result[f"block_eta_{label}_iso"] = eta.isoformat()
        result[f"block_minutes_to_{label}"] = int(seconds_to / 60)
        result[f"block_will_hit_{label}"] = True
    return result


def weekly_cap_status(records: list[UsageRecord], daily: list[dict]) -> dict:
    """Rolling-7-day spend + how it compares to the user's historical busy week.

    Useful as a "weekly plan progress" gauge — Pro/Max users have weekly
    caps that are easy to drain unexpectedly (Anthropic's #1 complaint in
    early 2026).

    Returns:
        rolling_7d_cost          : $ in the last 7 calendar days (today included)
        rolling_7d_tokens        : tokens in last 7 days
        prev_7d_cost             : $ in the 7 days before that (for delta context)
        delta_pct                : % change vs prior 7 days
        historical_7d_p95        : highest 7-day rolling spend you've ever had
        historical_7d_median     : median 7-day rolling spend
        usage_pct_of_personal_p95: rolling_7d / historical_p95
        verdict_kind/label       : color + Turkish wording
    """
    today = datetime.now(timezone.utc).date()
    cutoff_now = today
    cutoff_recent = today - timedelta(days=6)
    cutoff_prior = today - timedelta(days=13)

    def _sum_between(start, end):
        total = 0.0
        for d in daily:
            try:
                dd = datetime.fromisoformat(d["date"]).date()
            except (ValueError, TypeError):
                continue
            if start <= dd <= end:
                total += d.get("cost_usd", 0)
        return total

    rolling_7d_cost = _sum_between(cutoff_recent, cutoff_now)
    prev_7d_cost = _sum_between(cutoff_prior, cutoff_recent - timedelta(days=1))

    # Compute all 7-day rolling windows in user's history → P95 + median.
    daily_sorted = sorted(daily, key=lambda d: d["date"])
    rolling_history = []
    for i in range(6, len(daily_sorted)):
        window_sum = sum(daily_sorted[j].get("cost_usd", 0) for j in range(i - 6, i + 1))
        if window_sum > 0.5:    # skip near-zero stretches
            rolling_history.append(window_sum)
    rolling_history.sort()
    p95 = _percentile(rolling_history, 0.95) if rolling_history else 0
    median7 = _percentile(rolling_history, 0.5) if rolling_history else 0

    delta_pct = ((rolling_7d_cost - prev_7d_cost) / prev_7d_cost * 100) if prev_7d_cost > 0 else 0

    pct_of_p95 = (rolling_7d_cost / p95 * 100) if p95 > 0 else 0

    if pct_of_p95 >= 100:
        kind, label = "bad",  "tarihteki en yoğun haftana ulaştın"
    elif pct_of_p95 >= 75:
        kind, label = "warn", "geçmiş yoğun haftalarına yakın"
    elif pct_of_p95 >= 40:
        kind, label = "good", "tipik bir hafta"
    else:
        kind, label = "good", "düşük tempo"

    return {
        "rolling_7d_cost": round(rolling_7d_cost, 2),
        "prev_7d_cost": round(prev_7d_cost, 2),
        "delta_pct": round(delta_pct, 1),
        "historical_7d_p95": round(p95, 2),
        "historical_7d_median": round(median7, 2),
        "samples": len(rolling_history),
        "pct_of_personal_p95": round(pct_of_p95, 1),
        "verdict_kind": kind,
        "verdict_label": label,
    }


# --- Anomaly detection -----------------------------------------------------

def daily_anomalies(daily: list[dict]) -> list[dict]:
    """Flag days whose token usage is statistically unusual.

    Strategy: compute z-score against the trailing 7 days (excluding the
    day under test). Flag z >= 2.0 as a high anomaly. Also flag any day
    whose absolute cost is more than 2x the rolling 7-day median.
    """
    out = []
    series = list(daily)
    for i, day in enumerate(series):
        prior = series[max(0, i - 7):i]
        if len(prior) < 3:
            continue
        tokens = [d["total_tokens"] for d in prior]
        mean = statistics.fmean(tokens)
        stdev = statistics.pstdev(tokens) or 1.0
        z = (day["total_tokens"] - mean) / stdev
        median_cost = statistics.median([d["cost_usd"] for d in prior]) or 0.01
        cost_ratio = day["cost_usd"] / median_cost if median_cost else 0
        flags = []
        if z >= 2.0:
            flags.append(f"z-score {z:.1f} vs trailing 7d")
        if cost_ratio >= 2.0:
            flags.append(f"cost {cost_ratio:.1f}x trailing median")
        if flags:
            out.append({
                "date": day["date"],
                "tokens": day["total_tokens"],
                "cost_usd": day["cost_usd"],
                "z_score": round(z, 2),
                "cost_ratio": round(cost_ratio, 2),
                "flags": flags,
            })
    return out


# --- Misc helpers ----------------------------------------------------------

def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = q * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_values[lo])
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def baseline_stats(records: list[UsageRecord]) -> dict:
    """Personal baseline used as the 'is this normal?' reference.

    Reports per-day cost percentiles. Useful for the dashboard caption
    "you usually spend $X/day; today is in the Yth percentile".
    """
    daily = aggregate_by_day(records)
    if not daily:
        return {"samples": 0}
    costs = sorted(d["cost_usd"] for d in daily)
    tokens = sorted(d["total_tokens"] for d in daily)
    return {
        "samples": len(daily),
        "cost_p50": round(_percentile(costs, 0.5), 4),
        "cost_p90": round(_percentile(costs, 0.9), 4),
        "cost_p99": round(_percentile(costs, 0.99), 4),
        "tokens_p50": int(_percentile(tokens, 0.5)),
        "tokens_p90": int(_percentile(tokens, 0.9)),
        "tokens_p99": int(_percentile(tokens, 0.99)),
    }


def _git_log(repo_path: Path, since_days: int) -> list[dict]:
    """Run git log for a repo, return commits with timestamp + subject.

    Returns empty list if directory isn't a git repo or git isn't available.
    Uses a short timeout to avoid hanging on broken repos.
    """
    try:
        result = subprocess.run(
            [
                "git", "-C", str(repo_path), "log",
                "--since", f"{since_days} days ago",
                "--format=%H%x09%ct%x09%s%x09%an",
                "--no-merges",
                "--all",
            ],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return []
    if result.returncode != 0:
        return []
    commits = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        try:
            ts = datetime.fromtimestamp(int(parts[1]), tz=timezone.utc)
        except ValueError:
            continue
        commits.append({
            "sha": parts[0],
            "timestamp": ts,
            "message": parts[2][:90],
            "author": parts[3],
        })
    return commits


def attribute_to_commits(
    records: list[UsageRecord],
    since_days: int = 14,
    top_n: int = 12,
    max_attribution_hours: int = 4,
) -> dict:
    """Pair Claude Code sessions with git commits by time proximity.

    For each project_dir that is a git repo, walk recent commits in order.
    Attribute records that fall in the (prev_commit, this_commit] interval
    to "this commit" — i.e. work done leading up to the commit. We cap the
    look-back per commit at `max_attribution_hours` so weeks-old work
    doesn't get glued onto an unrelated commit.

    Returns:
        {
          "commits": [...top_n by cost...],
          "median_cost_usd": float,
          "median_tokens": int,
          "mean_cost_usd": float,
          "total_commits_matched": int,
          "since_days": int,
          "projects_scanned": int,
        }
    """
    # Group records by project_dir, sort by time.
    by_project: dict[str, list[UsageRecord]] = defaultdict(list)
    for r in records:
        if r.project_dir and r.timestamp:
            by_project[r.project_dir].append(r)

    cap = timedelta(hours=max_attribution_hours)
    matched: list[dict] = []
    projects_scanned = 0

    for project_dir, project_records in by_project.items():
        p = Path(project_dir)
        # Walk up to find a .git directory (handles worktrees too).
        git_root = None
        for candidate in [p] + list(p.parents)[:5]:
            if (candidate / ".git").exists():
                git_root = candidate
                break
        if git_root is None:
            continue

        commits = _git_log(git_root, since_days)
        if not commits:
            continue
        projects_scanned += 1

        commits.sort(key=lambda c: c["timestamp"])
        project_records.sort(key=lambda r: r.timestamp)

        # For each commit, gather records in (prev_commit, commit_time]
        # capped to max_attribution_hours before commit.
        rec_idx = 0
        prev_commit_time: Optional[datetime] = None

        for commit in commits:
            commit_time = commit["timestamp"]
            lower_bound = commit_time - cap
            if prev_commit_time and prev_commit_time > lower_bound:
                lower_bound = prev_commit_time

            tokens = 0
            cost = 0.0
            sess: set[str] = set()

            # Advance through sorted records
            while rec_idx < len(project_records):
                r = project_records[rec_idx]
                if r.timestamp <= lower_bound:
                    rec_idx += 1
                    continue
                if r.timestamp > commit_time:
                    break
                tokens += r.input_tokens + r.output_tokens + r.cache_creation_tokens + r.cache_read_tokens
                cost += estimate_cost_usd(
                    r.model,
                    input_tokens=r.input_tokens,
                    output_tokens=r.output_tokens,
                    cache_read_tokens=r.cache_read_tokens,
                    cache_creation_tokens=r.cache_creation_tokens,
                )
                sess.add(r.session_id)
                rec_idx += 1

            if tokens > 0:
                matched.append({
                    "sha": commit["sha"][:8],
                    "timestamp": commit_time.isoformat(),
                    "message": commit["message"],
                    "author": commit["author"],
                    "project_label": Path(project_dir).name,
                    "tokens": tokens,
                    "cost_usd": round(cost, 4),
                    "sessions": len(sess),
                })
            prev_commit_time = commit_time

    if not matched:
        return {
            "commits": [],
            "median_cost_usd": 0.0,
            "median_tokens": 0,
            "mean_cost_usd": 0.0,
            "total_commits_matched": 0,
            "since_days": since_days,
            "projects_scanned": projects_scanned,
        }

    costs = sorted(m["cost_usd"] for m in matched)
    tokens_list = sorted(m["tokens"] for m in matched)
    top = sorted(matched, key=lambda m: m["cost_usd"], reverse=True)[:top_n]

    return {
        "commits": top,
        "median_cost_usd": round(statistics.median(costs), 4),
        "median_tokens": int(statistics.median(tokens_list)),
        "mean_cost_usd": round(statistics.fmean(costs), 4),
        "total_commits_matched": len(matched),
        "since_days": since_days,
        "projects_scanned": projects_scanned,
    }


def _active_day_avg(daily: list[dict], date_floor: Optional[datetime.date] = None,
                    active_threshold_usd: float = 0.5) -> tuple[float, int]:
    """Average $/day across days where actual work happened.

    Skips $0 days (truly idle) and the threshold filters out trivial
    spurious activity. Returns (avg_usd, sample_count).
    """
    from datetime import date as _date
    days = []
    for d in daily:
        if d["cost_usd"] < active_threshold_usd:
            continue
        try:
            dd = _date.fromisoformat(d["date"])
        except (ValueError, TypeError):
            continue
        if date_floor and dd < date_floor:
            continue
        days.append(d["cost_usd"])
    if not days:
        return 0.0, 0
    return statistics.fmean(days), len(days)


def active_hours_stats(records: list[UsageRecord], days: int = 7) -> dict:
    """Estimate hours of *actual* coding activity by bucketing records into
    5-minute intervals. Each distinct bucket = 5 active minutes.

    This sidesteps the trap of summing raw session durations (which would
    overcount idle time when you walk away with Claude Code open) and the
    opposite trap of counting only message timestamps (which would
    undercount the time between messages within a single thinking turn).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent = [r for r in records if r.timestamp >= cutoff]
    if not recent:
        return {
            "active_hours": 0.0,
            "active_days": 0,
            "cost_usd": 0.0,
            "cost_per_hour": 0.0,
            "hours_per_active_day": 0.0,
        }

    BUCKET_SEC = 5 * 60  # 5-minute granularity
    buckets: set[int] = set()
    cost = 0.0
    days_set: set = set()
    for r in recent:
        buckets.add(int(r.timestamp.timestamp() // BUCKET_SEC))
        days_set.add(r.timestamp.date())
        cost += estimate_cost_usd(
            r.model,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cache_read_tokens=r.cache_read_tokens,
            cache_creation_tokens=r.cache_creation_tokens,
        )

    active_minutes = len(buckets) * (BUCKET_SEC / 60)
    active_hours = active_minutes / 60.0
    return {
        "active_hours": round(active_hours, 1),
        "active_days": len(days_set),
        "cost_usd": round(cost, 2),
        "cost_per_hour": round(cost / active_hours, 2) if active_hours else 0.0,
        "hours_per_active_day": round(active_hours / len(days_set), 1) if days_set else 0.0,
    }


def fuel_efficiency(
    records: list[UsageRecord],
    daily: list[dict],
    git_attr: dict,
    totals_dict: dict,
    tool_label: str = "Claude Code",
) -> dict:
    """The 'you vs spec sheet' panel.

    Each metric: your value + the published / typical reference + a
    verdict telling you whether you're inside, below, or above spec.
    Also reports recent-vs-prior so you can tell if a change you made
    (e.g. better prompts, more caching) is paying off.
    """
    from datetime import date as _date
    today = datetime.now(timezone.utc).date()
    cutoff_7 = today - timedelta(days=7)
    cutoff_30 = today - timedelta(days=30)

    # --- 1. Per active HOUR (replaces misleading $/day comparison) ---
    # Honesty (2026-05-31): there is no verifiable external $/hr norm, and
    # Anthropic exposes no account-level usage baseline. The per-hour band is
    # therefore SELF-RELATIVE — derived below from the user's own prior 30d
    # $/hr. (Earlier hardcoded 4/12/25 constants are removed.)
    hours_stats = active_hours_stats(records, days=7)
    prior_hours_stats = active_hours_stats(
        [r for r in records if r.timestamp.date() < cutoff_7 and r.timestamp.date() >= cutoff_30],
        days=23,
    )

    # Also keep per_active_day for those who want the raw $/day metric
    recent_avg, recent_n = _active_day_avg(daily, date_floor=cutoff_7)
    prior_avg, prior_n = _active_day_avg(
        [d for d in daily if d["date"] < cutoff_7.isoformat() and d["date"] >= cutoff_30.isoformat()]
    )
    baseline_day = INDUSTRY_REFERENCE["avg_cost_per_active_day_usd"]   # $13
    p90_day = INDUSTRY_REFERENCE["active_day_p90_cost_usd"]            # $30

    you_per_hour = hours_stats["cost_per_hour"]
    # Self-relative per-hour band: anchor on the user's OWN prior 30d $/hr
    # (Anthropic exposes no per-hour norm; the earlier 4/12/25 constants had no
    # verifiable source and contradicted the surrounding comment). The band is
    # [0.7x .. 1.3x] of the personal anchor; "heavy" = 2x personal.
    personal_hour_anchor = prior_hours_stats["cost_per_hour"]
    if personal_hour_anchor and personal_hour_anchor > 0:
        HOUR_LIGHT = round(personal_hour_anchor * 0.7, 2)
        HOUR_NORMAL_HI = round(personal_hour_anchor * 1.3, 2)
        HOUR_HEAVY = round(personal_hour_anchor * 2.0, 2)
        _hour_anchored = True
    else:
        # No personal history yet → conservative defaults, flagged for the UI.
        HOUR_LIGHT, HOUR_NORMAL_HI, HOUR_HEAVY = 4.0, 12.0, 25.0
        _hour_anchored = False
    if hours_stats["active_hours"] < 0.5:
        hour_kind, hour_label = "neutral", "barely any recent activity"
    elif not _hour_anchored:
        hour_kind, hour_label = "neutral", "henüz kişisel baz yok"
    elif you_per_hour < HOUR_LIGHT:
        hour_kind, hour_label = "good", "senin tipiğinin altında"
    elif you_per_hour < HOUR_NORMAL_HI:
        hour_kind, hour_label = "good", "senin tipik aralığında"
    elif you_per_hour < HOUR_HEAVY:
        hour_kind, hour_label = "warn", "senin yoğun saatlerin seviyesinde"
    else:
        hour_kind, hour_label = "bad", "senin yoğun saatlerinin üstünde"

    # Self-relative day verdict: compare recent $/day to the user's OWN prior
    # 30d average (NOT an unverifiable industry constant). Anthropic exposes no
    # account-level usage baseline, so the only honest anchor is the user's
    # history. `day_anchor` is the personal prior avg when we have enough prior
    # samples; otherwise we stay neutral rather than fabricate a comparison.
    day_anchor = prior_avg if prior_n >= 3 else 0.0
    # Personal busy-day reference (P90 over the user's own active days, last 30d).
    _active_day_costs = sorted(
        d["cost_usd"] for d in daily
        if d.get("cost_usd", 0) >= 0.5 and d.get("date", "") >= cutoff_30.isoformat()
    )
    if len(_active_day_costs) >= 5:
        _p90_idx = max(0, int(round(0.9 * (len(_active_day_costs) - 1))))
        personal_day_p90 = _active_day_costs[_p90_idx]
    else:
        personal_day_p90 = 0.0
    if recent_n == 0:
        day_kind, day_label = "neutral", "no recent activity"
    elif day_anchor <= 0:
        day_kind, day_label = "neutral", "no prior baseline yet"
    else:
        ratio = recent_avg / day_anchor
        if ratio < 0.7:
            day_kind, day_label = "good", f"{ratio:.1f}x senin normalin (düşük)"
        elif ratio < 1.3:
            day_kind, day_label = "good", f"{ratio:.1f}x senin normalin"
        elif ratio < 2.0:
            day_kind, day_label = "warn", f"{ratio:.1f}x senin normalin (yoğun)"
        else:
            day_kind, day_label = "bad", f"{ratio:.1f}x senin normalin (çok yoğun)"

    # --- 2. Per commit ---
    you_per_commit = git_attr.get("mean_cost_usd", 0)
    you_median_commit = git_attr.get("median_cost_usd", 0)
    matched = git_attr.get("total_commits_matched", 0)
    # Anthropic exposes no account data and there is NO verifiable industry
    # $/commit number (the old "industry $13/day ÷ 3 commits" anchor was
    # fabricated). So this is DESCRIPTIVE only — the raw figure, no judgment.
    if matched < 3:
        commit_kind, commit_label = "neutral", "yeterli commit eşleşmedi"
    else:
        commit_kind, commit_label = "neutral", "ham · kıyas yok"

    # --- 3. Cache hit rate ---
    cache_rate = totals_dict.get("cache_hit_rate", 0)
    target_good = 0.70
    target_excellent = 0.85
    if cache_rate >= target_excellent:
        cache_kind, cache_label = "good", "excellent (peak efficiency)"
    elif cache_rate >= target_good:
        cache_kind, cache_label = "good", "good (above target)"
    elif cache_rate >= 0.40:
        cache_kind, cache_label = "warn", "ok (below target)"
    else:
        cache_kind, cache_label = "bad", "low (most input uncached)"

    # --- 4. Per assistant turn ---
    msgs = totals_dict.get("messages", 0)
    cost_usd = totals_dict.get("cost_usd", 0)
    you_per_turn = cost_usd / msgs if msgs else 0
    # No published canonical number, but community typical band: $0.10-$0.50 per turn
    turn_low, turn_high = 0.10, 0.50
    if you_per_turn < turn_low:
        turn_kind, turn_label = "good", "very light per turn"
    elif you_per_turn <= turn_high:
        turn_kind, turn_label = "good", "within normal band"
    elif you_per_turn < turn_high * 2:
        turn_kind, turn_label = "warn", "heavy per turn"
    else:
        turn_kind, turn_label = "bad", "very expensive per turn"

    # --- Trend: did your numbers improve in the recent window? ---
    trend = {}
    if recent_n and prior_n:
        delta_day = (recent_avg - prior_avg) / prior_avg if prior_avg else 0
        trend["daily_cost"] = {
            "recent": round(recent_avg, 2),
            "prior": round(prior_avg, 2),
            "delta_pct": round(delta_day * 100, 1),
            "direction": "up" if delta_day > 0.05 else ("down" if delta_day < -0.05 else "flat"),
        }
    # Recent cache hit (last 7d records vs prior 30d records)
    recent_recs = [r for r in records if r.timestamp.date() >= cutoff_7]
    prior_recs = [r for r in records if cutoff_30 <= r.timestamp.date() < cutoff_7]
    if recent_recs and prior_recs:
        r_tot = aggregate_total(recent_recs)
        p_tot = aggregate_total(prior_recs)
        if r_tot.cache_hit_rate and p_tot.cache_hit_rate:
            d_cache = r_tot.cache_hit_rate - p_tot.cache_hit_rate
            trend["cache_hit_rate"] = {
                "recent": round(r_tot.cache_hit_rate, 4),
                "prior": round(p_tot.cache_hit_rate, 4),
                "delta_pct": round(d_cache * 100, 1),
                "direction": "up" if d_cache > 0.01 else ("down" if d_cache < -0.01 else "flat"),
            }

    # --- The TL;DR sentence: combine unit-economics with volume signal. ---
    per_unit_good = (
        commit_kind in ("good", "neutral")
        and cache_kind == "good"
        and turn_kind in ("good", "neutral")
    )
    heavy_hour = hour_kind == "bad"
    warn_hour = hour_kind == "warn"
    high_hours_per_day = hours_stats["hours_per_active_day"] >= 6.0

    if per_unit_good and (heavy_hour or warn_hour) and high_hours_per_day:
        headline_kind = "good"
        headline_label = "Power user · efficient unit economics"
        _prior_hpd = prior_hours_stats.get("hours_per_active_day", 0)
        _vs_prior = (
            f" (senin son-30g ort. ~{_prior_hpd}h/gün)" if _prior_hpd and _prior_hpd > 0 else ""
        )
        headline_detail = (
            f"You're using {tool_label} ~{hours_stats['hours_per_active_day']}h/day"
            f"{_vs_prior}. Your per-commit ({fmtUsd(you_median_commit)}), "
            f"per-turn ({fmtUsd(you_per_turn)}), and cache hit ({cache_rate*100:.1f}%) "
            f"are all normal or better. Your high daily spend is from volume, not waste."
        )
    elif per_unit_good and not (heavy_hour or warn_hour):
        headline_kind = "good"
        headline_label = "Efficient and within typical volume"
        headline_detail = (
            "All your per-unit metrics are in the green relative to your own history."
        )
    elif not per_unit_good and (heavy_hour or warn_hour):
        headline_kind = "bad"
        headline_label = "High spend AND weak per-unit economics"
        headline_detail = (
            "Both your volume and your per-unit costs are above typical. "
            "First lever: cache hit rate. Second: prompt length per turn."
        )
    elif not per_unit_good:
        headline_kind = "warn"
        headline_label = "Room to tighten per-unit efficiency"
        headline_detail = (
            "Per-turn or cache metrics are below typical. Small prompt or "
            "workflow tweaks could pay off."
        )
    else:
        headline_kind = "neutral"
        headline_label = "Usage profile inconclusive"
        headline_detail = "Not enough recent activity to classify."

    return {
        "headline": {
            "kind": headline_kind,
            "label": headline_label,
            "detail": headline_detail,
        },
        "per_active_hour": {
            "you": you_per_hour,
            "active_hours_last7d": hours_stats["active_hours"],
            "active_days_last7d": hours_stats["active_days"],
            "hours_per_active_day": hours_stats["hours_per_active_day"],
            "cost_last7d": hours_stats["cost_usd"],
            "band_normal_low": HOUR_LIGHT,
            "band_normal_high": HOUR_NORMAL_HI,
            "band_heavy": HOUR_HEAVY,
            "verdict_kind": hour_kind,
            "verdict_label": hour_label,
            "prior_per_hour": prior_hours_stats["cost_per_hour"],
            "prior_hours_per_day": prior_hours_stats["hours_per_active_day"],
        },
        "per_active_day": {
            "you_avg_recent7": round(recent_avg, 2),
            # Personal anchors (user's own history) — NOT an industry constant.
            "baseline_avg": round(day_anchor, 2),
            "baseline_p90": round(personal_day_p90, 2),
            "samples_recent": recent_n,
            "verdict_kind": day_kind,
            "verdict_label": day_label,
        },
        "per_commit": {
            "you_mean": round(you_per_commit, 2),
            "you_median": round(you_median_commit, 2),
            "matched_commits": matched,
            "verdict_kind": commit_kind,
            "verdict_label": commit_label,
        },
        "cache_hit_rate": {
            "you": round(cache_rate, 4),
            "target_good": target_good,
            "target_excellent": target_excellent,
            "verdict_kind": cache_kind,
            "verdict_label": cache_label,
        },
        "per_turn": {
            "you": round(you_per_turn, 4),
            "baseline_low": turn_low,
            "baseline_high": turn_high,
            "turns": msgs,
            "verdict_kind": turn_kind,
            "verdict_label": turn_label,
        },
        "trend": trend,
    }


def fmtUsd(v: float) -> str:
    """Tiny helper for headline detail strings."""
    try:
        return f"${float(v):.2f}"
    except (TypeError, ValueError):
        return "$?"


def burn_rate_timeseries(
    records: list[UsageRecord],
    bucket_hours: float,
    num_buckets: int,
) -> list[dict]:
    """Trading-chart style time series: equal-width buckets ending at `now`.

    Each bucket spans `bucket_hours` hours. We sum cost in that bucket and
    divide by the bucket length to get $/hour for that interval. This lets
    the dashboard plot the same dataset at 1h / 4h / 12h / 1d resolution.

    Moving averages are computed client-side over BAR COUNT (not calendar
    days), matching trader conventions (e.g. MA(7) at 1h = last 7 hours,
    MA(7) at 1d = last 7 days — both "last 7 bars").
    """
    now = datetime.now(timezone.utc)
    earliest = now - timedelta(hours=bucket_hours * num_buckets)

    # Bucket index 0 = oldest (start), num_buckets-1 = newest (ending at now).
    costs = [0.0] * num_buckets
    tokens = [0] * num_buckets
    records_count = [0] * num_buckets

    for r in records:
        if r.timestamp < earliest or r.timestamp > now:
            continue
        offset_h = (r.timestamp - earliest).total_seconds() / 3600.0
        idx = int(offset_h / bucket_hours)
        if idx >= num_buckets:
            idx = num_buckets - 1
        if idx < 0:
            continue
        costs[idx] += estimate_cost_usd(
            r.model,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cache_read_tokens=r.cache_read_tokens,
            cache_creation_tokens=r.cache_creation_tokens,
        )
        tokens[idx] += (r.input_tokens + r.output_tokens
                        + r.cache_creation_tokens + r.cache_read_tokens)
        records_count[idx] += 1

    out = []
    for i in range(num_buckets):
        b_start = earliest + timedelta(hours=bucket_hours * i)
        b_end = b_start + timedelta(hours=bucket_hours)
        out.append({
            "start": b_start.isoformat(),
            "end": b_end.isoformat(),
            "cost": round(costs[i], 4),
            "tokens": tokens[i],
            "records": records_count[i],
            "cost_per_hour": round(costs[i] / bucket_hours, 2),
        })
    return out


def daily_burn_rates(records: list[UsageRecord], days_back: int = 90) -> list[dict]:
    """Per-day $/active-hour — the true burn rate, not raw spend.

    Active hours computed by bucketing records into 5-minute slots and
    counting unique slots (same approach as `active_hours_stats`). This
    way a day where you worked 1h and spent $20 has the SAME burn rate
    as a day where you worked 10h and spent $200 — both $20/hour.

    The "cost_per_hour" series is what should be plotted as the trend.
    "cost_usd" is included for reference (= what /day chart shows).
    """
    BUCKET_SEC = 5 * 60
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).date()

    by_day: dict[str, dict] = defaultdict(lambda: {"cost": 0.0, "buckets": set(), "tokens": 0})
    for r in records:
        d = r.timestamp.astimezone(timezone.utc).date()
        if d < cutoff:
            continue
        key = d.isoformat()
        by_day[key]["cost"] += estimate_cost_usd(
            r.model,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cache_read_tokens=r.cache_read_tokens,
            cache_creation_tokens=r.cache_creation_tokens,
        )
        by_day[key]["buckets"].add(int(r.timestamp.timestamp() // BUCKET_SEC))
        by_day[key]["tokens"] += (
            r.input_tokens + r.output_tokens
            + r.cache_creation_tokens + r.cache_read_tokens
        )

    out = []
    for d in sorted(by_day.keys()):
        s = by_day[d]
        active_min = len(s["buckets"]) * (BUCKET_SEC / 60)
        active_hr = active_min / 60.0
        burn = (s["cost"] / active_hr) if active_hr > 0 else 0.0
        out.append({
            "date": d,
            "cost_usd": round(s["cost"], 2),
            "active_hours": round(active_hr, 2),
            "cost_per_hour": round(burn, 2),
            "tokens": s["tokens"],
        })
    return out


def live_active_models(records: list[UsageRecord], lookback_min: int = 15) -> dict:
    """For each model active in the last N minutes, report its live token rate
    and how long since the last turn. Lets the UI show several models burning
    in parallel rather than just the most-recent one.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=lookback_min)
    elapsed_min = lookback_min  # assume full window when computing rate

    by_model: dict[str, dict] = defaultdict(lambda: {
        "billable_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "messages": 0,
        "last_seen": None,
    })

    for r in records:
        if r.timestamp < cutoff:
            continue
        key = r.model or "unknown"
        m = by_model[key]
        m["billable_tokens"] += r.input_tokens + r.output_tokens + r.cache_creation_tokens
        m["total_tokens"]    += (r.input_tokens + r.output_tokens
                                 + r.cache_creation_tokens + r.cache_read_tokens)
        m["cost"]            += estimate_cost_usd(
            r.model,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cache_read_tokens=r.cache_read_tokens,
            cache_creation_tokens=r.cache_creation_tokens,
        )
        m["messages"] += 1
        if m["last_seen"] is None or r.timestamp > m["last_seen"]:
            m["last_seen"] = r.timestamp

    out = []
    elapsed_hours = elapsed_min / 60.0
    for model_id, stats in by_model.items():
        last = stats["last_seen"]
        out.append({
            "model_id": model_id,
            "billable_tokens_recent": stats["billable_tokens"],
            "tokens_per_min": round(stats["billable_tokens"] / elapsed_min, 0) if elapsed_min else 0,
            "messages": stats["messages"],
            "cost_recent": round(stats["cost"], 2),
            "cost_per_hour": round(stats["cost"] / elapsed_hours, 2) if elapsed_hours > 0 else 0.0,
            "last_seen": last.isoformat() if last else None,
            "seconds_since_last": int((now - last).total_seconds()) if last else None,
        })
    out.sort(key=lambda x: x.get("seconds_since_last") or 999999)
    return {"lookback_minutes": lookback_min, "models": out, "now": now.isoformat()}


def forecast(records: list[UsageRecord], daily: list[dict]) -> dict:
    """Predictive forecasts: what does today/week/month look like at current pace?

    'Today' uses your last 4 hours of burn rate × hours left in UTC day.
    'Week' uses your last 7 active days median × days left in week.
    'Month' uses last 14 active days median × days left in month.

    Returns end-of-period total projections AND the marginal "what's still to come" delta.
    """
    from datetime import date as _date
    now = datetime.now(timezone.utc)
    today = now.date()

    # --- Today ---
    # Burn rate averaged over the last 2 hours (was 4h — shortened so it
    # reflects "right now" more sharply when activity ramps up or stops).
    last_2h_cutoff = now - timedelta(hours=2)
    recent_recs = [r for r in records if r.timestamp >= last_2h_cutoff]
    hours_elapsed_2h = (now - last_2h_cutoff).total_seconds() / 3600.0
    last_2h_cost = sum(
        estimate_cost_usd(
            r.model,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cache_read_tokens=r.cache_read_tokens,
            cache_creation_tokens=r.cache_creation_tokens,
        )
        for r in recent_recs
    )
    burn_rate_now = last_2h_cost / hours_elapsed_2h if hours_elapsed_2h > 0 else 0

    end_of_day_utc = datetime.combine(today + timedelta(days=1), datetime.min.time()).replace(tzinfo=timezone.utc)
    hours_left_today = (end_of_day_utc - now).total_seconds() / 3600.0

    today_so_far = next(
        (d["cost_usd"] for d in daily if d["date"] == today.isoformat()),
        0.0,
    )
    # Use typical daily * proportion-of-day-remaining instead of naive
    # extrapolation — assumes the rest of today behaves like a typical day
    # for you, NOT that you stay glued to the keyboard for 23 hours.
    recent_active = sorted(
        [d["cost_usd"] for d in daily if d["cost_usd"] > 0.5][-7:],
        reverse=True,
    )
    typical_daily_for_today = statistics.median(recent_active) if recent_active else 0
    today_remaining = typical_daily_for_today * (hours_left_today / 24)
    today_projected = today_so_far + today_remaining

    # --- Week (calendar week, Mon-Sun) ---
    week_start = today - timedelta(days=today.weekday())
    week_days_left = 7 - (today - week_start).days - 1  # days after today
    days_active_this_week = [d for d in daily if week_start.isoformat() <= d["date"] <= today.isoformat()]
    week_so_far = sum(d["cost_usd"] for d in days_active_this_week)
    # Predict remaining days using the median of the last 7 active days
    recent_active = sorted(
        [d["cost_usd"] for d in daily if d["cost_usd"] > 0.5][-7:],
        reverse=True,
    )
    typical_daily = statistics.median(recent_active) if recent_active else 0
    week_projected = week_so_far + (typical_daily * week_days_left)

    # --- Month ---
    month_start = today.replace(day=1)
    if today.month == 12:
        next_month = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month = today.replace(month=today.month + 1, day=1)
    month_days_left = (next_month - today).days - 1  # days after today
    days_active_month = [d for d in daily if month_start.isoformat() <= d["date"] <= today.isoformat()]
    month_so_far = sum(d["cost_usd"] for d in days_active_month)
    month_projected = month_so_far + (typical_daily * month_days_left)

    # Burn rate at several lookback windows so the UI can let the user pick.
    # ALSO break down each window's burn rate by model so the user can see
    # which model is eating most of the dollars-per-hour.
    burn_rates_by_hours = {}
    burn_rates_by_hours_by_model: dict[str, list[dict]] = {}
    for hours in (0.25, 1, 2, 4, 6):
        c = now - timedelta(hours=hours)
        cost = 0.0
        per_model: dict[str, float] = defaultdict(float)
        for r in records:
            if r.timestamp < c:
                continue
            rc = estimate_cost_usd(
                r.model,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                cache_read_tokens=r.cache_read_tokens,
                cache_creation_tokens=r.cache_creation_tokens,
            )
            cost += rc
            per_model[r.model or "unknown"] += rc
        burn_rates_by_hours[str(hours)] = round(cost / hours, 2)
        # Sort models by $/hr desc, drop tiny contributions (< $0.01/hr).
        rows = []
        for m, c_total in per_model.items():
            per_hr = c_total / hours
            if per_hr < 0.01:
                continue
            rows.append({
                "model_id": m,
                "cost_total": round(c_total, 2),
                "cost_per_hour": round(per_hr, 2),
                "share": round(per_hr / (cost / hours), 3) if cost > 0 else 0,
            })
        rows.sort(key=lambda x: x["cost_per_hour"], reverse=True)
        burn_rates_by_hours_by_model[str(hours)] = rows

    return {
        "now": now.isoformat(),
        "burn_rate_per_hour_recent": round(burn_rate_now, 2),
        "burn_rate_recent_window_hours": round(hours_elapsed_2h, 2),
        "burn_rates_by_hours": burn_rates_by_hours,
        "burn_rates_by_hours_by_model": burn_rates_by_hours_by_model,
        "today": {
            "so_far": round(today_so_far, 2),
            "projected_eod": round(today_projected, 2),
            "remaining_projection": round(today_remaining, 2),
            "hours_left": round(hours_left_today, 1),
            "method": "remaining hours × typical daily rate",
        },
        "week": {
            "so_far": round(week_so_far, 2),
            "projected_eow": round(week_projected, 2),
            "remaining_projection": round(typical_daily * week_days_left, 2),
            "days_left": week_days_left,
            "typical_daily": round(typical_daily, 2),
        },
        "month": {
            "so_far": round(month_so_far, 2),
            "projected_eom": round(month_projected, 2),
            "remaining_projection": round(typical_daily * month_days_left, 2),
            "days_left": month_days_left,
        },
    }


def burn_rate_zones(window_baseline_data: dict) -> dict:
    """Speedometer zone boundaries based on user's own historical windows.

    Cost in a 5h window / 5 = average $/hr during that window.
    """
    if not window_baseline_data.get("samples"):
        # Not enough personal history yet → default thresholds (NOT derived from
        # the user's own data). Flag it so the UI labels them as "varsayılan"
        # instead of presenting them as measured personal zones.
        return {
            "typical": 4.0,
            "busy": 12.0,
            "heavy": 25.0,
            "max": 50.0,
            "insufficient_history": True,
        }
    typical = (window_baseline_data.get("cost_p50") or 0) / 5.0
    busy = (window_baseline_data.get("cost_p90") or 0) / 5.0
    return {
        "typical": round(typical, 2),
        "busy": round(busy, 2),
        "heavy": round(busy * 2, 2),
        "max": round(busy * 3, 2),
    }


def errors_summary(error_events: list[dict], lookback_hours: int = 24) -> dict:
    """Aggregate tool_result errors over the lookback window.

    Returns:
      total          : count in window
      by_category    : ordered list of {category, count, share}
      by_session     : count per session_id (for joining with by_session)
      recent_samples : last 8 errors verbatim (snippet + category)
      total_lifetime : grand total across all loaded data
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)

    by_cat = defaultdict(int)
    by_session = defaultdict(int)
    recent = []
    total = 0
    total_lifetime = len(error_events or [])

    for e in error_events or []:
        ts_raw = e.get("timestamp")
        if not ts_raw:
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except (ValueError, TypeError):
            continue
        if ts < cutoff:
            continue
        total += 1
        by_cat[e.get("category", "other_error")] += 1
        sid = e.get("session_id") or ""
        if sid:
            by_session[sid] += 1
        recent.append({
            "category": e.get("category"),
            "snippet": e.get("snippet"),
            "session_id": sid,
            "timestamp": ts_raw,
        })

    cat_list = [
        {"category": k, "count": v, "share": round(v / total, 3) if total else 0}
        for k, v in sorted(by_cat.items(), key=lambda x: -x[1])
    ]
    recent.sort(key=lambda r: r["timestamp"], reverse=True)

    return {
        "lookback_hours": lookback_hours,
        "total": total,
        "total_lifetime": total_lifetime,
        "by_category": cat_list,
        "by_session": dict(by_session),
        "recent_samples": recent[:8],
    }


def cache_efficiency(by_model_full: list[dict]) -> dict:
    """Cache-efficiency paneli — rakiplerin atladığı yüksek-sinyal metrik.

    Cache read maliyeti input'un ~%10'u (90% indirim). "Cache olmasaydı bu
    token'lar tam input fiyatından gidecekti" → tasarruf = cache_read ×
    (input_rate − cache_read_rate). Per-model fiyatla gerçek $ hesaplanır.

    Döndürür: hit_rate, cached_tokens, fresh_input_tokens, usd_on_cache (cache
    read'in gerçek maliyeti), usd_saved (cache sayesinde kaçınılan), discount_pct
    (cache'in toplam input-maliyetini ne kadar düşürdüğü), full_equiv_usd.
    """
    cached_tok = 0
    fresh_in_tok = 0
    usd_on_cache = 0.0
    usd_saved = 0.0
    for m in by_model_full:
        mid = m.get("model_id") or ""
        cr = int(m.get("cache_read_tokens") or 0)
        fin = int(m.get("input_tokens") or 0)
        p = price_for(mid)
        cached_tok += cr
        fresh_in_tok += fin
        usd_on_cache += cr * p.cache_read_per_mtok / 1_000_000.0
        # Cache olmasaydı: cr token tam input fiyatından
        usd_saved += cr * (p.input_per_mtok - p.cache_read_per_mtok) / 1_000_000.0
    denom = cached_tok + fresh_in_tok
    hit_rate = (cached_tok / denom) if denom > 0 else 0.0
    full_equiv = usd_on_cache + usd_saved        # cache yokken input-okuma maliyeti
    discount_pct = (usd_saved / full_equiv) if full_equiv > 0 else 0.0
    return {
        "hit_rate": round(hit_rate, 4),
        "cached_tokens": cached_tok,
        "fresh_input_tokens": fresh_in_tok,
        "usd_on_cache": round(usd_on_cache, 2),
        "usd_saved": round(usd_saved, 2),
        "discount_pct": round(discount_pct, 4),
        "full_equiv_usd": round(full_equiv, 2),
    }


def build_report(
    records: list[UsageRecord],
    plan: Optional[str] = None,
    user_intents: Optional[dict[str, str]] = None,
    error_events: Optional[list[dict]] = None,
    source: str = "claude",
) -> dict:
    """One call → everything the dashboard needs."""
    tool_label = "Codex" if source == "codex" else "Claude Code"
    daily = aggregate_by_day(records)
    windows = detect_billing_windows(records)
    plan_guess = infer_plan(windows)
    chosen_plan = plan or plan_guess["guess"]
    totals_dict = aggregate_total(records).to_dict()
    git_attr = attribute_to_commits(records, since_days=14)
    # 2026-05-28 (Cihan paradigm): "SON AKTİVİTE" panelinde session
    # lifetime toplamı (5 milyar token) yerine yalnızca son 2 saatte
    # yakılan token'ı göster. recent_cutoff aggregate_by_session'a
    # geçirilirse her item'a recent_window_* field'ları eklenir.
    recent_cutoff = datetime.now(timezone.utc) - timedelta(hours=2)

    # RECENT TURNS — Cihan paradigm 2026-05-29 v5: 1 TURN = 1 USER MESAJI +
    # tüm assistant cevap akışı (thinking + tool calls + final text).
    # JSONL'de bir cevabım 3-5 assistant record üretir (her tool call ayrı
    # record); hepsi aynı turn_id'yi paylaşır (parser.py'da ancestor
    # zincirinden user msg uuid çekildi). recent_turns artık turn_id
    # bazında AGGREGATE — 1 user mesajı = 1 satır.
    # Her satır = o turn'de yakılan tüm token'ların saf net (cost-weighted)
    # toplamı. Cache_read maliyeti input'un 0.10x'i (1/10), output 5x,
    # cache_creation 1.25x. Cihan: "anlık her turne göre, toplayarak gitmesin".
    turn_buckets: dict[str, dict] = {}
    for r in records:
        # synthetic/placeholder kayıtlar (model "<synthetic>", 0 token) recent listesinde
        # SLOT işgal edip gerçek turn'leri top-30'dan dışarı itiyordu (kart 2 satır görünüp
        # altı boş kalıyordu). Frontend zaten "<" ile başlayanı gizliyor; burada top-30
        # KESİMİNDEN ÖNCE eleyerek 30 GERÇEK turn'ün gelmesini sağlıyoruz.
        if not r.model or str(r.model).startswith("<"):
            continue
        tid = r.turn_id or r.message_id or r.uuid
        if not tid:
            continue
        b = turn_buckets.get(tid)
        if b is None:
            b = {
                "turn_id": tid,
                "session_id": r.session_id,
                "project_label": r.project_label,
                "model": r.model,
                "device": getattr(r, "device", "mac"),
                "last_ts": r.timestamp,
                "totals": Totals(),
                "eff": 0,
                "records": 0,
            }
            turn_buckets[tid] = b
        b["totals"].add(r)
        # Pricing-aware effective (Claude output 5x, Codex gpt5 output 8x —
        # her model kendi input fiyatına oranlanır).
        b["eff"] += effective_tokens(
            r.model,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cache_read_tokens=r.cache_read_tokens,
            cache_creation_tokens=r.cache_creation_tokens,
        )
        b["records"] += 1
        if r.timestamp > b["last_ts"]:
            b["last_ts"] = r.timestamp
            b["model"] = r.model       # use the latest record's model

    sorted_turns = sorted(turn_buckets.values(), key=lambda b: b["last_ts"], reverse=True)
    recent_turns_data = []
    for b in sorted_turns[:30]:
        td = b["totals"].to_dict()
        recent_turns_data.append({
            "timestamp":       b["last_ts"].isoformat(),
            "turn_id":         b["turn_id"],
            "session_id":      b["session_id"],
            "project_label":   b["project_label"],
            "model":           b["model"],
            "device":          b["device"],
            "effective_tokens": b["eff"],
            "total_tokens":    td["total_tokens"],
            "input_tokens":    td["input_tokens"],
            "output_tokens":   td["output_tokens"],
            "cache_creation_tokens": td["cache_creation_tokens"],
            "cache_read_tokens":     td["cache_read_tokens"],
            "cost_usd":        td["cost_usd"],
            "cache_hit_rate":  td["cache_hit_rate"],
            "assistant_record_count": b["records"],
        })

    # BY DEVICE — Cihan paradigm 2026-05-29: Mac vs PC ayrı sayaç.
    # Her device için lifetime + son 1 saat + bugün totals.
    now_utc = datetime.now(timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    h1_cutoff = now_utc - timedelta(hours=1)
    # Optimize: tek pass — her record için cihazına ve hangi bucket'lara
    # düştüğüne göre cost ekle. Önceki implementasyon her cihaz × 5 hour
    # için ayrı iter ediyordu → 10× iterasyon. Bu 366MB+ PC JSONL'lerle
    # 18s API gecikmesi yaratıyordu.
    # Key format: "0.25", "1", "2", "4", "6" — frontend butonları data-h ile
    # bu key'leri okuyor. Karma int+float liste tut, str(int(h)) if h.is_integer.
    def _hk(h):
        return str(int(h)) if float(h).is_integer() else str(h)
    burn_hours_tuple = (0.25, 1, 2, 4, 6)
    burn_cutoffs = {_hk(h): (now_utc - timedelta(hours=h), float(h)) for h in burn_hours_tuple}
    by_device_totals: dict[str, dict] = {}
    for dev in ("mac", "pc"):
        by_device_totals[dev] = {
            "lifetime": Totals(),
            "today":    Totals(),
            "h1":       Totals(),
            "burn_cost": {k: 0.0 for k in burn_cutoffs},
        }
    for r in records:
        dev = getattr(r, "device", "mac")
        if dev not in by_device_totals:
            continue
        slot = by_device_totals[dev]
        slot["lifetime"].add(r)
        if r.timestamp >= today_start:
            slot["today"].add(r)
        if r.timestamp >= h1_cutoff:
            slot["h1"].add(r)
        cost = None
        for k, (cutoff, _hf) in burn_cutoffs.items():
            if r.timestamp >= cutoff:
                if cost is None:
                    cost = estimate_cost_usd(
                        r.model,
                        input_tokens=r.input_tokens,
                        output_tokens=r.output_tokens,
                        cache_read_tokens=r.cache_read_tokens,
                        cache_creation_tokens=r.cache_creation_tokens,
                    )
                slot["burn_cost"][k] += cost
    # Finalize
    for dev in ("mac", "pc"):
        slot = by_device_totals[dev]
        h1_d = slot["h1"]
        burn_buckets = {k: round(slot["burn_cost"][k] / burn_cutoffs[k][1], 2)
                        for k in burn_cutoffs}
        by_device_totals[dev] = {
            "lifetime": slot["lifetime"].to_dict(),
            "today":    slot["today"].to_dict(),
            "h1":       h1_d.to_dict(),
            "burn_rate_usd_per_hour": round(h1_d.cost_usd, 4),
            "burn_rates_by_hours": burn_buckets,
        }

    bmf = aggregate_by_specific_model(records)
    return {
        "recent_turns": recent_turns_data,
        "by_device": by_device_totals,
        "totals": totals_dict,
        "cache_efficiency": cache_efficiency(bmf),
        "daily": daily,
        "by_model": aggregate_by_model(records),
        "by_model_full": bmf,
        "by_project": aggregate_by_project(records),
        "by_session": aggregate_by_session(
            records, user_intents, recent_cutoff=recent_cutoff,
        )[:200],  # cap for UI
        "by_tool": aggregate_by_tool(records),
        "windows": windows[-30:],                            # last 30 windows
        "current_window": current_window_status(windows, chosen_plan),
        "window_baseline": window_baseline(windows),
        "plan_inference": plan_guess,
        "chosen_plan": chosen_plan,
        "baseline": baseline_stats(records),
        "anomalies": daily_anomalies(daily),
        "git_attribution": git_attr,
        "fuel_efficiency": fuel_efficiency(records, daily, git_attr, totals_dict, tool_label=tool_label),
        "weekly_cap": weekly_cap_status(records, daily),
        "errors": errors_summary(error_events or [], lookback_hours=24),
        "daily_burn_rates": daily_burn_rates(records, days_back=90),
        # Trading-chart style multi-timeframe burn rate data. UI picks which
        # one to plot, then computes moving averages client-side.
        "burn_rate_timeseries": {
            "1":  burn_rate_timeseries(records, bucket_hours=1,  num_buckets=120),
            "4":  burn_rate_timeseries(records, bucket_hours=4,  num_buckets=120),
            "6":  burn_rate_timeseries(records, bucket_hours=6,  num_buckets=120),
            "12": burn_rate_timeseries(records, bucket_hours=12, num_buckets=120),
            "24": burn_rate_timeseries(records, bucket_hours=24, num_buckets=120),
        },
        "forecast": forecast(records, daily),
        "burn_rate_zones": burn_rate_zones(window_baseline(windows)),
        "live_active_models_by_window": {
            "15":   live_active_models(records, lookback_min=15),
            "60":   live_active_models(records, lookback_min=60),
            "300":  live_active_models(records, lookback_min=300),
            "1440": live_active_models(records, lookback_min=1440),
        },
        "plan_limits": PLAN_LIMITS,
        "industry_reference": INDUSTRY_REFERENCE,
        "record_count": len(records),
    }
