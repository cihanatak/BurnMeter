"""Friendly device-name logic — never an IP, migrates IP-ish auto labels,
never overrides a user-set name."""
import burnmeter.sync as sync


def test_friendly_name_is_not_an_ip():
    n = sync.friendly_device_name()
    assert n and len(n) >= 2
    assert not sync._looks_like_ip(n)


def test_looks_like_ip():
    assert sync._looks_like_ip("192.168.1.75")
    assert sync._looks_like_ip("10.0.0.1")
    assert sync._looks_like_ip("")
    assert not sync._looks_like_ip("Cihan-MacBook")
    assert not sync._looks_like_ip("DESKTOP-7G3K")


def test_resolve_migrates_ip_label():
    out = sync.resolve_device_label({"label": "192.168.1.75", "name_source": "auto"})
    assert not sync._looks_like_ip(out)          # migrated away from the IP


def test_resolve_keeps_user_name():
    assert sync.resolve_device_label({"label": "My MacBook", "name_source": "user"}) == "My MacBook"
    # even an IP stays if the user explicitly set it (their choice)
    assert sync.resolve_device_label({"label": "10.0.0.5", "name_source": "user"}) == "10.0.0.5"


def test_resolve_empty_gives_friendly():
    out = sync.resolve_device_label({})
    assert out and not sync._looks_like_ip(out)


def test_clean_name_strips_local_suffix():
    assert sync._clean_name("Cihans-MBP.local") == "Cihans-MBP"
    assert sync._clean_name("192.168.1.75") is None
