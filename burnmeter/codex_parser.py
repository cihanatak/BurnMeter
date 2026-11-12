"""Codex (OpenAI gpt-5.x) session parser for Burnmeter.

Codex CLI writes session rollouts to:
    $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ISO>-<uuid>.jsonl   (CODEX_HOME defaults to ~/.codex)
and, since Codex CLI 0.164, compresses finished ones to rollout-*.jsonl.zst
(same line-delimited JSON, read as a stream).

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

import io
import json
import os
from pathlib import Path
from typing import Callable, Iterator, Optional

from .parser import UsageRecord, _local_device, _parse_timestamp, _root_prefixes, _under

# Codex CLI 0.164+ compresses finished rollouts to rollout-*.jsonl.zst. Plain
# imports (not importlib strings) so PyInstaller's static analysis bundles the
# module into the frozen app. Order: stdlib (3.14+), its backport (same API),
# then the third-party `zstandard` if the user happens to have it.
try:
    from compression import zstd as _zstd_mod          # type: ignore[import-not-found]
except ImportError:
    try:
        from backports import zstd as _zstd_mod        # type: ignore[import-not-found,no-redef]
    except ImportError:
        _zstd_mod = None
_zstandard = None
if _zstd_mod is None:
    try:
        import zstandard as _zstandard                  # type: ignore[import-not-found,no-redef]
    except ImportError:
        _zstandard = None


def _zstd_opener() -> Optional[Callable[[Path], io.BufferedIOBase]]:
    """Return a function that opens a .zst file as a decompressing binary stream,
    or None when no zstd module imports (those files are then skipped and counted)."""
    if _zstd_mod is not None:
        return lambda p: _zstd_mod.open(p, "rb")
    if _zstandard is not None:
        def _open(p: Path):
            fh = open(p, "rb")
            try:
                reader = _zstandard.ZstdDecompressor().stream_reader(
                    fh, read_across_frames=True, closefd=True)
                return io.BufferedReader(reader)
            except Exception:
                fh.close()
                raise
        return _open
    return None


def _codex_home() -> Path:
    """Codex honours $CODEX_HOME for its whole state folder; ~/.codex otherwise."""
    env = (os.environ.get("CODEX_HOME") or "").strip()
    return Path(env).expanduser() if env else Path.home() / ".codex"


CODEX_HOME = _codex_home()
CODEX_SESSIONS_DIR = CODEX_HOME / "sessions"
_CACHE_PATH = CODEX_HOME / ".burnmeter_codex_cache.json"
_OLD_CACHE_PATH = CODEX_HOME / ".codex_meter_cache.json"   # pre-rename → auto-migrated
# parse mantığı değişince bump et → farklı "v" taşıyan eski cache entry'leri yok
# sayılır (mtime/size aynı olsa bile yeniden parse edilir, stale turn dönmez).
# v2: ardışık birebir-aynı token_count delta'ları atlanır (anti-şişme).
# v3: byte-offset incremental parse (cache entry'sine offset+resume eklendi) —
# aktif 7.7GB dosya artık baştan değil, sadece eklenen byte'lardan parse edilir.
_PARSE_VERSION = 3

# Substring pre-filter — only json-parse lines that might carry what we need.
# Avoids parsing the giant response_item text lines (the bulk of the 16GB).
_INTEREST = ('"token_count"', '"turn_context"', '"session_meta"', '"user_message"')


_ZST = ".zst"


class CompressedReadError(Exception):
    """A rollout-*.jsonl.zst that could not be read. reason: "no_zstd" | "corrupt"."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _is_compressed(path: Path) -> bool:
    return path.name.endswith(_ZST)


