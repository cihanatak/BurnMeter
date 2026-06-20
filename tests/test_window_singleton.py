"""Single-instance window lock: one native window only. Race-safe atomic claim
with a stale-lock steal (a crashed window must not block the next launch)."""
import burnmeter.window as window


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(window, "_WINDOW_LOCK", tmp_path / "window.lock")


def test_claim_then_blocks_a_live_other_owner(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    # first claim by us → succeeds and writes our pid
    assert window._claim_window_singleton() is True
    assert window._WINDOW_LOCK.exists()
    # simulate a DIFFERENT, still-alive window owning the lock
    window._WINDOW_LOCK.write_text("99999999")
    monkeypatch.setattr(window, "_win_pid_alive", lambda pid: True)
    assert window._claim_window_singleton() is False     # blocked → caller focuses + exits


def test_steals_a_stale_lock(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    window._WINDOW_LOCK.write_text("99999999")            # owner from a crashed run
    monkeypatch.setattr(window, "_win_pid_alive", lambda pid: False)   # …which is dead
    assert window._claim_window_singleton() is True       # steal + take over
    assert window._WINDOW_LOCK.read_text().strip() == str(__import__("os").getpid())


def test_release_only_removes_our_own_lock(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert window._claim_window_singleton() is True
    window._release_window_singleton()
    assert not window._WINDOW_LOCK.exists()
    # a lock owned by someone else is left alone
    window._WINDOW_LOCK.write_text("12345")
    window._release_window_singleton()
    assert window._WINDOW_LOCK.exists()


def test_steal_takes_ownership_from_a_live_non_window_pid(tmp_path, monkeypatch):
    # A live PID owns the lock but it's NOT a Burnmeter window (crashed window +
    # reused PID). The caller steals so the user is never left with zero windows.
    _fresh(tmp_path, monkeypatch)
    window._WINDOW_LOCK.write_text("99999999")
    monkeypatch.setattr(window, "_win_pid_alive", lambda pid: True)
    assert window._claim_window_singleton() is False        # blocked (owner "alive")
    window._steal_window_singleton()                        # …but no real window → steal
    import os
    assert window._WINDOW_LOCK.read_text().strip() == str(os.getpid())


def test_claim_reclaims_after_a_clean_release(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert window._claim_window_singleton() is True
    window._release_window_singleton()
    assert window._claim_window_singleton() is True         # next launch can claim again


def test_geometry_roundtrip_and_bounds(tmp_path, monkeypatch):
    monkeypatch.setattr(window, "_WINDOW_GEOM", tmp_path / "geo.json")
    monkeypatch.setattr(window, "_virtual_screen", lambda: None)   # portable: skip screen check
    assert window._load_geometry() is None                  # nothing saved yet
    window._save_geometry({"x": 120, "y": 60, "width": 1400, "height": 900})
    assert window._load_geometry() == {"x": 120, "y": 60, "width": 1400, "height": 900, "maximized": False}
    # absurd coord rejected → fall back to default maximized
    window._save_geometry({"x": -99999, "y": 0, "width": 1400, "height": 900})
    assert window._load_geometry() is None
    # absurd size rejected too
    window._save_geometry({"x": 0, "y": 0, "width": 50, "height": 50})
    assert window._load_geometry() is None
    # a maximized session round-trips the flag
    window._save_geometry({"x": 0, "y": 0, "width": 2560, "height": 1440, "maximized": True})
    assert window._load_geometry()["maximized"] is True
