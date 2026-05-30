"""Codex (OpenAI gpt-5.x) session parser for codex_meter.

Codex CLI writes session rollouts to:
    ~/.codex/sessions/YYYY/MM/DD/rollout-<ISO>-<uuid>.jsonl

Each line: {timestamp, type, payload}. We care about:
  - session_meta  → payload.{id, cwd, git}
  - turn_context  → payload.model (gpt-5.5), turn_id
  - event_msg.token_count → payload.info.last_token_usage (per-RESPONSE delta;
    Codex turn başına ~13 event yayar ve akışta aynı delta'yı birebir tekrar
    edebilir → ardışık birebir-aynı olanlar duplicate sayılıp atlanır)
  - event_msg.user_message → first one = the session "intent"

Token mapping → ccmeter UsageRecord:
  fresh input = input_tokens - cached_input_tokens
  cache_read  = cached_input_tokens
  output      = output_tokens (reasoning dahil)
  cache_creation = 0  (OpenAI auto-cache, no explicit write billing)

PERFORMANCE: Codex sessions can be HUGE (one 7.3GB file observed; 16GB total).
We (1) substring-filter raw lines before json.loads to skip giant response_item
lines, and (2) keep an incremental cache keyed by (path, mtime, size) so each
completed session file is parsed at most once. Only the actively-growing file
is re-read on refresh.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator, Optional

from .parser import UsageRecord, _parse_timestamp

CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
_CACHE_PATH = Path.home() / ".codex" / ".codex_meter_cache.json"
# parse mantığı değişince bump et → farklı "v" taşıyan eski cache entry'leri yok
# sayılır (mtime/size aynı olsa bile yeniden parse edilir, stale turn dönmez).
# v2: ardışık birebir-aynı token_count delta'ları atlanır (anti-şişme; hesap
# geneli ground truth'a 1.066x → 1.004x yaklaşır).
_PARSE_VERSION = 2

# Substring pre-filter — only json-parse lines that might carry what we need.
# Avoids parsing the giant response_item text lines (the bulk of the 16GB).
_INTEREST = ('"token_count"', '"turn_context"', '"session_meta"', '"user_message"')


def iter_codex_files(root: Path = CODEX_SESSIONS_DIR) -> Iterator[Path]:
    if not root.exists():
        return
    yield from sorted(root.rglob("rollout-*.jsonl"))


def _line_of_interest(raw: str) -> bool:
    return any(tok in raw for tok in _INTEREST)


def _epoch(iso: Optional[str]) -> float:
    """ISO-8601 → epoch seconds. 0.0 on failure. (Date.now yasak değil ama
    burada parse ediyoruz, current time gerekmiyor.)"""
    if not iso:
        return 0.0
    try:
        from datetime import datetime, timezone
        s = iso[:-1] + "+00:00" if iso.endswith("Z") else iso
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0


def parse_codex_file(path: Path) -> tuple[dict, list[list], Optional[str], Optional[dict]]:
    """Parse one Codex rollout file → KOMPAKT form (RAM disiplini).

    Returns (meta, turns, intent, last_rate_limits):
      - meta: {"sid", "cwd", "branch"} — dosya-seviyesi sabitler (record başına
        tekrar ETMEZ; eski 198MB cache'in ana şişme sebebi buydu).
      - turns: list of [ts, model, turn_id, fresh_in, out, cache_read] — kompakt
        tuple, dict değil. token_count event başına bir tane.
      - intent: first user_message text (capped).
      - last_rate_limits: son token_count event'inin rate_limits'i + "_ts".
        Codex Pro'nun gerçek kısıtı (5h + haftalık used_percent).
    """
    session_id = ""
    cwd = ""
    git_branch = ""
    cur_model = ""
    cur_turn_id = ""
    intent: Optional[str] = None
    turns: list[list] = []
    last_rate_limits: Optional[dict] = None
    idx = 0
    prev_sig: Optional[tuple] = None   # son eklenen delta imzası (anti-duplicate)

    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                if not _line_of_interest(raw):
                    continue
                try:
                    r = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                typ = r.get("type")
                payload = r.get("payload") or {}
                if typ == "session_meta":
                    session_id = str(payload.get("id") or "")
                    cwd = str(payload.get("cwd") or "")
                    git = payload.get("git") or {}
                    if isinstance(git, dict):
                        git_branch = str(git.get("branch") or "")
                elif typ == "turn_context":
                    m = payload.get("model")
                    if isinstance(m, str) and m:
                        cur_model = m
                    tid = payload.get("turn_id")
                    if isinstance(tid, str) and tid:
                        cur_turn_id = tid
                elif typ == "event_msg":
                    pt = payload.get("type")
                    if pt == "user_message" and intent is None:
                        txt = payload.get("message") or payload.get("text") or ""
                        if isinstance(txt, str) and txt.strip():
                            intent = txt.strip()[:240]
                    elif pt == "token_count":
                        info = payload.get("info") or {}
                        # rate_limits her token_count event'inde gelir — sonuncuyu tut.
                        rl = payload.get("rate_limits")
                        if isinstance(rl, dict) and rl.get("primary"):
                            last_rate_limits = dict(rl)
                            last_rate_limits["_ts"] = _epoch(r.get("timestamp"))
                        last = info.get("last_token_usage") or {}
                        if not last:
                            continue
                        in_tok = int(last.get("input_tokens") or 0)
                        cached = int(last.get("cached_input_tokens") or 0)
                        out_tok = int(last.get("output_tokens") or 0)
                        # ANTI-DUPLICATE: Codex akış sırasında aynı last_token_usage'ı
                        # ardışık birebir tekrar yayabilir (ölçüm: event'lerin %5.2'si).
                        # Bunları saymak hesabı %6.6 şişirir. Ardışık birebir-aynı
                        # (in,cached,out) delta'yı atla — sıfır-kullanım event'i değilse.
                        sig = (in_tok, cached, out_tok)
                        if sig == prev_sig and (in_tok or out_tok):
                            continue
                        prev_sig = sig
                        fresh_in = max(0, in_tok - cached)
                        ts = r.get("timestamp") or ""
                        # KOMPAKT turn tuple: [ts, model, turn_id, fresh_in, out, cache_read]
                        # session_id/cwd/git_branch dosya-seviyesinde meta'ya hoist
                        # edilir (record başına tekrar etmez → cache 198MB→~birkaç MB).
                        turns.append([
                            ts, cur_model or "gpt-5",
                            cur_turn_id or f"{session_id}:{idx}",
                            fresh_in, out_tok, cached,
                        ])
                        idx += 1
    except OSError:
        pass
    meta = {"sid": session_id or path.stem, "cwd": cwd, "branch": git_branch}
    return meta, turns, intent, last_rate_limits


def _load_cache() -> dict:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    try:
        _CACHE_PATH.write_text(json.dumps(cache))
    except OSError:
        pass


def _compact_to_usages(meta: dict, turns: list, device: str):
    """KOMPAKT (meta + turn tuples) → UsageRecord generator.

    meta = {sid, cwd, branch}; turn = [ts, model, turn_id, fresh_in, out, cr].
    project_label/label dosya başına bir kez hesaplanır (record başına değil).
    Generator: tüm liste aynı anda materialize edilmez → bellek dostu.
    """
    sid = meta.get("sid") or ""
    cwd = meta.get("cwd") or ""
    branch = meta.get("branch") or ""
    label = Path(cwd).name or cwd or "unknown"
    for i, t in enumerate(turns):
        ts, model, turn_id, fin, out, cr = t
        yield UsageRecord(
            timestamp=_parse_timestamp(ts),
            session_id=sid,
            project_dir=cwd,
            project_label=label,
            git_branch=branch,
            model=model or "gpt-5",
            message_id=f"{sid}:{i}",
            input_tokens=int(fin or 0),
            output_tokens=int(out or 0),
            cache_creation_tokens=0,
            cache_read_tokens=int(cr or 0),
            tool_names=[],
            source_file="",
            uuid=f"{sid}:{i}",
            parent_uuid="",
            turn_id=turn_id or f"{sid}:{i}",
            device=device,
        )


def load_codex_records(
    root: Path = CODEX_SESSIONS_DIR,
    extra_roots: Optional[list[Path]] = None,
) -> tuple[list[UsageRecord], dict, dict[str, str]]:
    """Load Codex usage records with an incremental (path,mtime,size) cache.

    Returns (records sorted by time, stats, user_intents) — same shape as
    parser.load_records so the analytics pipeline is source-agnostic.
    """
    cache = _load_cache()
    new_cache: dict = {}
    records: list[UsageRecord] = []
    user_intents: dict[str, str] = {}
    files = 0
    reparsed = 0
    latest_rl_by_dev: dict[str, dict] = {}   # device → most-recent rate_limits

    roots = [(root, "mac")]
    for er in (extra_roots or []):
        roots.append((er, "pc"))

    if not root.exists() and not (extra_roots and any(r.exists() for r in extra_roots)):
        return [], {
            "files_scanned": 0, "reparsed": 0,
            "root": str(root), "root_exists": False,
            "source": "codex",
        }, user_intents

    for scan_root, device in roots:
        if not scan_root.exists():
            continue
        for path in iter_codex_files(scan_root):
            files += 1
            key = str(path)
            try:
                st = path.stat()
                sig = [st.st_mtime, st.st_size]
            except OSError:
                continue
            cached = cache.get(key)
            if (cached and cached.get("sig") == sig
                    and cached.get("v") == _PARSE_VERSION and "meta" in cached):
                meta = cached["meta"]
                turns = cached.get("turns", [])
                intent = cached.get("intent")
                rl = cached.get("rate_limits")
            else:
                meta, turns, intent, rl = parse_codex_file(path)
                reparsed += 1
            # KOMPAKT cache: meta (sid/cwd/branch 1 kez) + turns (tuple list).
            new_cache[key] = {"sig": sig, "v": _PARSE_VERSION, "meta": meta,
                              "turns": turns, "intent": intent,
                              "device": device, "rate_limits": rl}
            if rl and isinstance(rl, dict):
                prev = latest_rl_by_dev.get(device)
                if prev is None or (rl.get("_ts", 0) > prev.get("_ts", 0)):
                    latest_rl_by_dev[device] = {**rl, "device": device}
            sid = meta.get("sid") if meta else None
            if intent and sid and sid not in user_intents:
                user_intents[sid] = intent
            for rec in _compact_to_usages(meta, turns, device):
                records.append(rec)

    _save_cache(new_cache)
    records.sort(key=lambda r: r.timestamp)
    return records, {
        "files_scanned": files,
        "reparsed": reparsed,
        "root": str(root),
        "root_exists": True,
        "extra_roots": [str(r) for r in (extra_roots or [])],
        "source": "codex",
        "record_count": len(records),
        "rate_limits": latest_rl_by_dev,   # {device: {primary, secondary, plan_type, ...}}
    }, user_intents
