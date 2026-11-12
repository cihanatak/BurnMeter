"""Codex CLI 0.164+ compresses finished rollouts to rollout-*.jsonl.zst.

Guards: compressed logs are read (same records as the plain file), a session in
both forms counts once, an unreadable .zst is skipped and counted (never a
crash), odd lines and damaged caches don't abort the load, and CODEX_HOME is
honoured. Temp folders only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import burnmeter.codex_parser as cp
from burnmeter.codex_parser import load_codex_records

from test_codex_correctness import write_rollout

zstd = pytest.importorskip("backports.zstd") if sys.version_info < (3, 14) else \
    pytest.importorskip("compression.zstd")

SID = "019e0000-0000-7000-8000-00000000cccc"
TURNS = [
    ("T1", 100, 0, 50, "2026-10-01T10:00:00.000Z"),
    ("T2", 200, 50, 80, "2026-10-01T10:01:00.000Z"),
    ("T3", 300, 100, 90, "2026-10-01T10:05:00.000Z"),
]


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "_CACHE_PATH", tmp_path / "_cache.json")
    monkeypatch.setattr(cp, "_OLD_CACHE_PATH", tmp_path / "_old_cache.json")


def _compress(plain: Path, remove: bool = True) -> Path:
    z = plain.with_name(plain.name + ".zst")
    z.write_bytes(zstd.compress(plain.read_bytes()))
    if remove:
        plain.unlink()
    return z


def _sig(recs):
    return sorted((r.session_id, r.turn_id, r.model, r.input_tokens, r.output_tokens,
                   r.cache_read_tokens, r.timestamp.isoformat(), r.project_dir)
                  for r in recs)


def _load(root):
    return load_codex_records(root, since_days=0)


def test_compressed_yields_same_records_as_plain(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    write_rollout(a / "rollout-2026-10-01T10-00-00-x.jsonl", SID, TURNS)
    write_rollout(b / "rollout-2026-10-01T10-00-00-x.jsonl", SID, TURNS)
    _compress(b / "rollout-2026-10-01T10-00-00-x.jsonl")
    plain, ps, _ = _load(a)
    comp, cs, _ = _load(b)
    assert len(plain) == 3
    assert _sig(comp) == _sig(plain)
    assert cs["compressed_files"] == 1 and cs["compressed_unread"] == 0
    assert ps["compressed_files"] == 0
    # second load is a cache hit and gives the same answer
    again, s2, _ = _load(b)
    assert s2["reparsed"] == 0 and _sig(again) == _sig(plain)


def test_compressed_last_line_without_newline_counts(tmp_path):
    p = tmp_path / "rollout-2026-10-01T10-00-00-x.jsonl"
    write_rollout(p, SID, TURNS)
    p.write_bytes(p.read_bytes().rstrip(b"\n"))
    _compress(p)
    recs, _, _ = _load(tmp_path)
    assert len(recs) == 3


def test_plain_and_compressed_both_present_counted_once(tmp_path):
    p = tmp_path / "rollout-2026-10-01T10-00-00-x.jsonl"
    write_rollout(p, SID, TURNS)
    _compress(p, remove=False)
    assert [f.name for f in cp.iter_codex_files(tmp_path)] == [p.name]
    recs, st, _ = _load(tmp_path)
    assert len(recs) == 3 and st["files_scanned"] == 1


def test_plain_then_compressed_not_double_counted(tmp_path):
    """Parsed as .jsonl, then Codex compresses it: one re-parse, same totals."""
    p = tmp_path / "rollout-2026-10-01T10-00-00-x.jsonl"
    write_rollout(p, SID, TURNS)
    first, _, _ = _load(tmp_path)
    _compress(p)
    second, st, _ = _load(tmp_path)
    assert _sig(second) == _sig(first)
    cache = json.loads(cp._CACHE_PATH.read_text(encoding="utf-8"))
    assert str(p) not in cache and str(p) + ".zst" in cache


def test_no_zstd_module_skips_and_counts(tmp_path, monkeypatch):
    write_rollout(tmp_path / "rollout-2026-10-01T10-00-00-plain.jsonl", SID, TURNS)
    z = tmp_path / "rollout-2026-10-02T10-00-00-z.jsonl"
    write_rollout(z, "019e0000-0000-7000-8000-00000000dddd",
                  [("Z1", 7, 0, 3, "2026-10-02T10:00:00.000Z")])
    _compress(z)
    monkeypatch.setattr(cp, "_zstd_opener", lambda: None)
    recs, st, _ = _load(tmp_path)
    assert len(recs) == 3
    assert st["compressed_unread"] == 1
    assert st["compressed_unread_reasons"] == {"no_zstd": 1, "corrupt": 0}
    # not remembered as failed: once a module is available the file is read
    monkeypatch.undo()
    monkeypatch.setattr(cp, "_CACHE_PATH", tmp_path / "_cache.json")
    monkeypatch.setattr(cp, "_OLD_CACHE_PATH", tmp_path / "_old_cache.json")
    recs, st, _ = _load(tmp_path)
    assert len(recs) == 4 and st["compressed_unread"] == 0


@pytest.mark.parametrize("damage", ["garbage", "truncated"])
def test_corrupt_zst_skipped_and_counted(tmp_path, damage):
    write_rollout(tmp_path / "rollout-2026-10-01T10-00-00-plain.jsonl", SID, TURNS)
    z = tmp_path / "rollout-2026-10-02T10-00-00-z.jsonl"
    write_rollout(z, "019e0000-0000-7000-8000-00000000dddd",
                  [("Z1", 7, 0, 3, "2026-10-02T10:00:00.000Z")] * 1)
    zp = _compress(z)
    data = zp.read_bytes()
    zp.write_bytes(b"not zstd at all \x00\xff" * 10 if damage == "garbage" else data[:len(data) // 2])
    for _ in range(2):   # second build serves the failure from the cache
        recs, st, _ = _load(tmp_path)
        assert len(recs) == 3
        assert st["compressed_unread"] == 1
        assert st["compressed_unread_reasons"]["corrupt"] == 1


def test_odd_lines_do_not_abort_load(tmp_path):
    """Review F7: a string payload, an array or a number containing "token_count",
    and non-dict info / last_token_usage must each be skipped."""
    p = tmp_path / "rollout-2026-10-01T10-00-00-x.jsonl"
    write_rollout(p, SID, TURNS)
    odd = [
        '{"type":"event_msg","payload":"token_count"}',
        '["token_count"]',
        '"token_count"',
        '{"type":"event_msg","payload":{"type":"token_count","info":"x"}}',
        '{"type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":[1]}}}',
        '{"type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":'
        '{"input_tokens":"abc"}}}}',
        '{"type":"event_msg","timestamp":5,"payload":{"type":"token_count",'
        '"rate_limits":{"primary":{"x":1}},"info":null}}',
        '{"type":"turn_context","payload":null}',
    ]
    with p.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(odd) + "\n")
    recs, _, _ = _load(tmp_path)
    assert len(recs) == 3


@pytest.mark.parametrize("content", [b"\x81\x82garbage\xff", b"[1, 2, 3]", b'{"k": 5}'])
def test_damaged_cache_means_no_cache(tmp_path, content):
    """Review F6: undecodable bytes, a list-shaped cache, or an entry that isn't a dict."""
    root = tmp_path / "s"
    root.mkdir()
    p = root / "rollout-2026-10-01T10-00-00-x.jsonl"
    write_rollout(p, SID, TURNS)
    if content == b'{"k": 5}':
        content = json.dumps({str(p): 5, str(p) + "x": [1]}).encode()
    cp._CACHE_PATH.write_bytes(content)
    recs, st, _ = _load(root)
    assert len(recs) == 3 and st["reparsed"] == 1
    assert isinstance(json.loads(cp._CACHE_PATH.read_text(encoding="utf-8")), dict)


def test_codex_home_env(tmp_path):
    home = tmp_path / "codexhome"
    env = {**os.environ, "CODEX_HOME": str(home)}
    out = subprocess.run(
        [sys.executable, "-c",
         "import burnmeter.codex_parser as c;"
         "print(c.CODEX_SESSIONS_DIR); print(c._CACHE_PATH)"],
        env=env, capture_output=True, text=True, check=True,
        cwd=str(Path(__file__).resolve().parents[1])).stdout.split("\n")
    assert Path(out[0]) == home / "sessions"
    assert Path(out[1]) == home / ".burnmeter_codex_cache.json"


def test_codex_home_default(monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    assert cp._codex_home() == Path.home() / ".codex"
    monkeypatch.setenv("CODEX_HOME", "  ")
    assert cp._codex_home() == Path.home() / ".codex"
