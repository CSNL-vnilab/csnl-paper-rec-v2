#!/usr/bin/env python3
"""
scripts/apply_feedback.py — gated csnl_paper_rec writes from a reviewed
_feedback_proposals.json: insert feedback_events + exclusion_rules (rules/04).

OPERATOR-RUN (after reviewing _feedback_proposals.json):
    ! python scripts/apply_feedback.py --run-id 20260519-1539

Idempotent: feedback_events.idem_key UNIQUE, exclusion_rules
UNIQUE(unit_id, excluded_term) — both use ON CONFLICT DO NOTHING.
No Slack call; no recommendation re-send. csnl_research is never touched.
"""
import argparse
import json
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from _db import load_env, exec_many, query_json, ledger_schema  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent
_KST = timezone(timedelta(hours=9))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args()
    load_env()
    s = ledger_schema()
    fp = _ROOT / "state" / "runs" / a.run_id / "_feedback_proposals.json"
    if not fp.exists():
        print(f"ERROR: {fp.relative_to(_ROOT)} not found "
              "(run scripts/classify_feedback.py first).")
        return 1
    props = json.loads(fp.read_text()).get("proposals", [])
    if not props:
        print("[apply_feedback] no proposals — nothing to apply.")
        return 0

    now = datetime.now(_KST).isoformat(timespec="seconds")
    # feedback_events
    fe_rows = []
    for p in props:
        fe = p.get("feedback_event") or {}
        fe_rows.append((
            str(uuid.uuid4()), now, fe.get("recommendation_doi", ""),
            p.get("unit_id", ""), p.get("member_init", ""),
            p.get("signal", "thread_reply"),
            fe.get("payload_json", ""), fe.get("idem_key", "")))
    n_fe = exec_many(
        f"INSERT INTO {s}.feedback_events (id,occurred_at,recommendation_doi,"
        "unit_id,member_init,signal,payload_json,idem_key) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", fe_rows)

    # exclusion_rules (only for proposals with non-null .exclusion)
    ex_rows = []
    for p in props:
        ex = p.get("exclusion")
        if not ex or not ex.get("excluded_term"):
            continue
        ex_rows.append((p.get("unit_id", ""), p.get("member_init", ""),
                        ex["excluded_term"],
                        ex.get("reason", "feedback"), now,
                        ex.get("source", "feedback")))
    n_ex = exec_many(
        f"INSERT INTO {s}.exclusion_rules "
        "(unit_id,member_init,excluded_term,reason,declared_at,source) "
        "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", ex_rows)

    # post-state counts
    cnt_fe = query_json(f"SELECT count(*) AS n FROM {s}.feedback_events")[0]["n"]
    cnt_ex = query_json(f"SELECT count(*) AS n FROM {s}.exclusion_rules")[0]["n"]
    print(f"[apply_feedback] inserted: feedback_events={n_fe}  exclusion_rules={n_ex}")
    print(f"  ledger now: feedback_events={cnt_fe}  exclusion_rules={cnt_ex}")
    print("  dedup will now reflect the new exclusions (rules/04).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
