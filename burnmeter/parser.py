"""JSONL parser for Claude Code session files.

Claude Code writes one JSONL file per session under
~/.claude/projects/<encoded-cwd>/<session-id>.jsonl.

Each line is a JSON object. Assistant messages contain a `message.usage`
block with input_tokens / output_tokens / cache_creation_input_tokens /
cache_read_input_tokens. The model used for that turn is in `message.model`.

We deliberately keep this parser tolerant of schema drift: missing fields
are coerced to safe defaults. We never crash on a malformed line — we
collect a `parse_errors` count instead.

One API response is written as several lines (one per content block: thinking,
text, each tool_use) that share `message.id`, so counting every line would
inflate totals (Anthropic SDK docs explicitly warn about this). The lines are
NOT identical, though: their usage snapshots grow while the response streams,
and the full output count is usually only on the last one. load_records
therefore folds a response's lines into one record by field-wise max — see
`_merge_usage`.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional


CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def _local_device() -> str:
    """Short origin label for THIS machine, used to tag usage records by device.
    Derived from the OS so a Windows/Linux box is never mislabeled 'mac' (the old
    hardcoded default). The dashboard only surfaces this as a badge when a user
    actually has 2+ devices (mirror/sync)."""
    import platform
    return {"Darwin": "mac", "Windows": "pc", "Linux": "linux"}.get(platform.system(), "pc")


@dataclass(slots=True)
class UsageRecord:
    """A single normalized usage event.

    One line of JSONL → at most one UsageRecord (only assistant turns
    that have a usage block produce a record).

    slots=True: yarım milyon+ record tutulurken __dict__ overhead'ini keser
    (~%40 bellek tasarrufu). Commercial ürün için RAM disiplini şart.
    """
    timestamp: datetime
    session_id: str
    project_dir: str        # decoded cwd
    project_label: str      # short basename
    git_branch: str
    model: str
    message_id: str         # for dedup across parallel tool calls
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    # Of cache_creation_tokens, how many were written with the 1-HOUR TTL. Claude
    # bills a 1h cache write at 2.0x input where a 5-minute write costs 1.25x, so
    # this split is a real price difference, not bookkeeping: on a heavy agentic
    # month ~80% of writes are 1h. Absent in older logs → 0 → billed at 5m, which
    # is the correct fallback for records that predate the field.
    cache_creation_1h_tokens: int = 0
    tool_names: list[str] = field(default_factory=list)
    source_file: str = ""
    # JSONL uuid chain — bir assistant cevabı birden çok record üretir
    # (her tool call ayrı record). turn_id = ancestor zincirinde ilk user
    # message'ın uuid'si. Bir user mesaj + sonraki tüm assistant record'lar
    # aynı turn_id'yi paylaşır → analytics turn başına aggregate edebilir.
    uuid: str = ""
    parent_uuid: str = ""
    turn_id: str = ""
    # device — "mac" (main root) veya "pc" (extra_root'tan sync'lenmiş).
    # Cihan paradigm 2026-05-29: PC + Mac ayrı sayaç + her satırda badge.
    device: str = "mac"
    # usage.speed — "fast" when the response ran in fast mode, which bills every
    # token type at 2x. Standard responses ("standard") and older logs stay "".
    speed: str = ""

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
        )

    @property
    def billable_input_tokens(self) -> int:
        """Input + cache writes (cache_creation), excluding cache reads."""
        return self.input_tokens + self.cache_creation_tokens

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


def decode_project_dir(encoded: str) -> str:
    """Convert ~/.claude/projects/-Users-me-proj back to a path-ish string.

    Claude Code replaces every non-alphanumeric character of the absolute
    cwd with '-'. That mapping is lossy (we can't recover '/' vs '.' vs
    '_'), so we just give back the dashed form prefixed with '/' for
    readability. Project label is the last segment.
    """
    if not encoded:
        return ""
    if encoded.startswith("-"):
        return "/" + encoded[1:].replace("-", "/")
    return encoded.replace("-", "/")


def _parse_timestamp(raw: Optional[str]) -> datetime:
    if not raw:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        # Claude Code emits ISO-8601 with 'Z' suffix.
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _extract_tool_names(content) -> list[str]:
    if not isinstance(content, list):
        return []
    out = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            name = block.get("name")
            if isinstance(name, str):
                out.append(name)
    return out


def parse_line(line: str, source_file: str = "") -> Optional[UsageRecord]:
    """Parse one JSONL line. Returns None if it isn't a usage-bearing event."""
    line = line.strip()
    if not line:
        return None
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None

    msg = rec.get("message")
    if not isinstance(msg, dict):
        return None

    # Only assistant turns carry usage we can attribute to a model.
    if msg.get("role") != "assistant":
        return None

    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None

    cwd = rec.get("cwd") or ""
    if not cwd:
        # Fall back to encoded directory name from path.
        parent = Path(source_file).parent.name if source_file else ""
        cwd = decode_project_dir(parent)

    project_label = Path(cwd).name or cwd or "unknown"

    return UsageRecord(
        timestamp=_parse_timestamp(rec.get("timestamp")),
        session_id=str(rec.get("sessionId") or ""),
        project_dir=str(cwd),
        project_label=project_label,
        git_branch=str(rec.get("gitBranch") or ""),
        model=str(msg.get("model") or ""),
        message_id=str(msg.get("id") or rec.get("uuid") or ""),
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens") or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
        # usage.cache_creation = {ephemeral_5m_input_tokens, ephemeral_1h_input_tokens};
        # the two sum to cache_creation_input_tokens. We keep the 1h half because it
        # bills at a higher multiple (see UsageRecord).
        cache_creation_1h_tokens=int(
            ((usage.get("cache_creation") or {}) if isinstance(usage.get("cache_creation"), dict) else {})
            .get("ephemeral_1h_input_tokens") or 0
        ),
        tool_names=_extract_tool_names(msg.get("content")),
        source_file=source_file,
        uuid=str(rec.get("uuid") or ""),
        parent_uuid=str(rec.get("parentUuid") or ""),
        # turn_id load_records'ta dosya context'iyle resolve edilir.
        speed="fast" if usage.get("speed") == "fast" else "",
    )