def iter_codex_files(root: Path = CODEX_SESSIONS_DIR) -> Iterator[Path]:
    """Plain and compressed rollouts. When both X.jsonl and X.jsonl.zst exist
    (Codex mid-compression), only the plain one is yielded: same session, and
    reading both would count it twice."""
    if not root.exists():
        return
    plain = set(root.rglob("rollout-*.jsonl"))
    files = list(plain)
    for z in root.rglob("rollout-*.jsonl.zst"):
        if z.with_name(z.name[:-len(_ZST)]) not in plain:
            files.append(z)
    yield from sorted(files)


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
    except (ValueError, TypeError, AttributeError):
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
    # A .zst is written once and never appended to: always read whole, streamed
    # (it can expand to gigabytes), and its last line counts even without "\n".
    compressed = _is_compressed(path)
    offset = 0 if compressed else int(start_offset or 0)

    try:
        if compressed:
            opener = _zstd_opener()
            if opener is None:
                raise CompressedReadError("no_zstd")
            fh = opener(path)
        else:
            fh = path.open("rb")
        # Binary so byte offsets are exact; only consume newline-terminated lines.
        with fh:
            if offset:
                fh.seek(offset)
            for raw_b in fh:
                if not compressed and not raw_b.endswith(b"\n"):
                    break          # partial trailing line → consume on next pass
                offset += len(raw_b)
                raw = raw_b.decode("utf-8", "replace")
                if not _line_of_interest(raw):
                    continue
                try:
                    r = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # One odd line (array, number, string payload) must not abort the
                # whole Codex load: skip anything that isn't the expected shape.
                if not isinstance(r, dict):
                    continue
                typ = r.get("type")
                payload = r.get("payload")
                if not isinstance(payload, dict):
                    continue
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
                        if not isinstance(info, dict):
                            continue
                        last = info.get("last_token_usage") or {}
                        if not last or not isinstance(last, dict):
                            continue
                        try:
                            in_tok = int(last.get("input_tokens") or 0)
                            cached = int(last.get("cached_input_tokens") or 0)
                            out_tok = int(last.get("output_tokens") or 0)
                        except (TypeError, ValueError):
                            continue
                        # ANTI-DUPLICATE: Codex akış sırasında aynı last_token_usage'ı
                        # ardışık birebir tekrar yayabilir (ölçüm: event'lerin %5.2'si).
                        # Bunları saymak hesabı %6.6 şişirir. Ardışık birebir-aynı
                        # (in,cached,out) delta'yı atla — sıfır-kullanım event'i değilse.
                        sig = (in_tok, cached, out_tok)
                        if sig == prev_sig and (in_tok or out_tok):
                            continue
                        prev_sig = sig
                        fresh_in = max(0, in_tok - cached)
                        ts = r.get("timestamp")
                        if not isinstance(ts, str):
                            ts = ""
                        # KOMPAKT turn tuple: [ts, model, turn_id, fresh_in, out, cache_read]
                        # session_id/cwd/git_branch dosya-seviyesinde meta'ya hoist
                        # edilir (record başına tekrar etmez → cache 198MB→~birkaç MB).
                        turns.append([
                            # No turn_context seen yet → we do NOT know the model.
                            # Inventing "gpt-5" priced these turns at a flagship rate
                            # they may never have used; "" resolves to unknown, which
                            # counts the tokens and surfaces in the pricing warning.
                            ts, cur_model or "",
                            cur_turn_id or f"{session_id}:{idx}",
                            fresh_in, out_tok, cached,
                        ])
                        idx += 1
    except CompressedReadError:
        raise
    except OSError:
        if compressed:
            raise CompressedReadError("corrupt")
    except Exception:
        # A truncated or corrupt .zst (ZstdError, EOFError, ...) yields no
        # trustworthy turns: the caller skips and counts it.
        if compressed:
            raise CompressedReadError("corrupt")
        raise
    stem = path.name[:-len(_ZST)] if compressed else path.name
    meta = {"sid": session_id or Path(stem).stem, "cwd": cwd, "branch": git_branch}
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
        # Garbage bytes after a power cut, or valid JSON that isn't an object,
        # used to stop every build until the user deleted the file: treat any
        # failure as "no cache" and rebuild.
        try:
            cache = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return cache if isinstance(cache, dict) else {}
    return {}


