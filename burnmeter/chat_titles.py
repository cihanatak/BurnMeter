"""Chat titles (sohbet adları) for sessions — from the Claude desktop app's local store.

Why: the project FOLDER is ambiguous ("repo" — which repo? one workspace hosts many chats),
so the UI shows the CHAT TITLE the user sees in the desktop sidebar ("World Intelligence",
"Burnmeter", …). The desktop app persists one small JSON per session at:

  Windows:  %LOCALAPPDATA%/Claude-Data/claude_<id>/claude-code-sessions/<org>/<user>/local_<id>.json
            (older builds: %APPDATA%/Claude/claude-code-sessions/...)
  macOS:    ~/Library/Application Support/Claude/claude-code-sessions/...
  Linux:    ~/.config/Claude/claude-code-sessions/...

each carrying {cliSessionId, title, titleSource, ...}. cliSessionId == the session_id in
~/.claude/projects transcripts, so mapping is direct. Read-only, fail-silent (no desktop
app → no titles → the UI falls back to the folder name). Cached with a short TTL, and a
file is parsed again only when it changed: the store has hundreds of files of up to
~125 KB (20 MB on the maker's PC), and report builds run every few seconds.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_TTL_SECONDS = 30
_cache: dict = {"ts": 0.0, "map": {}}
_files: dict = {}   # path → (mtime_ns, size, session id, title, set by the user)


def _store_roots() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            roots.append(Path(appdata) / "Claude" / "claude-code-sessions")
        # Later desktop builds keep it per Windows account, in Claude-Data/claude_<id>.
        local = os.environ.get("LOCALAPPDATA")
        if local:
            roots.extend(sorted((Path(local) / "Claude-Data").glob("*/claude-code-sessions")))
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
    global _files
    out: dict[str, str] = {}
    seen: dict = {}
    for root in roots:
        try:
            for p in root.rglob("local_*.json"):
                try:
                    st = p.stat()
                    hit = _files.get(str(p))
                    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
                        sid, title, user_set = hit[2:]
                    else:
                        j = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                        sid = j.get("cliSessionId")
                        title = (j.get("title") or "").strip()
                        user_set = j.get("titleSource") == "user"
                    seen[str(p)] = (st.st_mtime_ns, st.st_size, sid, title, user_set)
                    if sid and title:
                        # user-set titles win over auto ones if both files exist for a session
                        if sid not in out or user_set:
                            out[sid] = title
                except Exception:
                    continue
        except Exception:
            continue
    _files = seen
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
