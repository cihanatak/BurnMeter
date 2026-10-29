"""Keep the test suite off the user's real parse caches.

The Claude and Codex loaders cache parsed logs under ~/.claude and ~/.codex,
and an installed Burnmeter shares those files. A test that scans a temp tree
must never rewrite them, so the whole run points both caches at a throwaway
directory. Set once and never restored: a server thread a test leaves behind
can still save after its test has finished.
"""
import atexit
import shutil
import tempfile
from pathlib import Path

import burnmeter.codex_parser as _codex
import burnmeter.parser as _claude

_CACHE_DIR = Path(tempfile.mkdtemp(prefix="burnmeter-test-caches-"))
_claude._CLAUDE_CACHE_PATH = _CACHE_DIR / "claude_cache.json"
_codex._CACHE_PATH = _CACHE_DIR / "codex_cache.json"
_codex._OLD_CACHE_PATH = _CACHE_DIR / "codex_old_cache.json"
atexit.register(shutil.rmtree, _CACHE_DIR, True)
