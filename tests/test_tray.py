"""Tray smoke tests — catch import/name errors in the tray module without
actually starting a system-tray icon (which needs a display)."""
import pytest


def test_make_image_builds_without_error():
    """_make_image() must not raise — a missing import here (e.g. Path) would
    crash run_tray and silently drop the app to the browser fallback."""
    pytest.importorskip("PIL")
    import burnmeter.tray as tray
    img = tray._make_image()
    assert img is not None
    assert img.size[0] > 0 and img.size[1] > 0


def test_asset_path_returns_path_or_none():
    import burnmeter.tray as tray
    p = tray._asset_path("burnmeter.png")
    # bundled in the repo → should resolve; tolerate None in odd layouts.
    assert p is None or p.exists()
