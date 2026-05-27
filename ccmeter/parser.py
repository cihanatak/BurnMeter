"""JSONL parser for Claude Code session files.

Claude Code writes one JSONL file per session under
~/.claude/projects/<encoded-cwd>/<session-id>.jsonl.

Each line is a JSON object. Assistant messages contain a `message.usage`
block with input_tokens / output_tokens / cache_creation_input_tokens /
cache_read_input_tokens. The model used for that turn is in `message.model`.

We deliberately keep this parser tolerant of schema drift: missing fields
are coerced to safe defaults. We never crash on a malformed line — we
collect a `parse_errors` count instead.

We also deduplicate by `message.id` because parallel tool-use messages
share the same id with identical usage; counting them all would inflate
totals (Anthropic SDK docs explicitly warn about this).
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional


CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


@dataclass
class UsageRecord:
    """A single normalized usage event.

    One line of JSONL → at most one UsageRecord (only assistant turns
    that have a usage block produce a record).
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
    tool_names: list[str] = field(default_factory=list)
    source_file: str = ""

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
        tool_names=_extract_tool_names(msg.get("content")),
        source_file=source_file,
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
            low = text.lower()[:300]
            text_says_err = any(k in low for k in (
                "error:", "no such file", "file not found", "permission denied",
                "command not found", "syntaxerror", "traceback",
                "no longer exists", "exceeds maximum", "rate limit",
                "exit code 1", "exit status 1", "timeout",
            ))
            if is_err or text_says_err:
                errors.append({
                    "category": _classify_error_text(text),
                    "snippet": text[:160].replace("\n", " "),
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


def load_records(
    root: Path = CLAUDE_PROJECTS_DIR,
    deduplicate: bool = True,
) -> tuple[list[UsageRecord], dict, dict[str, str]]:
    """Load all usage records from a Claude projects directory tree.

    Returns (records sorted by time, stats, user_intents) where:
      - records: list of UsageRecord, sorted by timestamp
      - stats: counts of files scanned and parse errors
      - user_intents: session_id → first user message text (capped to 240
        chars). Lets the UI label each session with its actual prompt.
    """
    records: list[UsageRecord] = []
    seen_ids: set[str] = set()
    user_intents: dict[str, str] = {}
    user_intent_ts: dict[str, datetime] = {}    # earliest user msg timestamp per session
    error_events: list[dict] = []                # collected tool_result errors
    files = 0
    errors = 0

    if not root.exists():
        return [], {
            "files_scanned": 0,
            "parse_errors": 0,
            "root": str(root),
            "root_exists": False,
        }, user_intents, error_events

    for path in iter_jsonl_files(root):
        files += 1
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    # First user message per session — the "what was the goal"
                    # label. We keep the EARLIEST one (the opening prompt).
                    intent = None
                    try:
                        intent = parse_user_intent(raw)
                    except Exception:
                        pass
                    if intent:
                        sid, text, ts = intent
                        prev_ts = user_intent_ts.get(sid)
                        if prev_ts is None or ts < prev_ts:
                            user_intents[sid] = text[:240]
                            user_intent_ts[sid] = ts

                    # Tool errors + tool_use names — behavioral analytics.
                    try:
                        evt = parse_tool_events(raw)
                    except Exception:
                        evt = None
                    if evt and evt["errors"]:
                        for e in evt["errors"]:
                            error_events.append({
                                "session_id": evt["session_id"],
                                "timestamp": evt["timestamp"].isoformat() if evt["timestamp"] else None,
                                "category": e["category"],
                                "snippet": e["snippet"],
                            })

                    try:
                        rec = parse_line(raw, source_file=str(path))
                    except Exception:
                        errors += 1
                        continue
                    if rec is None:
                        continue
                    if deduplicate and rec.message_id:
                        if rec.message_id in seen_ids:
                            continue
                        seen_ids.add(rec.message_id)
                    records.append(rec)
        except OSError:
            errors += 1
            continue

    records.sort(key=lambda r: r.timestamp)
    return records, {
        "files_scanned": files,
        "parse_errors": errors,
        "root": str(root),
        "root_exists": True,
        "error_events_count": len(error_events),
    }, user_intents, error_events
