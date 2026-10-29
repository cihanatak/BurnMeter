"""firestore.rules is the only thing that makes Pro sync a Pro feature.

The cases in firestore_rules_cases.json run on Google's real rules engine through the
Rules API test endpoint, which deploys nothing. That needs network and a gcloud login, so
it runs only with BURNMETER_RULES_TEST=1 (`python scripts/pro_admin.py rules-test` does
the same with a per-case report). The checks on the case file itself always run.
"""
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("pro_admin", ROOT / "scripts" / "pro_admin.py")
pro_admin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pro_admin)
SPEC = json.loads((ROOT / "tests" / "firestore_rules_cases.json").read_text(encoding="utf-8"))


def test_every_case_is_well_formed():
    names = [c["name"] for c in SPEC["cases"]]
    assert len(names) == len(set(names)), "case names must be unique: the report goes by name"
    for c in SPEC["cases"]:
        assert c["expect"] in ("ALLOW", "DENY"), c["name"]
        assert c["op"] in ("get", "list", "create", "update", "delete"), c["name"]
        assert c["as"] is None or c["as"] in SPEC["accounts"], c["name"]
        assert ("data" in c) == (c["op"] in ("create", "update")), c["name"]
        assert ("stored" in c) == (c["op"] in ("update", "get", "delete")), c["name"]


def test_device_docs_have_allow_and_deny_cases_for_every_operation():
    for op in ("create", "update", "get", "list", "delete"):
        seen = {c["expect"] for c in SPEC["cases"] if c["op"] == op and "/devices/" in c["path"]}
        assert seen == {"ALLOW", "DENY"}, op


def test_the_suite_hands_the_engine_typed_values():
    suite = pro_admin.build_test_suite(SPEC)
    cases = suite["testCases"]
    assert len(cases) == len(SPEC["cases"])
    first = cases[0]["request"]
    assert first["path"] == "/databases/(default)/documents/users/alice/devices/0123456789ab"
    assert first["auth"] == {"uid": "alice", "token": {"email_verified": True}}
    assert first["resource"]["data"]["updated_at"] == first["time"] == "2026-10-29T12:00:00Z"
    big = next(c for c in SPEC["cases"] if c["name"] == "a blob over 256 KiB is refused")
    assert len(pro_admin._value(big["data"], pro_admin.datetime.now())["blob"]) == 262145
    # every case can see every account's users doc; a missing one is null
    mocks = {m["args"][0]["exactValue"].rsplit("/", 1)[-1]: m["result"]["value"]
             for m in cases[0]["functionMocks"]}
    assert mocks["erin"] is None and mocks["alice"] == {"data": {"plan": "pro", "status": "active"}}


@pytest.mark.skipif(os.environ.get("BURNMETER_RULES_TEST") != "1",
                    reason="needs network + a gcloud login; set BURNMETER_RULES_TEST=1")
def test_the_rules_on_the_real_engine():
    results = pro_admin.run_rules_tests(pro_admin.token())
    failed = [f"{r['expect']} {r['name']} {r['detail']}" for r in results if not r["ok"]]
    assert not failed, "\n".join(failed)
