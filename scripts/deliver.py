#!/usr/bin/env python3
"""
scripts/deliver.py — csnl-paper-rec-v2 gated delivery (PostgreSQL ledger).

DEFAULT: --dry-run. Prints the full preview; makes ZERO Slack calls and ZERO
DB calls. The dry-run packet is the deliverable of the build session.

Real send requires ALL of:
  1. --send
  2. --operator-approved
  3. state/.APPROVED_<RUN_ID>  (operator-created token; never by the agent)
No env var overrides this gate (rules/05_delivery.md; DECISIONS-2026-05-18).

Tone-lint: parses the BANNED_TERMS fenced block from rules/01_tone.md at
runtime; case-insensitive substring; any hit ABORTS that unit. Refuses to
send if the rules file / block is missing.

Sequential: one unit at a time, ≥7 s gap, Slack ok+permalink verified, then
Postgres ledger rows written (paper_recommendations + recommendation_messages
in $CPR_LEDGER_SCHEMA). Ported from the predecessor sqlite deliver.py.

    ! python scripts/deliver.py --run-id YYYYMMDD-HHMM                     # dry-run
    ! python scripts/deliver.py --run-id YYYYMMDD-HHMM --send --operator-approved
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPO_ROOT / "state"
RULES_DIR = REPO_ROOT / "rules"
KST = ZoneInfo("Asia/Seoul")
BANNED_FENCE = "BANNED_TERMS"
MIN_GAP_SECONDS = 7
MAX_RETRIES = 5


def kst_iso() -> str:
    return datetime.datetime.now(tz=KST).isoformat(timespec="seconds")


def run_dir(rid: str) -> Path:
    return STATE_DIR / "runs" / rid


def approval_token(rid: str) -> Path:
    return STATE_DIR / f".APPROVED_{rid}"


# ---------------------------------------------------------------------------
# Tone lint (mirrors draft-reviewer; backstop at the gate)
# ---------------------------------------------------------------------------

def load_banned_terms() -> list[str] | None:
    f = RULES_DIR / "01_tone.md"
    if not f.exists():
        return None
    m = re.search(r"```" + re.escape(BANNED_FENCE) + r"\s*\n(.*?)\n```",
                  f.read_text(encoding="utf-8"), re.DOTALL)
    if not m:
        return None
    terms = [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]
    return terms or None


def tone_lint(text: str, banned: list[str]) -> list[str]:
    low = (text or "").lower()
    return [t for t in banned if t.lower() in low]


def cap_check(text: str) -> list[str]:
    """paradigm / framework ≤1 each (rules/01)."""
    hits = []
    for w in ("paradigm", "framework"):
        if len(re.findall(w, text or "", re.I)) > 1:
            hits.append(f"{w}>1")
    return hits


# ---------------------------------------------------------------------------
# researchers.yaml — channel/DM resolution + drift note
# ---------------------------------------------------------------------------

def load_researchers() -> dict:
    import yaml
    return yaml.safe_load((REPO_ROOT / "config" / "researchers.yaml").read_text())


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

def slack_post(token: str, channel: str, text: str) -> dict:
    import requests
    url = "https://slack.com/api/chat.postMessage"
    headers = {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json; charset=utf-8"}
    for attempt in range(1, MAX_RETRIES + 1):
        r = requests.post(url, json={"channel": channel, "text": text},
                          headers=headers, timeout=20)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 2 ** attempt))
            print(f"    [429] back-off {wait}s ({attempt}/{MAX_RETRIES})")
            time.sleep(wait)
            continue
        r.raise_for_status()
        d = r.json()
        if not d.get("ok"):
            raise RuntimeError(f"Slack error: {d.get('error')}")
        return d
    raise RuntimeError(f"Slack 429 persisted after {MAX_RETRIES} retries")


def slack_permalink(token: str, channel: str, ts: str) -> str:
    import requests
    try:
        r = requests.get("https://slack.com/api/chat.getPermalink",
                         headers={"Authorization": f"Bearer {token}"},
                         params={"channel": channel, "message_ts": ts}, timeout=10)
        d = r.json()
        if d.get("ok"):
            return d.get("permalink", "")
    except Exception as e:  # noqa: BLE001
        print(f"    WARNING: permalink fetch failed: {e}")
    return ""


# ---------------------------------------------------------------------------
# Ledger (Postgres; real send only)
# ---------------------------------------------------------------------------

def write_ledger(draft: dict, rid: str, slack_ts: str, posted_at: str) -> None:
    sys.path.insert(0, str(REPO_ROOT / "pipeline"))
    from _db import load_env, exec_many, ledger_schema
    load_env()
    schema = ledger_schema()
    rec = [(rid, draft["unit_id"], mi, ch, slack_ts, draft["paper_doi"],
            draft["paper_title"], draft["paper_date"], draft["tier"], posted_at)
           for mi, ch in zip(draft["dm_inits"], draft["channel_ids"])]
    exec_many(
        f"INSERT INTO {schema}.paper_recommendations "
        "(run_id,unit_id,member_init,channel_id,slack_ts,paper_doi,"
        "paper_title,paper_date,tier,posted_at) VALUES "
        "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", rec)
    msg = [(str(uuid.uuid4()), ch, slack_ts, draft["unit_id"], draft["paper_doi"],
            posted_at, json.dumps({"run_id": rid, "tier": draft["tier"],
                                   "paper_title": draft["paper_title"]}))
           for ch in draft["channel_ids"]]
    exec_many(
        f"INSERT INTO {schema}.recommendation_messages "
        "(id,channel_id,message_ts,unit_id,paper_doi,posted_at,context_json) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", msg)


# ---------------------------------------------------------------------------
# Dry-run preview
# ---------------------------------------------------------------------------

def sep(c="─", w=70):
    print(c * w)


def dry_run(drafts: list[dict], rid: str, banned: list[str], researchers: dict) -> None:
    rmap = researchers.get("researchers", {})
    print()
    sep("═")
    print(f"  DRY-RUN PREVIEW  |  RUN_ID = {rid}  |  units = {len(drafts)}")
    print("  NOTHING IS SENT. NO DB WRITE. Gate: --send --operator-approved"
          f" + state/.APPROVED_{rid}")
    sep("═")
    all_ok = True
    for i, d in enumerate(drafts, 1):
        dm_chans = [rmap.get(mi, {}).get("dm_channel", "?") for mi in d["dm_inits"]]
        print()
        sep()
        print(f"  Unit {i}/{len(drafts)}: {d['unit_id']}  (tier: {d['tier']})")
        sep()
        print(f"  Channels  : {d['channel_ids']}")
        print(f"  DM inits  : {d['dm_inits']}  → DM channels {dm_chans}")
        print(f"  Paper     : {d['paper_title']}")
        print(f"  DOI/date  : {d['paper_doi']} / {d['paper_date']}")
        print("\n  ── channel_text ──")
        for ln in d["channel_text"].split("\n"):
            print(f"  | {ln}")
        print("  ── dm_ping_text ──")
        for ln in d["dm_ping_text"].replace(
                "{permalink}", f"https://slack.com/archives/{d['channel_ids'][0]}/pXXXX").split("\n"):
            print(f"  | {ln}")
        hits = tone_lint(d["channel_text"], banned) + tone_lint(d["dm_ping_text"], banned)
        caps = cap_check(d["channel_text"])
        ncj = len(re.sub(r"\s", "", d["channel_text"]))
        if hits or caps:
            all_ok = False
            print(f"\n  Tone lint: FAIL — banned={hits} caps={caps} "
                  f"→ unit ABORTED on real send")
        else:
            print(f"\n  Tone lint: OK (no banned terms; paradigm/framework ≤1; "
                  f"channel chars≈{ncj})")
        print("  Ledger rows that WOULD be written (real send only):")
        for mi, ch in zip(d["dm_inits"], d["channel_ids"]):
            print(f"    paper_recommendations: unit={d['unit_id']} member={mi} "
                  f"channel={ch} doi={d['paper_doi']} tier={d['tier']}")
        print(f"    recommendation_messages: {len(d['channel_ids'])} row(s)")
    print()
    sep("═")
    print(f"  END PREVIEW — tone lint {'OK for ALL units' if all_ok else 'FAILED (see above)'}")
    print("  To send (operator only):")
    print(f"    1. touch state/.APPROVED_{rid}")
    print(f"    2. python scripts/deliver.py --run-id {rid} --send --operator-approved")
    sep("═")


# ---------------------------------------------------------------------------
# Real send (gated, sequential)
# ---------------------------------------------------------------------------

def real_send(drafts: list[dict], rid: str, banned: list[str],
              researchers: dict, token: str) -> None:
    rmap = researchers.get("researchers", {})
    print(f"\n{'='*70}\n  REAL SEND  |  RUN_ID={rid}  |  {len(drafts)} unit(s); "
          f"≥{MIN_GAP_SECONDS}s gap\n{'='*70}")
    sent = skipped = 0
    for i, d in enumerate(drafts, 1):
        uid = d["unit_id"]
        print(f"\n  [{i}/{len(drafts)}] {uid}")
        hits = tone_lint(d["channel_text"], banned) + tone_lint(d["dm_ping_text"], banned)
        if hits or cap_check(d["channel_text"]):
            print(f"  [{uid}] TONE LINT FAIL {hits} — unit ABORTED")
            skipped += 1
            continue
        try:
            posted_at = kst_iso()
            first_ts = ""
            for ch in d["channel_ids"]:
                print(f"  [{uid}] post → {ch}")
                resp = slack_post(token, ch, d["channel_text"])
                ts = resp.get("ts", "")
                first_ts = first_ts or ts
                pl = slack_permalink(token, ch, ts)
                print(f"  [{uid}] ok ts={ts} permalink={pl}")
                # DM ping to the member whose channel this is
                for mi, mch in zip(d["dm_inits"], d["channel_ids"]):
                    if mch == ch:
                        dm = rmap.get(mi, {}).get("dm_channel")
                        if dm:
                            slack_post(token, dm, d["dm_ping_text"].replace("{permalink}", pl))
                            print(f"  [{uid}] DM ping → {mi} ({dm})")
            write_ledger(d, rid, first_ts, posted_at)
            print(f"  [{uid}] ledger written")
            sent += 1
        except RuntimeError as e:
            print(f"  [{uid}] send FAILED: {e} — unit skipped")
            skipped += 1
        if i < len(drafts):
            print(f"  waiting {MIN_GAP_SECONDS}s…")
            time.sleep(MIN_GAP_SECONDS)
    print(f"\n{'='*70}\n  done: {sent} sent, {skipped} skipped\n{'='*70}")


def main() -> int:
    ap = argparse.ArgumentParser(description="csnl-paper-rec-v2 gated delivery")
    ap.add_argument("--run-id", required=True, metavar="YYYYMMDD-HHMM")
    ap.add_argument("--send", action="store_true", default=False)
    ap.add_argument("--operator-approved", action="store_true", default=False)
    args = ap.parse_args()
    rid = args.run_id
    dry = not args.send

    if args.send:
        if not args.operator_approved:
            print("ERROR: --send requires --operator-approved.")
            return 1
        tok = approval_token(rid)
        if not tok.exists():
            print(f"ERROR: approval token missing: {tok.relative_to(REPO_ROOT)}")
            print(f"Operator must: touch {tok.relative_to(REPO_ROOT)}")
            return 1

    banned = load_banned_terms()
    if banned is None:
        if not dry:
            print("ERROR: rules/01_tone.md BANNED_TERMS block missing — cannot send.")
            return 1
        print("  WARNING: BANNED_TERMS block missing — lint skipped in preview.")
        banned = []

    df = run_dir(rid) / "07_drafts.json"
    if not df.exists():
        print(f"ERROR: {df.relative_to(REPO_ROOT)} not found. Run the scout +"
              " producer-reviewer stages first.")
        return 1
    drafts = json.loads(df.read_text()).get("drafts", [])
    if not drafts:
        print("No drafts. Nothing to deliver.")
        return 0
    req = ("unit_id", "channel_ids", "dm_inits", "channel_text",
           "dm_ping_text", "paper_doi", "paper_title", "paper_date", "tier")
    for d in drafts:
        for k in req:
            if not d.get(k):
                print(f"ERROR: unit {d.get('unit_id','?')} missing '{k}'.")
                return 1
        if "{permalink}" not in d["dm_ping_text"]:
            print(f"ERROR: unit {d['unit_id']} dm_ping_text missing {{permalink}}.")
            return 1

    researchers = load_researchers()
    if dry:
        dry_run(drafts, rid, banned, researchers)
        return 0
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        from _db import load_env  # noqa
        sys.path.insert(0, str(REPO_ROOT / "pipeline"))
        load_env()
        token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        print("ERROR: SLACK_BOT_TOKEN not set (env or .env).")
        return 1
    real_send(drafts, rid, banned, researchers, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
