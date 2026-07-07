"""Chat titles (sohbet adları) for sessions — from the Claude desktop app's local store.

Why: the project FOLDER is ambiguous ("repo" — which repo? one workspace hosts many chats),
so the UI shows the CHAT TITLE the user sees in the desktop sidebar ("World Intelligence",
"Burnmeter", …). The desktop app persists one small JSON per session at:

  Windows:  %APPDATA%/Claude/claude-code-sessions/<org>/<user>/local_<id>.json
  macOS:    ~/Library/Application Support/Claude/claude-code-sessions/...
  Linux:    ~/.config/Claude/claude-code-sessions/...

each carrying {cliSessionId, title, titleSource, ...}. cliSessionId == the session_id in
~/.claude/projects transcripts, so mapping is direct. Read-only, fail-silent (no desktop
app → no titles → the UI falls back to the folder name). Cached with a short TTL — the
store is ~dozens of tiny files, but report builds run every few seconds.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_TTL_SECONDS = 30
_cache: dict = {"ts": 0.0, "map": {}}


def _store_roots() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            roots.append(Path(appdata) / "Claude" / "claude-code-sessions")
    elif sys.platform == "darwin":
        roots.append(Path.home() / "Library" / "Application Support" / "Claude" / "claude-code-sessions")
    else:
        roots.append(Path.home() / ".config" / "Claude" / "claude-code-sessions")
    return [r for r in roots if r.is_dir()]


def load_chat_titles(roots: list[Path] | None = None) -> dict[str, str]:
    """session_id → chat title. TTL-cached; pass explicit roots to bypass the cache (tests)."""
    use_cache = roots is None
    if use_cache:
        now = time.time()
        if now - _cache["ts"] < _TTL_SECONDS:
            return _cache["map"]
        roots = _store_roots()
    out: dict[str, str] = {}
    for root in roots:
        try:
            for p in root.rglob("local_*.json"):
                try:
                    j = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                    sid = j.get("cliSessionId")
                    title = (j.get("title") or "").strip()
                    if sid and title:
                        # user-set titles win over auto ones if both files exist for a session
                        if sid not in out or j.get("titleSource") == "user":
                            out[sid] = title
                except Exception:
                    continue
        except Exception:
            continue
    if use_cache:
        _cache["map"] = out
        _cache["ts"] = time.time()
    return out


def enrich_report(rep: dict, titles: dict[str, str] | None = None) -> dict:
    """Attach chat titles to recent_turns[] and live_active_models_by_window[*].models[]
    (in place). Entries without a known session keep no 'chat' key → UI falls back."""
    t = titles if titles is not None else load_chat_titles()
    if not t:
        return rep
    for turn in rep.get("recent_turns") or []:
        c = t.get(turn.get("session_id") or "")
        if c:
            turn["chat"] = c
    for w in (rep.get("live_active_models_by_window") or {}).values():
        for m in (w or {}).get("models") or []:
            c = t.get(m.get("session_id") or "")
            if c:
                m["chat"] = c
    for s in rep.get("by_session") or []:
        c = t.get(s.get("session_id") or "")
        if c:
            s["chat"] = c
    return rep