def parse_user_intent(line: str) -> Optional[tuple[str, str, datetime]]:
    """Extract (session_id, user-message text, timestamp) from a user turn line.

    Returns None for non-user lines, slash commands, and lines without
    sessionId. Used to give each session a human-readable "what was the goal"
    label — surfaced in the dashboard's recent-activity table.
    """
    line = line.strip()
    if not line:
        return None
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None

    # Only what the user typed. Claude Code also logs role-"user" lines that aren't:
    # copies of each API request ("api-request-blob", undated, so they read as 1970
    # and won "earliest"), meta lines such as image notes, compaction summaries, and
    # tool results with a reminder appended ("Tool loaded.").
    if rec.get("type", "user") != "user" or rec.get("isMeta") or rec.get("isCompactSummary"):
        return None
    if not rec.get("timestamp"):
        return None
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return None
    if msg.get("role") != "user":
        return None

    sid = str(rec.get("sessionId") or "")
    if not sid:
        return None

    content = msg.get("content")
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "") or ""
                if text:
                    break

    text = text.strip()
    if not text:
        return None
    # Skip slash commands and Claude Code internal markers — they're not
    # user intent.
    if text.startswith("/"):
        return None
    if text.startswith("<") and "</" in text:
        return None
    if text.startswith("[Request interrupted"):   # Claude Code's marker after Esc
        return None

    ts = _parse_timestamp(rec.get("timestamp"))
    return sid, text, ts


def _classify_error_text(text: str) -> str:
    """Bucket raw tool_result error text into a short category label."""
    t = (text or "").lower()
    if "no such file" in t or "file not found" in t or "no file at" in t:
        return "file_not_found"
    if "permission denied" in t:
        return "permission_denied"
    if "command not found" in t:
        return "command_not_found"
    if "timeout" in t or "timed out" in t:
        return "timeout"
    if "syntaxerror" in t or "traceback" in t:
        return "python_error"
    if "exit code" in t or "exit status" in t:
        return "bash_failure"
    if "no longer exists" in t or "not found in" in t:
        return "stale_reference"
    if "exceeds maximum" in t or "too large" in t:
        return "output_too_large"
    if "rate limit" in t or "rate_limit" in t:
        return "rate_limited"
    return "other_error"


