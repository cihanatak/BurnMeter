"""End-to-end test for Burnmeter Pro sync: E2E crypto + self-host relay + Pro-gate.

Requires the [sync] extra (cryptography). Skipped if not installed.
"""
import base64
import json
import os
import socket
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("cryptography")  # [sync] extra; skip cleanly if absent

import burnmeter.sync as sync          # noqa: E402
from burnmeter import sync_relay        # noqa: E402


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _start_relay(storage, free_tokens=""):
    os.environ["BURNMETER_RELAY_FREE_TOKENS"] = free_tokens
    port = _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), sync_relay.make_handler(Path(storage)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.05)
    return httpd, f"http://127.0.0.1:{port}"


def _cfg(relay_url, token="acct-token-xyz", passphrase="correct horse battery"):
    return {"relay_url": relay_url, "account_token": token,
            "passphrase": passphrase, "device_id": "devAAA", "label": "MacBook"}


def test_crypto_roundtrip_and_ciphertext():
    cfg = _cfg("http://x")
    snap = {"v": 1, "device_id": "devAAA", "sources": {"claude": {"month_so_far": 4242.5}}}
    blob = sync.encrypt_snapshot(snap, cfg)
    # relay/disk must hold CIPHERTEXT — the plaintext value must not appear
    assert b"4242.5" not in blob
    assert sync.decrypt_snapshot(blob, cfg) == snap


def test_wrong_passphrase_cannot_decrypt():
    snap = {"v": 1, "sources": {}}
    blob = sync.encrypt_snapshot(snap, _cfg("http://x", passphrase="right one"))
    with pytest.raises(Exception):
        sync.decrypt_snapshot(blob, _cfg("http://x", passphrase="WRONG one"))


def test_relay_push_pull_roundtrip_and_zero_knowledge():
    with tempfile.TemporaryDirectory() as storage:
        httpd, url = _start_relay(storage)
        try:
            cfg = _cfg(url)
            snap = {"v": 1, "device_id": cfg["device_id"], "label": "MacBook",
                    "sources": {"claude": {"month_so_far": 777.0, "record_count": 9}}}
            blob = sync.encrypt_snapshot(snap, cfg)
            status, _ = sync._req(cfg, "PUT", "/v1/snap/" + cfg["device_id"], body=blob)
            assert status == 201

            # zero-knowledge: the relay's on-disk blob is ciphertext (no plaintext value)
            stored = list(Path(storage).rglob("*.json"))
            assert stored, "relay stored nothing"
            raw = stored[0].read_text()
            assert "777" not in raw and "MacBook" not in raw

            # pull → decrypt → identical snapshot
            devs = sync.pull(cfg)
            assert len(devs) == 1
            assert devs[0]["sources"]["claude"]["month_so_far"] == 777.0
            assert devs[0]["label"] == "MacBook"
        finally:
            httpd.shutdown()


def test_pro_gate_blocks_free_token():
    with tempfile.TemporaryDirectory() as storage:
        cfg = _cfg("http://x", token="free-tok-1")
        httpd, url = _start_relay(storage, free_tokens="free-tok-1")
        cfg["relay_url"] = url
        try:
            blob = sync.encrypt_snapshot({"v": 1, "sources": {}}, cfg)
            import urllib.error
            with pytest.raises(urllib.error.HTTPError) as ei:
                sync._req(cfg, "PUT", "/v1/snap/" + cfg["device_id"], body=blob)
            assert ei.value.code == 402   # Pro required
        finally:
            httpd.shutdown()


def test_device_snapshot_shape_no_logs():
    # empty dirs → no sources, but a well-formed snapshot (no crash)
    with tempfile.TemporaryDirectory() as d:
        cfg = _cfg("http://x")
        snap = sync.build_device_snapshot(cfg, Path(d), None)
        assert snap["device_id"] == "devAAA"
        assert "sources" in snap and isinstance(snap["sources"], dict)
        assert snap["v"] == sync.SNAPSHOT_VERSION
