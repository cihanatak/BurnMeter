"""DNS-rebinding guard on the local API.

Burnmeter runs an HTTP server on 127.0.0.1 holding the user's entire local usage
history — project paths, branch names, chat titles, per-session cost — and, for
Pro users, the decrypted cross-device snapshot. It also exposes POST /api/update,
which installs and relaunches software.

A page on evil.com can point its own DNS at 127.0.0.1. The browser then treats
this server as same-origin, so CORS no longer applies and a custom request header
is no longer proof that our own dashboard sent it. The Host header is the part the
attacker cannot forge: the browser sends the name the user navigated to. These
tests pin that guard.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from burnmeter import server as srv


class _FakeHandler:
    """Minimal stand-in exposing just what the guard touches."""

    def __init__(self, host_header, bound="127.0.0.1"):
        self.headers = {"Host": host_header} if host_header is not None else {}
        self.server = MagicMock()
        self.server.server_address = (bound, 7654)
        self.sent = []

    def _json(self, status, payload):
        self.sent.append((status, payload))


def _guard(host_header, bound="127.0.0.1", allowed_env=None, monkeypatch=None):
    """Run the real guard against a fake request and report whether it passed."""
    handler_cls = _handler_class()
    h = _FakeHandler(host_header, bound)
    if monkeypatch is not None:
        monkeypatch.setenv("BURNMETER_ALLOWED_HOSTS", allowed_env or "")
    return handler_cls._api_host_ok(h)


def _handler_class():
    from pathlib import Path
    cache = srv._Cache(projects_dir=Path("."), ttl_seconds=999,
                       loader=lambda: ([], {}, {}, []))
    return srv.make_handler(cache)


@pytest.mark.parametrize("host", [
    "127.0.0.1", "127.0.0.1:7654", "localhost", "localhost:7654",
    "[::1]:7654", "LOCALHOST:7654",
])
def test_loopback_hosts_are_allowed(host):
    assert _guard(host) is True


@pytest.mark.parametrize("host", [
    "evil.com", "evil.com:7654", "burnmeter.attacker.test",
    "sub.domain.example:7654", "notlocalhost",
])
def test_foreign_hostnames_are_rejected(host):
    """This is the rebinding vector: a NAME that currently resolves to loopback."""
    assert _guard(host) is False


def test_missing_host_header_is_rejected():
    assert _guard(None) is False


def test_bound_lan_address_is_allowed_but_other_names_are_not():
    """A power user binding a LAN address must keep working."""
    assert _guard("192.168.1.50:7654", bound="192.168.1.50") is True
    assert _guard("evil.com:7654", bound="192.168.1.50") is False


def test_wildcard_bind_accepts_ip_literals_and_still_rejects_names():
    """On 0.0.0.0 any typed IP is legitimate; a hostname never is."""
    assert _guard("192.168.1.50:7654", bound="0.0.0.0") is True
    assert _guard("10.0.0.9", bound="0.0.0.0") is True
    assert _guard("evil.com", bound="0.0.0.0") is False


def test_allowlist_env_lets_a_reverse_proxy_through(monkeypatch):
    assert _guard("burn.mydomain.dev", allowed_env="burn.mydomain.dev",
                  monkeypatch=monkeypatch) is True
    assert _guard("evil.com", allowed_env="burn.mydomain.dev",
                  monkeypatch=monkeypatch) is False


def test_update_endpoint_is_covered_by_the_guard():
    """/api/update triggers an install + relaunch, so it must be behind the same
    check as everything else — it used to rely on a custom header alone, which a
    rebound same-origin page can set freely."""
    src = (srv.__file__ or "")
    text = open(src, encoding="utf-8").read()
    post = text[text.index("def do_POST"):text.index("def do_POST") + 400]
    assert "_reject_foreign_host" in post, "do_POST must reject foreign hosts first"
    get = text[text.index("def do_GET"):text.index("def do_GET") + 400]
    assert "_reject_foreign_host" in get, "do_GET must reject foreign hosts first"


def test_guard_only_applies_to_api_paths():
    """The dashboard HTML/CSS/JS itself is public code; blocking it would break
    normal browser use without protecting anything."""
    handler_cls = _handler_class()
    h = _FakeHandler("evil.com")
    h._json = lambda *a: None
    assert handler_cls._reject_foreign_host(h, "/") is False
    assert handler_cls._reject_foreign_host(h, "/static/dashboard.js") is False
