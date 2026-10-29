#!/usr/bin/env python3
"""Operator tools for Burnmeter Pro on Firebase (project burnmeter-4e5f3).

Runs with YOUR gcloud login (`gcloud auth login`), never with anything the app ships:

  python scripts/pro_admin.py rules-test       run tests/firestore_rules_cases.json against
                                               firestore.rules on Google's rules engine
                                               (Rules API projects:test; deploys nothing)
  python scripts/pro_admin.py rules-show       the live ruleset's id and source
  python scripts/pro_admin.py rules-deploy     test, then upload firestore.rules and make it live
  python scripts/pro_admin.py rules-rollback <ruleset-id>
  python scripts/pro_admin.py stats            counts only: accounts, plans, device docs
  python scripts/pro_admin.py grant <email|uid> [--plan pro] [--status active]

A command that changes something only says what it would do until you add --yes.
Deploy and rollback steps: docs/FIRESTORE_RULES.md.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = "burnmeter-4e5f3"
ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "firestore.rules"
CASES = ROOT / "tests" / "firestore_rules_cases.json"

_RULES_API = "https://firebaserules.googleapis.com/v1"
_FIRESTORE = "https://firestore.googleapis.com/v1"
_IDENTITY = "https://identitytoolkit.googleapis.com/v1"
_DOCS = "/databases/(default)/documents/"
_RELEASE = f"projects/{PROJECT}/releases/cloud.firestore"


def token() -> str:
    gcloud = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if not gcloud:
        sys.exit("gcloud not found: install the Google Cloud CLI, then `gcloud auth login`")
    out = subprocess.run([gcloud, "auth", "print-access-token"], capture_output=True,
                         text=True, timeout=120)
    if out.returncode != 0 or not out.stdout.strip():
        sys.exit("no gcloud access token: run `gcloud auth login` with an owner of "
                 f"{PROJECT}\n{out.stderr.strip()}")
    return out.stdout.strip()


def _call(url: str, tok: str, body=None, method: str = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    # x-goog-user-project bills the call to this project; user credentials need it.
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Authorization": "Bearer " + tok,
                                          "x-goog-user-project": PROJECT,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"error": {"message": raw[:500]}}


def _fail(what: str, st: int, r) -> None:
    msg = ((r or {}).get("error") or {}).get("message") if isinstance(r, dict) else r
    sys.exit(f"{what}: HTTP {st}: {msg}")


# ---------- rules tests ----------
def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _value(v, now: datetime):
    """Expand the case file's shorthands. The rules engine reads an RFC 3339 string as a
    timestamp, which is how a test hands it one."""
    if isinstance(v, dict):
        if "$t" in v:
            return _iso(now + timedelta(seconds=v["$t"]))
        if "$chars" in v:
            return "A" * v["$chars"]
        return {k: _value(x, now) for k, x in v.items()}
    if isinstance(v, list):
        return [_value(x, now) for x in v]
    return v


def build_test_suite(spec: dict) -> dict:
    """The Rules API TestSuite for the case file."""
    now = datetime.fromisoformat(spec["now"].replace("Z", "+00:00"))
    mocks = [{"function": "get",
              "args": [{"exactValue": _DOCS + "users/" + uid}],
              "result": {"value": None if a["users_doc"] is None else {"data": a["users_doc"]}}}
             for uid, a in spec["accounts"].items()]
    cases = []
    for c in spec["cases"]:
        req = {"path": _DOCS + c["path"], "method": c["op"], "time": _iso(now)}
        if c.get("as"):
            req["auth"] = {"uid": c["as"],
                           "token": {"email_verified": spec["accounts"][c["as"]]["verified"]}}
        if "data" in c:
            req["resource"] = {"data": _value(c["data"], now)}
        case = {"expectation": c["expect"], "request": req, "functionMocks": mocks}
        if "stored" in c:
            case["resource"] = {"data": _value(c["stored"], now)}
        cases.append(case)
    return {"testCases": cases}


def run_rules_tests(tok: str) -> list:
    """[{name, expect, ok, detail}] for every case; exits on a compile error."""
    spec = json.loads(CASES.read_text(encoding="utf-8"))
    body = {"source": {"files": [{"name": "firestore.rules",
                                  "content": RULES.read_text(encoding="utf-8")}]},
            "testSuite": build_test_suite(spec)}
    st, r = _call(f"{_RULES_API}/projects/{PROJECT}:test", tok, body)
    if st != 200:
        _fail("rules test", st, r)
    issues = r.get("issues") or []
    for i in issues:
        pos = i.get("sourcePosition") or {}
        print(f"{i.get('severity')}: line {pos.get('line')}:{pos.get('column')} {i.get('description')}")
    if any(i.get("severity") == "ERROR" for i in issues):
        sys.exit("firestore.rules does not compile")
    out = []
    for c, res in zip(spec["cases"], r.get("testResults") or []):
        ok = res.get("state") == "SUCCESS"
        detail = "" if ok else " ".join(filter(None, [
            "; ".join(res.get("debugMessages") or []),
            "at line {line}".format(**res["errorPosition"]) if res.get("errorPosition") else ""]))
        out.append({"name": c["name"], "expect": c["expect"], "ok": ok, "detail": detail})
    if len(out) != len(spec["cases"]):
        sys.exit(f"rules engine returned {len(out)} results for {len(spec['cases'])} cases")
    return out


def cmd_rules_test(tok: str) -> int:
    results = run_rules_tests(tok)
    for x in results:
        print(f"{'ok  ' if x['ok'] else 'FAIL'} {x['expect']:<5} {x['name']}"
              + (f"  [{x['detail']}]" if x["detail"] else ""))
    failed = sum(not x["ok"] for x in results)
    print(f"{len(results) - failed}/{len(results)} cases pass")
    return 1 if failed else 0


# ---------- deploy / rollback ----------
def _live_ruleset(tok: str) -> str:
    st, r = _call(f"{_RULES_API}/{_RELEASE}", tok)
    if st != 200:
        _fail("read release", st, r)
    return r["rulesetName"]


def cmd_rules_show(tok: str) -> int:
    name = _live_ruleset(tok)
    st, r = _call(f"{_RULES_API}/{name}", tok)
    if st != 200:
        _fail("read ruleset", st, r)
    print(f"live: {name} (created {r.get('createTime')})")
    for f in r["source"]["files"]:
        print(f"---- {f['name']}\n{f['content']}")
    return 0


def _point_release(tok: str, ruleset: str) -> None:
    st, r = _call(f"{_RULES_API}/{_RELEASE}", tok,
                  {"release": {"name": _RELEASE, "rulesetName": ruleset}}, method="PATCH")
    if st != 200:
        _fail("update release", st, r)


def cmd_rules_deploy(tok: str, yes: bool) -> int:
    if cmd_rules_test(tok):
        sys.exit("not deploying: a rules case fails")
    before = _live_ruleset(tok)
    print(f"live now: {before}\nrollback: python scripts/pro_admin.py rules-rollback "
          f"{before.rsplit('/', 1)[-1]} --yes")
    if not yes:
        print("dry run: add --yes to upload firestore.rules and make it live")
        return 0
    st, r = _call(f"{_RULES_API}/projects/{PROJECT}/rulesets", tok,
                  {"source": {"files": [{"name": "firestore.rules",
                                         "content": RULES.read_text(encoding="utf-8")}]}})
    if st != 200:
        _fail("create ruleset", st, r)
    _point_release(tok, r["name"])
    print(f"live: {_live_ruleset(tok)}")
    return 0


def cmd_rules_rollback(tok: str, ruleset: str, yes: bool) -> int:
    name = ruleset if "/" in ruleset else f"projects/{PROJECT}/rulesets/{ruleset}"
    st, r = _call(f"{_RULES_API}/{name}", tok)
    if st != 200:
        _fail("read ruleset", st, r)
    print(f"live now: {_live_ruleset(tok)}\nwould make live: {name} (created {r.get('createTime')})")
    if not yes:
        print("dry run: add --yes to switch")
        return 0
    _point_release(tok, name)
    print(f"live: {_live_ruleset(tok)}")
    return 0


# ---------- accounts ----------
def _select(tok: str, query: dict) -> list:
    st, r = _call(f"{_FIRESTORE}/projects/{PROJECT}/databases/(default)/documents:runQuery",
                  tok, {"structuredQuery": query})
    if st != 200:
        _fail("query", st, r)
    return [x["document"] for x in r if "document" in x]


def cmd_stats(tok: str) -> int:
    """Counts only; prints no email, uid or device name."""
    st, r = _call(f"{_IDENTITY}/projects/{PROJECT}/accounts:query", tok, {"returnUserInfo": False})
    print(f"Firebase Auth accounts: {r.get('recordsCount', '?') if st == 200 else f'HTTP {st}'}")
    plans: dict = {}
    for d in _select(tok, {"from": [{"collectionId": "users"}],
                           "select": {"fields": [{"fieldPath": "plan"}, {"fieldPath": "status"}]}}):
        f = d.get("fields") or {}
        key = ((f.get("plan") or {}).get("stringValue", "(no plan)") + "/"
               + (f.get("status") or {}).get("stringValue", "active"))
        plans[key] = plans.get(key, 0) + 1
    print(f"users docs by plan/status: {plans or 'none'}")
    # Only updated_at is fetched: device names (0.3.5 wrote them in plaintext) stay unread.
    devices = _select(tok, {"from": [{"collectionId": "devices", "allDescendants": True}],
                            "select": {"fields": [{"fieldPath": "updated_at"}]}})
    now = datetime.now(timezone.utc)
    ages = []
    for d in devices:
        t = ((d.get("fields") or {}).get("updated_at") or {}).get("timestampValue")
        if t:   # Firestore may send nanoseconds; datetime takes six digits
            t = re.sub(r"(\.\d{6})\d+", r"\1", t).replace("Z", "+00:00")
            ages.append(now - datetime.fromisoformat(t))
    owners = {d["name"].split("/devices/")[0] for d in devices}
    print(f"device docs: {len(devices)} across {len(owners)} accounts; "
          f"updated in the last day: {sum(a < timedelta(days=1) for a in ages)}, "
          f"last 30 days: {sum(a < timedelta(days=30) for a in ages)}")
    return 0


def _uid_for(tok: str, who: str) -> str:
    if "@" not in who:
        return who
    st, r = _call(f"{_IDENTITY}/projects/{PROJECT}/accounts:lookup", tok, {"email": [who]})
    users = (r or {}).get("users") or []
    if st != 200 or not users:
        sys.exit("no Firebase account with that email")
    return users[0]["localId"]


def cmd_grant(tok: str, who: str, plan: str, status: str, yes: bool) -> int:
    uid = _uid_for(tok, who)
    url = f"{_FIRESTORE}/projects/{PROJECT}/databases/(default)/documents/users/{uid}"
    st, r = _call(url, tok)
    f = (r.get("fields") or {}) if st == 200 else {}
    was = (f.get("plan") or {}).get("stringValue", "(none)"), (f.get("status") or {}).get("stringValue", "(none)")
    print(f"users/{uid}: plan {was[0]} -> {plan}, status {was[1]} -> {status}")
    if not yes:
        print("dry run: add --yes to write it")
        return 0
    st, r = _call(url + "?updateMask.fieldPaths=plan&updateMask.fieldPaths=status", tok,
                  {"fields": {"plan": {"stringValue": plan}, "status": {"stringValue": status}}},
                  method="PATCH")
    if st != 200:
        _fail("write users doc", st, r)
    print("written")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("rules-test")
    sub.add_parser("rules-show")
    sub.add_parser("rules-deploy").add_argument("--yes", action="store_true")
    rb = sub.add_parser("rules-rollback")
    rb.add_argument("ruleset")
    rb.add_argument("--yes", action="store_true")
    sub.add_parser("stats")
    g = sub.add_parser("grant")
    g.add_argument("who", help="account email or Firebase uid")
    g.add_argument("--plan", default="pro", choices=["pro", "free"])
    g.add_argument("--status", default="active", choices=["active", "suspended"])
    g.add_argument("--yes", action="store_true")
    a = ap.parse_args(argv)
    tok = token()
    if a.cmd == "rules-test":
        return cmd_rules_test(tok)
    if a.cmd == "rules-show":
        return cmd_rules_show(tok)
    if a.cmd == "rules-deploy":
        return cmd_rules_deploy(tok, a.yes)
    if a.cmd == "rules-rollback":
        return cmd_rules_rollback(tok, a.ruleset, a.yes)
    if a.cmd == "stats":
        return cmd_stats(tok)
    return cmd_grant(tok, a.who, a.plan, a.status, a.yes)


if __name__ == "__main__":
    sys.exit(main())
