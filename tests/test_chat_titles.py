"""Chat titles (sohbet adı): desktop-store parsing + report enrichment. The folder name is
ambiguous (one workspace hosts many chats), so recent/live rows show the chat title."""
import json

from burnmeter.chat_titles import load_chat_titles, enrich_report


def _store(tmp_path, entries):
    root = tmp_path / "claude-code-sessions" / "org" / "user"
    root.mkdir(parents=True)
    for i, e in enumerate(entries):
        (root / f"local_{i}.json").write_text(json.dumps(e), encoding="utf-8")
    return tmp_path / "claude-code-sessions"


def test_load_titles_maps_session_to_title(tmp_path):
    root = _store(tmp_path, [
        {"cliSessionId": "sid-1", "title": "World Intelligence", "titleSource": "user"},
        {"cliSessionId": "sid-2", "title": "Burnmeter", "titleSource": "auto"},
        {"cliSessionId": "sid-3", "title": "", "titleSource": "auto"},          # empty → skipped
        {"cliSessionId": None, "title": "orphan"},                                 # no sid → skipped
        {"not": "even close"},                                                     # malformed → skipped
    ])
    m = load_chat_titles(roots=[root])
    assert m == {"sid-1": "World Intelligence", "sid-2": "Burnmeter"}


def test_user_title_wins_over_auto(tmp_path):
    root = _store(tmp_path, [
        {"cliSessionId": "sid-1", "title": "auto name", "titleSource": "auto"},
        {"cliSessionId": "sid-1", "title": "My Real Name", "titleSource": "user"},
    ])
    assert load_chat_titles(roots=[root])["sid-1"] == "My Real Name"


def test_enrich_report_attaches_chat_to_recent_and_live():
    rep = {
        "recent_turns": [{"session_id": "sid-1"}, {"session_id": "unknown"}],
        "live_active_models_by_window": {"5": {"models": [{"session_id": "sid-1"}]}},
        "by_session": [{"session_id": "sid-1"}, {"session_id": "nope"}],
    }
    enrich_report(rep, titles={"sid-1": "World Intelligence"})
    assert rep["recent_turns"][0]["chat"] == "World Intelligence"
    assert "chat" not in rep["recent_turns"][1]          # unknown session → no key (UI falls back)
    assert rep["live_active_models_by_window"]["5"]["models"][0]["chat"] == "World Intelligence"
    assert rep["by_session"][0]["chat"] == "World Intelligence"   # the Chats view's source
    assert "chat" not in rep["by_session"][1]


def test_enrich_report_no_titles_is_noop():
    rep = {"recent_turns": [{"session_id": "x"}]}
    enrich_report(rep, titles={})
    assert "chat" not in rep["recent_turns"][0]


def test_missing_store_root_is_safe(tmp_path):
    assert load_chat_titles(roots=[tmp_path / "does-not-exist"]) == {}


def test_build_report_attaches_chat_titles(monkeypatch):
    """build_report is the SINGLE choke point every build path goes through (worker
    subprocess, in-process, statusline, sync auto-push). Hooking enrichment only in the
    server callback missed the worker path (0.1.73 dev bug) — guard it HERE."""
    from datetime import datetime, timezone
    import burnmeter.chat_titles as ct
    from burnmeter.analytics import build_report
    from burnmeter.parser import UsageRecord

    monkeypatch.setattr(ct, "load_chat_titles", lambda roots=None: {"sess": "World Intelligence"})
    rec = UsageRecord(
        timestamp=datetime.now(timezone.utc), session_id="sess", project_dir="/p",
        project_label="proj", git_branch="main", model="claude-opus-4-8", message_id="t1",
        input_tokens=100, output_tokens=10, cache_creation_tokens=0, cache_read_tokens=0,
        tool_names=[], source_file="", uuid="t1", parent_uuid="", turn_id="t1", device="pc",
    )
    rep = build_report([rec])
    assert rep["recent_turns"][0]["chat"] == "World Intelligence"
    lam = rep["live_active_models_by_window"]["5"]["models"]
    assert lam and lam[0].get("chat") == "World Intelligence"


def test_the_moved_windows_store_is_read(monkeypatch, tmp_path):
    """Later desktop builds keep the store in %LOCALAPPDATA%/Claude-Data/claude_<id>;
    reading only %APPDATA%/Claude left every chat on Windows with its folder name."""
    import burnmeter.chat_titles as ct
    new = tmp_path / "local" / "Claude-Data" / "claude_abc" / "claude-code-sessions"
    (new / "org" / "user").mkdir(parents=True)
    (new / "org" / "user" / "local_1.json").write_text(
        json.dumps({"cliSessionId": "sid-1", "title": "Burnmeter", "titleSource": "user"}), encoding="utf-8")
    monkeypatch.setattr(ct.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))          # the old place: absent
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert ct._store_roots() == [new]
    assert load_chat_titles(roots=ct._store_roots()) == {"sid-1": "Burnmeter"}


def test_a_file_is_parsed_again_only_when_it_changes(monkeypatch, tmp_path):
    import os
    import burnmeter.chat_titles as ct
    root = _store(tmp_path, [{"cliSessionId": "sid-1", "title": "first", "titleSource": "auto"}])
    f = next(root.rglob("local_*.json"))
    assert load_chat_titles(roots=[root]) == {"sid-1": "first"}
    real_loads = ct.json.loads
    monkeypatch.setattr(ct.json, "loads", lambda *a, **k: (_ for _ in ()).throw(AssertionError("parsed")))
    assert load_chat_titles(roots=[root]) == {"sid-1": "first"}       # unchanged → from the cache
    monkeypatch.setattr(ct.json, "loads", real_loads)
    f.write_text(json.dumps({"cliSessionId": "sid-1", "title": "renamed", "titleSource": "user"}), encoding="utf-8")
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))   # a coarse clock can't hide the change
    assert load_chat_titles(roots=[root]) == {"sid-1": "renamed"}