def parse_tool_events(line: str) -> Optional[dict]:
    """Extract tool_result error events and tool_use names from a JSONL line.

    Returns a dict with:
      - session_id, timestamp
      - errors: list of (category, snippet) from any tool_result with is_error
                or recognizable error text
      - tool_uses: list of tool names called in this line

    Returns None if the line carries neither.
    """
    line = line.strip()
    if not line:
        return None
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    # Request copies ("api-request-blob") repeat the session's tool results.
    if rec.get("type") not in (None, "user", "assistant"):
        return None
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    if not isinstance(content, list):
        return None

    errors = []
    tool_uses = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "tool_use":
            name = block.get("name")
            if isinstance(name, str):
                tool_uses.append(name)
        elif btype == "tool_result":
            bc = block.get("content", "")
            text = bc if isinstance(bc, str) else ""
            if isinstance(bc, list) and bc:
                first = bc[0]
                if isinstance(first, dict):
                    text = first.get("text", "")
            text = str(text or "")
            is_err = block.get("is_error", False)
            # Cihan paradigm 2026-05-29: sahte "error" yakalama düzeltildi.
            # Eski mantık: bir tool_result content'inde "error:" lowercase
            # geçerse hata sayıyordu — başarılı bash output'unda "0 errors",
            # "no errors", argparse "usage:" gibi kelimeler false-positive
            # üretiyordu. Şimdi sadece Claude Code'un explicit `is_error: True`
            # flag'ine güveniyoruz (gerçek hata sinyali).
            if is_err:
                errors.append({
                    "category": _classify_error_text(text),
                    "snippet": text[:160].replace("\n", " "),
                    "tool_use_id": str(block.get("tool_use_id") or ""),
                })

    if not errors and not tool_uses:
        return None
    return {
        "session_id": str(rec.get("sessionId") or ""),
        "timestamp": _parse_timestamp(rec.get("timestamp")),
        "errors": errors,
        "tool_uses": tool_uses,
    }


def iter_jsonl_files(root: Path = CLAUDE_PROJECTS_DIR) -> Iterator[Path]:
    if not root.exists():
        return
    for path in root.rglob("*.jsonl"):
        if path.is_file():
            yield path


# ---- per-file parse cache (claude) — codex_parser ile aynı disiplin ----
# Claude session JSONL'leri append-only; tamamlanmış dosyalar değişmez. Cache
# (path,mtime,size) ile değişmemiş dosyalar yeniden OKUNMAZ → build ~25s'den
# ~birkaç saniyeye iner (sadece aktif/yeni dosyalar parse edilir).
_CLAUDE_CACHE_PATH = CLAUDE_PROJECTS_DIR.parent / ".burnmeter_claude_cache.json"
# Bump whenever UsageRecord gains a field the cache must carry, or a record's
# meaning changes — an old entry would otherwise be replayed with the new field
# silently zeroed. v2: cache_creation_1h_tokens (1-hour cache-write TTL split).
# v3: speed (fast mode bills 2x); intents from the user's own turns only; tool
# errors carry their tool_use_id.
_CLAUDE_CACHE_VERSION = 3