def _save_cache(cache: dict) -> None:
    # Write a temp file and swap it in: a reader (the app, or a build killed
    # mid-save) never sees half a cache.
    tmp = _CACHE_PATH.with_name(f"{_CACHE_PATH.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        os.replace(tmp, _CACHE_PATH)
    except OSError:
        try:
            tmp.unlink()
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
            model=model or "",
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


def _usable_entry(cached) -> Optional[dict]:
    """A cache entry this version can reuse, else None (a foreign or damaged cache
    must mean "re-read the file", never a crash)."""
    if (isinstance(cached, dict) and cached.get("v") == _PARSE_VERSION
            and isinstance(cached.get("meta"), dict)
            and isinstance(cached.get("turns", []), list)):
        return cached
    return None


def _cached_failure(cached, sig: list) -> Optional[str]:
    """Reason a compressed file failed last time, if it is still byte-identical.
    Only "corrupt" is remembered: installing a zstd module must take effect at once."""
    if (isinstance(cached, dict) and cached.get("v") == _PARSE_VERSION
            and cached.get("error") == "corrupt" and cached.get("sig") == sig):
        return "corrupt"
    return None


def _bytes_to_read(cached: Optional[dict], sig: list, compressed: bool = False) -> int:
    """How much of a log load_codex_records reads: nothing if it's unchanged, the new
    bytes if it only grew, else all of it (its three cases, for the progress counts).
    A compressed log is never appended to, so it is either unchanged or read whole."""
    if _cached_failure(cached, sig):
        return 0
    cached = _usable_entry(cached)
    if cached:
        old_sig = cached.get("sig") or [0, 0]
        off0 = int(cached.get("offset") or 0)
        if old_sig == sig:
            return 0
        if not compressed and sig[1] > old_sig[1] and off0 <= sig[1]:
            return sig[1] - off0
    return sig[1]


def load_codex_records(
    root: Path = CODEX_SESSIONS_DIR,
    extra_roots: Optional[list[Path]] = None,
    since_days: int = 90,
    progress: Optional[Callable[[dict], None]] = None,
) -> tuple[list[UsageRecord], dict, dict[str, str]]:
    """Load Codex usage records with an incremental (path,mtime,size) cache.

    Returns (records sorted by time, stats, user_intents) — same shape as
    parser.load_records so the analytics pipeline is source-agnostic.

    progress: called about twice a second with files_done/files_total and
    bytes_done/bytes_total (the bytes that need reading, not served from the cache).
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
    saved_at = 0                   # reparse count at the last partial save
    latest_rl_by_dev: dict[str, dict] = {}   # device → most-recent rate_limits
    # Compressed logs that could not be read, so the CLI/dashboard can say
    # "N compressed Codex logs were not read" instead of silently showing less.
    compressed_files = 0
    unread = {"no_zstd": 0, "corrupt": 0}

    roots = [(root, _local_device())]
    for er in (extra_roots or []):
        roots.append((er, "pc"))

    if not root.exists() and not (extra_roots and any(r.exists() for r in extra_roots)):
        return [], {
            "files_scanned": 0, "reparsed": 0,
            "root": str(root), "root_exists": False,
            "source": "codex",
            "compressed_files": 0, "compressed_unread": 0,
            "compressed_unread_reasons": dict(unread),
        }, user_intents

    import time
    cutoff_ts = (time.time() - since_days * 86400) if since_days and since_days > 0 else 0.0
    # Every log first (a stat each), so a long first read can say how far it is.
    plan = []
    for scan_root, device in roots:
        if not scan_root.exists():
            continue
        logs = []
        for path in iter_codex_files(scan_root):
            try:
                st = path.stat()
                sig = [st.st_mtime, st.st_size]
            except OSError:
                continue
            # Recent-window cap: skip sessions older than `since_days` so a huge
            # ~/.codex history (50 GB+) doesn't blow past the build-worker timeout
            # (180s → blank forever). since_days<=0 = all-time. mtime is the right
            # signal — the active file keeps a fresh mtime; old sessions fall out.
            if cutoff_ts and st.st_mtime < cutoff_ts:
                continue
            logs.append((path, sig, _bytes_to_read(cache.get(str(path)), sig,
                                                   _is_compressed(path))))
        plan.append((device, logs))
    files_total = sum(len(logs) for _dev, logs in plan)
    read_total = sum(need for _dev, logs in plan for _p, _s, need in logs)
    read_done = 0
    reported = time.monotonic()
    for device, logs in plan:
        for path, sig, need in logs:
            if progress is not None and time.monotonic() - reported >= 0.5:
                progress({"files_done": files, "files_total": files_total,
                          "bytes_done": read_done, "bytes_total": read_total})
                reported = time.monotonic()
            key = str(path)
            files += 1
            compressed = _is_compressed(path)
            if compressed:
                compressed_files += 1
            raw_cached = cache.get(key)
            if compressed and _cached_failure(raw_cached, sig):
                # Same corrupt bytes as last build: don't decompress them again.
                unread["corrupt"] += 1
                new_cache[key] = raw_cached
                read_done += need
                continue
            cached = _usable_entry(raw_cached)
            try:
                if cached:
                    old_sig = cached.get("sig") or [0, 0]
                    off0 = int(cached.get("offset") or 0)
                    if old_sig == sig:
                        # unchanged → reuse cached fully (no I/O)
                        meta = cached["meta"]; turns = cached.get("turns", [])
                        intent = cached.get("intent"); rl = cached.get("rate_limits")
                        offset = off0; state = cached.get("resume") or {}
                        if not isinstance(state, dict):
                            state = {}
                    elif not compressed and sig[1] > old_sig[1] and off0 <= sig[1]:
                        # append-only growth → INCREMENTAL: read ONLY the new bytes,
                        # append to the cached turns (the 7.7GB active file never gets
                        # fully reparsed → ~30s build collapses to milliseconds).
                        resume = cached.get("resume")
                        meta, new_turns, intent2, rl2, offset, state = parse_codex_file(
                            path, start_offset=off0,
                            resume=resume if isinstance(resume, dict) else {})
                        turns = (cached.get("turns") or []) + new_turns
                        intent = cached.get("intent") or intent2
                        rl = rl2 or cached.get("rate_limits")
                        reparsed += 1
                    else:
                        # shrunk / rotated / edited-in-place / compressed changed
                        # → safe full reparse
                        meta, turns, intent, rl, offset, state = parse_codex_file(path)
                        reparsed += 1
                else:
                    # New key. A session that was read as X.jsonl and now appears as
                    # X.jsonl.zst is parsed once more under its new path; the old
                    # entry drops out below because X.jsonl no longer exists, and
                    # the replay dedup would collapse any overlap anyway.
                    meta, turns, intent, rl, offset, state = parse_codex_file(path)
                    reparsed += 1
            except CompressedReadError as e:
                unread[e.reason] = unread.get(e.reason, 0) + 1
                if e.reason == "corrupt":
                    new_cache[key] = {"sig": sig, "v": _PARSE_VERSION, "error": "corrupt"}
                read_done += need
                continue
            state["intent"] = intent          # keep resume intent in sync
            read_done += need
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
            # Persist progress every ~25 parsed files so a slow/interrupted build of a
            # huge ~/.codex (50 GB+) RESUMES from where it stopped instead of re-parsing
            # from scratch — it eventually completes + caches, so the Codex/combined tab
            # stops "hanging forever".
            if reparsed and reparsed % 25 == 0 and reparsed != saved_at:
                _save_cache({**cache, **new_cache})   # unvisited entries stay valid
                saved_at = reparsed                   # cache hits don't move `reparsed`

    if progress is not None:
        progress({"files_done": files, "files_total": files_total,
                  "bytes_done": read_done, "bytes_total": read_total})

    # Keep the entries of logs under other roots while those files exist:
    # scanning one tree (a test's temp dir) must not evict another's cache.
    scanned = _root_prefixes(r for r, _dev in roots)
    for key, c in cache.items():
        if key not in new_cache and not _under(key, scanned) and os.path.exists(key):
            new_cache[key] = c
    if new_cache and (reparsed or new_cache.keys() != cache.keys()):
        _save_cache(new_cache)    # never clobber a good cache with an empty scan
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
        # rollout-*.jsonl.zst seen / not readable (reasons: no_zstd, corrupt).
        "compressed_files": compressed_files,
        "compressed_unread": sum(unread.values()),
        "compressed_unread_reasons": unread,
    }, user_intents
