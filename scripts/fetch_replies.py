#!/usr/bin/env python3
"""
scripts/fetch_replies.py — pull researcher DM replies for a paper-rec run.

Operator-run (gated; uses SLACK_BOT_TOKEN from .env; reads csnl_paper_rec
to find each recipient's sent ts):
    ! python scripts/fetch_replies.py --run-id 20260519-1539

Output: state/runs/<RID>/_replies.json
  { run_id, fetched_at, replies:[ { member_init, dm_channel, ts, user,
    text, since_send_ts } ] }

NO inference, NO classification — that's classify_feedback.py / the
feedback-analyst agent. Just a faithful pull of researcher messages from
each DM since the recommendation send timestamp.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from _db import load_env, query_json, ledger_schema  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent


def slack_history(token: str, channel: str, oldest: str, limit: int = 50) -> list[dict]:
    url = "https://slack.com/api/conversations.history"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"channel": channel, "oldest": oldest, "limit": limit, "inclusive": False}
    r = requests.get(url, headers=headers, params=params, timeout=20)
    r.raise_for_status()
    d = r.json()
    if not d.get("ok"):
        raise RuntimeError(f"Slack error on {channel}: {d.get('error')}")
    return d.get("messages", [])


def slack_thread(token: str, channel: str, parent_ts: str, limit: int = 50) -> list[dict]:
    """Thread replies under a parent message (the bot's recommendation)."""
    url = "https://slack.com/api/conversations.replies"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"channel": channel, "ts": parent_ts, "limit": limit}
    r = requests.get(url, headers=headers, params=params, timeout=20)
    r.raise_for_status()
    d = r.json()
    if not d.get("ok"):
        # Some workspaces return 'thread_not_found' if no replies — treat as empty
        if d.get("error") in ("thread_not_found",):
            return []
        raise RuntimeError(f"Slack thread error on {channel}: {d.get('error')}")
    msgs = d.get("messages", []) or []
    # First msg is the parent itself; drop it
    return [m for m in msgs if m.get("ts") != parent_ts]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--limit", type=int, default=50)
    a = ap.parse_args()
    rid = a.run_id
    rd = _ROOT / "state" / "runs" / rid

    load_env()
    token = os.environ.get("SLACK_BOT_TOKEN") or ""
    if not token:
        print("ERROR: SLACK_BOT_TOKEN not set in .env"); return 1

    s = ledger_schema()
    sent = query_json(
        f"SELECT member_init, channel_id, slack_ts "
        f"FROM {s}.paper_recommendations WHERE run_id='{rid}'")
    if not sent:
        print(f"ERROR: no sent rows for run_id={rid} in {s}.paper_recommendations")
        return 1

    drafts = json.loads((rd / "08_dm_drafts.json").read_text()).get("drafts", [])
    dm_by_member = {d["member_init"]: d["dm_channel"] for d in drafts}

    replies = []
    for r in sent:
        member = r["member_init"]
        ts = str(r["slack_ts"])
        dm = dm_by_member.get(member) or r["channel_id"]
        if not dm:
            print(f"  WARN: no DM channel for {member}; skipping")
            continue
        msgs = []
        try:
            # top-level DM messages newer than the recommendation send
            msgs.extend(slack_history(token, dm, ts, limit=a.limit))
        except RuntimeError as e:
            print(f"  WARN [{member} {dm} history]: {e}")
        try:
            # thread replies under the recommendation message itself
            msgs.extend(slack_thread(token, dm, ts, limit=a.limit))
        except RuntimeError as e:
            print(f"  WARN [{member} {dm} thread]: {e}")
        seen_ts = set()
        for m in msgs:
            # researcher reply = not the bot's own send
            if m.get("bot_id") or m.get("subtype") == "bot_message":
                continue
            if not m.get("text"):
                continue
            mts = m.get("ts")
            if mts in seen_ts:
                continue
            seen_ts.add(mts)
            replies.append({
                "member_init": member, "dm_channel": dm,
                "since_send_ts": ts, "ts": mts,
                "user": m.get("user"), "text": m["text"],
                "thread_ts": m.get("thread_ts"),
            })
        print(f"  {member} {dm}: "
              f"{sum(1 for x in replies if x['member_init']==member)} reply(ies)")

    from datetime import datetime, timezone, timedelta
    out = {"run_id": rid,
           "fetched_at": datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds"),
           "replies": replies}
    dest = rd / "_replies.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[fetch_replies] {dest}  total replies: {len(replies)}  "
          f"recipients: {len(sent)}  non-repliers: "
          f"{len(sent) - len({x['member_init'] for x in replies})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