def _claude_load_cache() -> dict:
    try:
        return json.loads(_CLAUDE_CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _claude_save_cache(cache: dict) -> None:
    """Write the cache atomically: a temp file renamed over the old one. A plain
    write_text that is interrupted (app quit, crash) leaves a truncated file, and the
    next build then re-parses every log from scratch — minutes on a big history."""
    tmp = _CLAUDE_CACHE_PATH.with_name(f"{_CLAUDE_CACHE_PATH.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(cache))
        os.replace(tmp, _CLAUDE_CACHE_PATH)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _root_prefixes(roots) -> tuple[str, ...]:
    """Scanned roots as the file system compares paths (case-folded on Windows,
    absolute), so one tree spelled two ways isn't kept as two trees."""
    return tuple(os.path.normcase(os.path.abspath(r)).rstrip("/\\") + os.sep for r in roots)


def _under(path: str, prefixes: tuple[str, ...]) -> bool:
    return os.path.normcase(os.path.abspath(path)).startswith(prefixes)


def _merge_usage(into: UsageRecord, other: UsageRecord) -> None:
    """Fold another streamed line of the SAME API response into its record.

    Each line carries a usage snapshot taken while the response streamed, so the
    counts only grow: on the maker's logs, 117,604 of 117,613 multi-line responses
    had their full output_tokens on the LAST line alone. Keeping the first line
    (v0.3.4 and earlier) dropped 32% of all output tokens. The response's real
    usage is the field-wise max; its tool calls are spread across the lines.
    """
    into.input_tokens = max(into.input_tokens, other.input_tokens)
    into.output_tokens = max(into.output_tokens, other.output_tokens)
    into.cache_creation_tokens = max(into.cache_creation_tokens, other.cache_creation_tokens)
    into.cache_read_tokens = max(into.cache_read_tokens, other.cache_read_tokens)
    into.cache_creation_1h_tokens = max(into.cache_creation_1h_tokens, other.cache_creation_1h_tokens)
    if other.speed == "fast":
        into.speed = "fast"
    # A new list, not extend(): a record rebuilt from the cache shares nothing with
    # its entry (see _rec_from_dict), and this keeps it that way.
    into.tool_names = into.tool_names + other.tool_names


def _rec_from_dict(d: dict) -> UsageRecord:
    d = dict(d)
    d["timestamp"] = _parse_timestamp(d.get("timestamp"))
    # Its own list: merging a later line into a cached record once extended the
    # cache entry's list too, so tool counts grew on every build and so did the cache.
    d["tool_names"] = list(d.get("tool_names") or ())
    return UsageRecord(**d)


def parse_claude_file(path: Path, scan_device: str = "mac"):
    """Parse ONE Claude JSONL → (records, intents, errors, parse_errors).

    Per-file ONLY: turn_id resolution is within-file; cross-file dedup +
    earliest-intent merging happen in load_records. Cached by (path,mtime,size).
      - records: all UsageRecords in this file (turn_id + device set, NO dedup).
      - intents: {session_id: (text, ts_iso)} earliest user msg within this file.
      - errors:  list of tool_result error dicts.
      - parse_errors: count of malformed lines.
    """
    records: list[UsageRecord] = []
    intents: dict[str, tuple] = {}
    intent_ts: dict[str, datetime] = {}
    errs: list[dict] = []
    perr = 0
    try:
        file_lines: list[str] = []
        uuid_map: dict[str, tuple[str, str, bool]] = {}
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                file_lines.append(raw)
                s = raw.strip()
                if not s:
                    continue
                try:
                    parsed = json.loads(s)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict):   # valid JSON of another shape: skip
                    continue                       # the line, don't abort the whole load
                u = parsed.get("uuid")
                if not u:
                    continue
                typ = str(parsed.get("type") or "")
                parent = str(parsed.get("parentUuid") or "")
                is_human = False
                if typ == "user":
                    msg = parsed.get("message")
                    msg = msg if isinstance(msg, dict) else {}
                    content = msg.get("content")
                    if isinstance(content, str) and content.strip():
                        is_human = True
                    elif isinstance(content, list):
                        has_tool_result = any(
                            isinstance(b, dict) and b.get("type") == "tool_result"
                            for b in content
                        )
                        if not has_tool_result:
                            is_human = True
                uuid_map[str(u)] = (parent, typ, is_human)

        def resolve_turn(start_uuid: str) -> str:
            cur = start_uuid
            for _ in range(2000):
                entry = uuid_map.get(cur)
                if entry is None:
                    return cur
                parent_uuid, typ, is_human = entry
                if typ == "user" and is_human:
                    return cur
                if not parent_uuid:
                    return cur
                cur = parent_uuid
            return cur

        for raw in file_lines:
            intent = None
            try:
                intent = parse_user_intent(raw)
            except Exception:
                pass
            if intent:
                sid, text, ts = intent
                prev = intent_ts.get(sid)
                if prev is None or (ts and ts < prev):
                    intents[sid] = (text[:240], ts.isoformat() if ts else None)
                    intent_ts[sid] = ts

            errs.extend(_tool_errors(raw))

            try:
                rec = parse_line(raw, source_file=str(path))
            except Exception:
                perr += 1
                continue
            if rec is None:
                continue
            if rec.uuid:
                rec.turn_id = resolve_turn(rec.uuid)
            rec.device = scan_device
            records.append(rec)
    except OSError:
        perr += 1
    return records, intents, errs, perr


def _tool_errors(line: str, since: Optional[datetime] = None) -> list[dict]:
    """The tool_result errors on one log line, as load_records reports them."""
    try:
        evt = parse_tool_events(line)
    except Exception:
        return []
    if not (evt and evt["errors"]):
        return []
    ts = evt["timestamp"]
    if since is not None and not (ts and ts >= since):
        return []
    return [{"session_id": evt["session_id"],
             "timestamp": ts.isoformat() if ts else None,
             "category": e["category"], "snippet": e["snippet"],
             "tool_use_id": e.get("tool_use_id", "")} for e in evt["errors"]]


_TS_KEY = b'"timestamp"'


def _newest_stamp(raw: bytes) -> bytes:
    """The latest "timestamp" value on a raw log line, read without parsing the
    JSON (b"" when there is none). Nested objects can carry timestamps of their
    own, so all of them count: the line's own stamp is always among them."""
    newest = b""
    i = raw.find(_TS_KEY)
    while i >= 0:
        j = i + len(_TS_KEY)
        while j < len(raw) and raw[j] in b' \t:"':
            j += 1
        stamp = raw[j:j + 19]
        if stamp > newest:
            newest = stamp
        i = raw.find(_TS_KEY, j)
    return newest


def parse_claude_recent(path: Path, scan_device: str, since: datetime):
    """parse_claude_file for the statusline: records and tool errors stamped at or
    after `since`, nothing else (no turn chains, no intents).

    A long session's log holds months of history: on the maker's machine the 44
    logs touched in a day weighed 1.6 GB, and parsing all of them, four JSON
    decodes per line, made up most of a 65-second statusline. Lines are dated
    from their raw bytes first, so only the recent ones are decoded at all. The
    text check runs 14 hours early in case a stamp carries a UTC offset; the
    decoded timestamp decides.
    """
    since = since.astimezone(timezone.utc)
    cut = (since - timedelta(hours=14)).strftime("%Y-%m-%dT%H:%M:%S").encode()
    records: list[UsageRecord] = []
    errs: list[dict] = []
    perr = 0
    try:
        with path.open("rb") as fh:
            for raw in fh:
                if _newest_stamp(raw) < cut:
                    continue
                line = raw.decode("utf-8", errors="replace")
                if b'"tool_result"' in raw:
                    errs.extend(_tool_errors(line, since))
                if b'"usage"' not in raw:
                    continue
                try:
                    rec = parse_line(line, source_file=str(path))
                except Exception:
                    perr += 1
                    continue
                if rec is None or rec.timestamp < since:
                    continue
                rec.device = scan_device
                records.append(rec)
    except OSError:
        perr += 1
    return records, {}, errs, perr


def load_records(
    root: Path = CLAUDE_PROJECTS_DIR,
    deduplicate: bool = True,
    extra_roots: Optional[list[Path]] = None,
    since: Optional[datetime] = None,
) -> tuple[list[UsageRecord], dict, dict[str, str]]:
    """Load all usage records from a Claude projects directory tree.

    Returns (records sorted by time, stats, user_intents) where:
      - records: list of UsageRecord, sorted by timestamp
      - stats: counts of files scanned and parse errors
      - user_intents: session_id → first user message text (capped to 240
        chars). Lets the UI label each session with its actual prompt.

    since: return only the records (and errors) stamped at or after this moment.
    Logs untouched since then are skipped, older lines are dated from their raw
    bytes and never decoded, and the parse cache is bypassed entirely: loading a
    big history's cache costs more than reading one day of logs. For the
    statusline's no-server fallback.
    """
    records: list[UsageRecord] = []
    by_message: dict[str, UsageRecord] = {}   # message id → its merged record
    seen_lines: set[str] = set()              # line uuids already folded in
    user_intents: dict[str, str] = {}
    user_intent_ts: dict[str, datetime] = {}    # earliest user msg timestamp per session
    error_events: list[dict] = []                # collected tool_result errors
    seen_errors: set[str] = set()                # tool_use ids already counted
    files = 0
    errors = 0

    # Build list of all roots to scan. Cihan paradigm 2026-05-29: PC'deki
    # Claude Code yakımları da görünsün diye Syncthing/rsync ile mirror
    # edilmiş ek dizinler. dedup `message_id`'ye göre — aynı record iki
    # root'ta varsa (sync race) ikinci kez sayılmaz.
    all_roots = [root]
    if extra_roots:
        all_roots.extend(extra_roots)

    if not root.exists() and not (extra_roots and any(r.exists() for r in extra_roots)):
        return [], {
            "files_scanned": 0,
            "parse_errors": 0,
            "root": str(root),
            "root_exists": False,
            "extra_roots": [str(r) for r in (extra_roots or [])],
        }, user_intents, error_events

    use_cache = since is None
    cache = _claude_load_cache() if use_cache else {}
    new_cache: dict = {}
    dirty = False                 # did any entry change? An unchanged cache isn't rewritten.
    since_ts = since.timestamp() if since is not None else None

    for scan_root in all_roots:
        if not scan_root.exists():
            continue
        # device label — main root → "mac", extra_roots → "pc" (varsayım:
        # Cihan'ın senaryosu tek ek cihaz). İleride çoklu cihaz için
        # path-tail'inden infer edilebilir (örn. projects-pc → pc).
        scan_device = _local_device() if scan_root == root else "pc"
        for path in iter_jsonl_files(scan_root):
            try:
                st = path.stat()
                sig = [st.st_mtime, st.st_size]
            except OSError:
                files += 1
                errors += 1
                continue
            if since_ts is not None and st.st_mtime < since_ts:
                continue
            files += 1
            key = str(path)
            c = cache.get(key)
            if since is not None:
                f_recs, f_intents, f_errs, f_perr = parse_claude_recent(path, scan_device, since)
                entry = None              # not cached: only part of the file was read
            elif c and c.get("v") == _CLAUDE_CACHE_VERSION and c.get("sig") == sig:
                # unchanged file → reconstruct from cache (no re-read)
                f_recs = [_rec_from_dict(d) for d in c.get("recs", [])]
                f_intents = c.get("intents", {})
                f_errs = c.get("errors", [])
                f_perr = c.get("perr", 0)
                entry = c
            else:
                f_recs, f_intents, f_errs, f_perr = parse_claude_file(path, scan_device)
                entry = {
                    "v": _CLAUDE_CACHE_VERSION, "sig": sig,
                    "recs": [r.to_dict() for r in f_recs],
                    "intents": f_intents, "errors": f_errs, "perr": f_perr,
                }
                dirty = True
            if use_cache:
                new_cache[key] = entry

            # cross-file aggregation: merge each response's lines, earliest intent, errors
            errors += f_perr
            for r in f_recs:
                r.device = scan_device          # re-apply (cache-hit safety)
                if deduplicate and r.message_id:
                    if r.uuid:
                        if r.uuid in seen_lines:
                            continue            # the very same line again (mirrored root)
                        seen_lines.add(r.uuid)
                    first = by_message.get(r.message_id)
                    if first is not None:
                        _merge_usage(first, r)
                        continue
                    by_message[r.message_id] = r
                records.append(r)
            for sid, val in f_intents.items():
                text = val[0] if isinstance(val, (list, tuple)) else val
                ts_iso = val[1] if isinstance(val, (list, tuple)) and len(val) > 1 else None
                ts = _parse_timestamp(ts_iso) if ts_iso else None
                prev = user_intent_ts.get(sid)
                if ts and (prev is None or ts < prev):
                    user_intents[sid] = (text or "")[:240]
                    user_intent_ts[sid] = ts
            # A forked or resumed session's log repeats the tool results it inherited.
            for e in f_errs:
                tid = e.get("tool_use_id")
                if tid:
                    if tid in seen_errors:
                        continue
                    seen_errors.add(tid)
                error_events.append(e)

    if use_cache:
        # Keep the entries of logs under other roots (another --projects-dir, a
        # test's temp tree) while those files exist: scanning one tree must not
        # evict the cache of another, such as the installed app's.
        scanned = _root_prefixes(all_roots)
        for key, c in cache.items():
            if key not in new_cache and not _under(key, scanned) and os.path.exists(key):
                new_cache[key] = c
        # Rewrite only when something changed (a re-parsed file, or a file gone):
        # this cache reaches hundreds of MB, and rewriting it on every build made
        # each `burnmeter statusline` call take over a minute.
        if dirty or len(new_cache) != len(cache):
            _claude_save_cache(new_cache)
    records.sort(key=lambda r: r.timestamp)
    return records, {
        "files_scanned": files,
        "parse_errors": errors,
        "root": str(root),
        "root_exists": True,
        "extra_roots": [str(r) for r in (extra_roots or [])],
        "error_events_count": len(error_events),
    }, user_intents, error_events
