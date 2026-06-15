"""Codex (OpenAI gpt-5.x) session parser for Burnmeter.

Codex CLI writes session rollouts to:
    ~/.codex/sessions/YYYY/MM/DD/rollout-<ISO>-<uuid>.jsonl

Each line: {timestamp, type, payload}. We care about:
  - session_meta  → payload.{id, cwd, git}
  - turn_context  → payload.model (gpt-5.5), turn_id
  - event_msg.token_count → payload.info.last_token_usage (per-RESPONSE delta;
    Codex turn başına ~13 event yayar ve akışta aynı delta'yı birebir tekrar
    edebilir → ardışık birebir-aynı olanlar duplicate sayılıp atlanır)
  - event_msg.user_message → first one = the session "intent"

Token mapping → burnmeter UsageRecord:
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

from .parser import UsageRecord, _parse_timestamp, _local_device

CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
_CACHE_PATH = Path.home() / ".codex" / ".burnmeter_codex_cache.json"
_OLD_CACHE_PATH = Path.home() / ".codex" / ".codex_meter_cache.json"   # pre-rename → auto-migrated
# parse mantığı değişince bump et → farklı "v" taşıyan eski cache entry'leri yok
# sayılır (mtime/size aynı olsa bile yeniden parse edilir, stale turn dönmez).
# v2: ardışık birebir-aynı token_count delta'ları atlanır (anti-şişme).
# v3: byte-offset incremental parse (cache entry'sine offset+resume eklendi) —
# aktif 7.7GB dosya artık baştan değil, sadece eklenen byte'lardan parse edilir.
_PARSE_VERSION = 3

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


def parse_codex_file(path: Path, start_offset: int = 0, resume: Optional[dict] = None
                     ) -> tuple[dict, list, Optional[str], Optional[dict], int, dict]:
    """Parse a Codex rollout file → KOMPAKT form (RAM disiplini).

    INCREMENTAL: with start_offset>0 + a resume state, parse ONLY the bytes
    appended since last time. Codex rollout files are append-only; the active one
    is 7.7GB+ and grows on every interaction — a full reparse on each change was
    the build's dominant cost and an OOM trigger. Only newline-terminated lines
    are consumed; a partial trailing line is left for the next pass.

    Returns (meta, turns, intent, last_rate_limits, end_offset, state):
      - turns: turns parsed in THIS call (caller appends to the accumulated list).
      - end_offset: byte position after the last COMPLETE line (next resume point).
      - state: {sid,cwd,branch,model,turn_id,idx,prev_sig,intent} to resume from.
    """
    resume = resume or {}
    session_id = resume.get("sid") or ""
    cwd = resume.get("cwd") or ""
    git_branch = resume.get("branch") or ""
    cur_model = resume.get("model") or ""
    cur_turn_id = resume.get("turn_id") or ""
    intent: Optional[str] = resume.get("intent")
    idx = int(resume.get("idx") or 0)
    _ps = resume.get("prev_sig")
    prev_sig: Optional[tuple] = tuple(_ps) if _ps else None
    turns: list = []
    last_rate_limits: Optional[dict] = None
    offset = int(start_offset or 0)

    try:
        # Binary so byte offsets are exact; only consume newline-terminated lines.
        with path.open("rb") as fh:
            fh.seek(offset)
            for raw_b in fh:
                if not raw_b.endswith(b"\n"):
                    break          # partial trailing line → consume on next pass
                offset += len(raw_b)
                raw = raw_b.decode("utf-8", "replace")
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
    state = {"sid": session_id, "cwd": cwd, "branch": git_branch,
             "model": cur_model, "turn_id": cur_turn_id, "idx": idx,
             "prev_sig": list(prev_sig) if prev_sig else None, "intent": intent}
    return meta, turns, intent, last_rate_limits, offset, state


def _load_cache() -> dict:
    if not _CACHE_PATH.exists() and _OLD_CACHE_PATH.exists():
        try:
            _OLD_CACHE_PATH.rename(_CACHE_PATH)   # migrate pre-rename cache → no re-parse
        except OSError:
            pass
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
    # GLOBAL REPLAY DEDUP accumulator — key=(turn_id, in, out, cache_read) → en erken record.
    # Codex resume/fork dosyaları geçmişi REPLAY eder (bkz. aşağıdaki dedup bloğu).
    dedup: dict = {}
    user_intents: dict[str, str] = {}
    files = 0
    reparsed = 0
    latest_rl_by_dev: dict[str, dict] = {}   # device → most-recent rate_limits

    roots = [(root, _local_device())]
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
            if cached and cached.get("v") == _PARSE_VERSION and "meta" in cached:
                old_sig = cached.get("sig") or [0, 0]
                off0 = int(cached.get("offset") or 0)
                if old_sig == sig:
                    # unchanged → reuse cached fully (no I/O)
                    meta = cached["meta"]; turns = cached.get("turns", [])
                    intent = cached.get("intent"); rl = cached.get("rate_limits")
                    offset = off0; state = cached.get("resume") or {}
                elif sig[1] > old_sig[1] and off0 <= sig[1]:
                    # append-only growth → INCREMENTAL: read ONLY the new bytes,
                    # append to the cached turns (the 7.7GB active file never gets
                    # fully reparsed → ~30s build collapses to milliseconds).
                    meta, new_turns, intent2, rl2, offset, state = parse_codex_file(
                        path, start_offset=off0, resume=cached.get("resume") or {})
                    turns = (cached.get("turns") or []) + new_turns
                    intent = cached.get("intent") or intent2
                    rl = rl2 or cached.get("rate_limits")
                    reparsed += 1
                else:
                    # shrunk / rotated / edited-in-place → safe full reparse
                    meta, turns, intent, rl, offset, state = parse_codex_file(path)
                    reparsed += 1
            else:
                meta, turns, intent, rl, offset, state = parse_codex_file(path)
                reparsed += 1
            state["intent"] = intent          # keep resume intent in sync
            # KOMPAKT cache + byte offset & resume state for incremental reparse.
            new_cache[key] = {"sig": sig, "v": _PARSE_VERSION, "meta": meta,
                              "turns": turns, "intent": intent, "device": device,
                              "rate_limits": rl, "offset": offset, "resume": state}
            if rl and isinstance(rl, dict):
                prev = latest_rl_by_dev.get(device)
                if prev is None or (rl.get("_ts", 0) > prev.get("_ts", 0)):
                    latest_rl_by_dev[device] = {**rl, "device": device}
            sid = meta.get("sid") if meta else None
            if intent and sid and sid not in user_intents:
                user_intents[sid] = intent
            # REPLAY DEDUP — Codex resume/fork dosyaları aynı konuşmanın geçmişini
            # TEKRAR yazar: tek session_id N rollout dosyasına yayılır, her biri ~tüm
            # turn'leri yeniden logger (sadece timestamp resume-zamanına kayar).
            # Ölçülen: 1 session = 45 dosya = 543k turn ama gerçek ~14.8k (36.8x şişme)
            # → burn rate ~700x patlıyordu. Her MANTIKSAL event'i (turn_id, in, out,
            # cache_read) BİR KEZ say; en ERKEN timestamp'i tut (orijinal yakım anı →
            # zaman-pencereleri doğru). Replay'ler aynı (turn_id,token) imzasını taşır,
            # yalnız ts farklı → bu anahtar onları çökertir; replay olmayan session'lar
            # (her event benzersiz imza) etkilenmez.
            for rec in _compact_to_usages(meta, turns, device):
                k = (rec.turn_id, rec.input_tokens, rec.output_tokens, rec.cache_read_tokens)
                prev = dedup.get(k)
                if prev is None or rec.timestamp < prev.timestamp:
                    dedup[k] = rec

    _save_cache(new_cache)
    records = list(dedup.values())
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
