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
    }
    enrich_report(rep, titles={"sid-1": "World Intelligence"})
    assert rep["recent_turns"][0]["chat"] == "World Intelligence"
    assert "chat" not in rep["recent_turns"][1]          # unknown session → no key (UI falls back)
    assert rep["live_active_models_by_window"]["5"]["models"][0]["chat"] == "World Intelligence"


def test_enrich_report_no_titles_is_noop():
    rep = {"recent_turns": [{"session_id": "x"}]}
    enrich_report(rep, titles={})
    assert "chat" not in rep["recent_turns"][0]


def test_missing_store_root_is_safe(tmp_path):
    assert load_chat_titles(roots=[tmp_path / "does-not-exist"]) == {}
