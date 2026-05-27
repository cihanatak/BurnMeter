"""Generate synthetic Claude Code session JSONL files for testing.

This is a *test fixture generator*, not real data. It produces files that
match the on-disk schema we observe in real Claude Code sessions, so we
can exercise the parser, analytics, and server end to end without
requiring an actual Claude Code install.
"""
from __future__ import annotations

import argparse
import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODELS = [
    ("claude-sonnet-4-5-20250929",  0.65),
    ("claude-opus-4-5-20250901",    0.20),
    ("claude-haiku-4-5-20251001",   0.15),
]
TOOLS = ["Read", "Bash", "Edit", "Grep", "Glob", "Write", "WebFetch"]


def _pick(weighted):
    r = random.random()
    cum = 0
    for v, w in weighted:
        cum += w
        if r <= cum:
            return v
    return weighted[-1][0]


def _encoded_cwd(cwd: str) -> str:
    out = []
    for ch in cwd:
        out.append(ch if ch.isalnum() else "-")
    return "".join(out)


def make_session(out_root: Path, cwd: str, session_start: datetime,
                 turns: int, branch: str = "main"):
    enc = _encoded_cwd(cwd)
    project_dir = out_root / enc
    project_dir.mkdir(parents=True, exist_ok=True)
    session_id = str(uuid.uuid4())
    path = project_dir / f"{session_id}.jsonl"

    parent_uuid = None
    cur = session_start

    with path.open("w") as fh:
        # User opening turn (we still write it; parser ignores it)
        u_uuid = str(uuid.uuid4())
        fh.write(json.dumps({
            "parentUuid": parent_uuid,
            "uuid": u_uuid,
            "sessionId": session_id,
            "cwd": cwd,
            "gitBranch": branch,
            "version": "1.0.0",
            "timestamp": cur.isoformat().replace("+00:00", "Z"),
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "build a thing"}],
            },
        }) + "\n")
        parent_uuid = u_uuid

        for i in range(turns):
            cur = cur + timedelta(seconds=random.randint(15, 240))
            model = _pick(MODELS)
            # As a session grows, cache reads tend to dominate, but keep
            # the magnitude realistic for a fixture (~100K range per turn).
            cache_read = int(random.gauss(2000 + 200 * i, 600))
            cache_read = max(0, min(cache_read, 25_000))
            cache_create = random.choice([0, 0, 0, 600, 1800])
            input_tok = random.randint(120, 1200)
            output_tok = random.randint(150, 2200)

            content = []
            tools_in_turn = []
            if random.random() < 0.6:
                ntools = random.randint(1, 3)
                tools_in_turn = random.sample(TOOLS, ntools)
                for tn in tools_in_turn:
                    content.append({
                        "type": "tool_use",
                        "id": "toolu_" + uuid.uuid4().hex[:16],
                        "name": tn,
                        "input": {"x": 1},
                    })
            else:
                content.append({"type": "text", "text": "ok"})

            a_uuid = str(uuid.uuid4())
            fh.write(json.dumps({
                "parentUuid": parent_uuid,
                "uuid": a_uuid,
                "sessionId": session_id,
                "cwd": cwd,
                "gitBranch": branch,
                "version": "1.0.0",
                "timestamp": cur.isoformat().replace("+00:00", "Z"),
                "message": {
                    "id": "msg_" + uuid.uuid4().hex[:24],
                    "role": "assistant",
                    "model": model,
                    "content": content,
                    "usage": {
                        "input_tokens": input_tok,
                        "output_tokens": output_tok,
                        "cache_creation_input_tokens": cache_create,
                        "cache_read_input_tokens": cache_read,
                    },
                },
            }) + "\n")
            parent_uuid = a_uuid

    return path


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True, help="root projects/ dir to populate")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args(argv)

    random.seed(args.seed)
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)

    projects = [
        ("/Users/dev/payments-api",   "feature/refunds"),
        ("/Users/dev/web-frontend",   "main"),
        ("/Users/dev/ml-experiments", "main"),
    ]

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    for d in range(args.days):
        # day 0 = oldest; day = args.days - 1 == today
        day = (now - timedelta(days=args.days - 1 - d)).replace(hour=9)
        sessions_today = random.randint(2, 6)
        # inject one anomalous day mid-history
        is_anomaly = (d == args.days - 4)
        if is_anomaly:
            sessions_today *= 3
        for _ in range(sessions_today):
            cwd, branch = random.choice(projects)
            # Clamp session start strictly to the past.
            max_offset_hours = 8 if d == args.days - 1 else 10
            offset_hours = random.randint(0, max_offset_hours)
            start = day + timedelta(hours=offset_hours, minutes=random.randint(0, 59))
            if start > now - timedelta(minutes=30):
                start = now - timedelta(hours=random.randint(1, 4))
            turns = random.randint(8, 60)
            if is_anomaly:
                turns = random.randint(80, 140)
            make_session(root, cwd, start, turns, branch=branch)

    # Also include an *active* session that started ~25 minutes ago so the
    # current-window UI has something to show.
    now_session_start = datetime.now(timezone.utc) - timedelta(minutes=25)
    cwd, branch = random.choice(projects)
    make_session(root, cwd, now_session_start, turns=20, branch=branch)

    print(f"Wrote synthetic data to {root}")


if __name__ == "__main__":
    main()
