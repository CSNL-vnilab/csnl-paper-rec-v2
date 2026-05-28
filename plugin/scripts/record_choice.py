#!/usr/bin/env python3
"""
plugin/scripts/record_choice.py — persist one MCQ answer.

Usage:
    python plugin/scripts/record_choice.py --init <init> --session <sid> \\
        --canonical-id <cid> --choice <save_later|not_relevant|already_read|skipped> \\
        [--detail-json '@detail.json' | '<inline json>']

UPSERTs archive_responses (researcher × paper) and updates the running
counters on archive_interview_sessions (papers_seen, choice_counts JSON).

`tell_me_more` was retired 2026-05-28 (the 4th MCQ option). Existing
historical rows with that value should be migrated away (or kept as
deprecated read-only) via state/migrations/2026-05-28_drop_tell_me_more.sql.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _pdb import load_env, exec_sql, query, schema  # noqa: E402

_VALID = ("save_later", "not_relevant", "already_read", "skipped")


def _read_arg(v: str) -> str:
    if v and v.startswith("@"):
        return Path(v[1:]).read_text(encoding="utf-8")
    return v or "{}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--canonical-id", required=True)
    ap.add_argument("--choice", required=True, choices=_VALID)
    ap.add_argument("--detail-json", default="{}")
    ap.add_argument("--allow-unstaged", action="store_true",
                    help="Skip the staged-paper verification (operator "
                         "back-fill only — the interview skill never sets "
                         "this).")
    args = ap.parse_args()

    args.init = args.init.strip().upper()
    detail = json.loads(_read_arg(args.detail_json))
    load_env()
    sch = schema()

    # 0. Verify the canonical_id was actually issued by pick_next.py for
    #    this session. If `current_issue` is null or points to a different
    #    paper, reject — we don't want any CLI-fabricated response.
    if not args.allow_unstaged:
        staged = query(
            f"SELECT current_issue FROM {sch}.archive_interview_sessions "
            f"WHERE session_id = %s AND researcher_id = %s",
            (args.session, args.init),
        )
        if not staged:
            raise RuntimeError(
                f"No session found for (session_id={args.session!r}, "
                f"init={args.init!r}). Call profile_show.py first."
            )
        ci = staged[0]["current_issue"]
        if isinstance(ci, str):
            try:
                ci = json.loads(ci)
            except Exception:
                ci = None
        if not ci or not isinstance(ci, dict) or ci.get("canonical_id") != args.canonical_id:
            raise RuntimeError(
                "Refusing to record: this canonical_id was not the most "
                "recent pick_next.py issue for this session. Call "
                "pick_next.py --session <sid> first, then record the "
                "researcher's choice for that paper."
            )

    # 1. UPSERT response.
    exec_sql(
        f"""
        INSERT INTO {sch}.archive_responses
          (researcher_id, canonical_id, session_id, choice, choice_detail, responded_at)
        VALUES (%s, %s, %s, %s, %s::jsonb, now()::text)
        ON CONFLICT (researcher_id, canonical_id) DO UPDATE SET
          session_id    = EXCLUDED.session_id,
          choice        = EXCLUDED.choice,
          choice_detail = EXCLUDED.choice_detail,
          responded_at  = EXCLUDED.responded_at;
        """,
        (args.init, args.canonical_id, args.session, args.choice,
         json.dumps(detail, ensure_ascii=False)),
    )

    # 2. Recompute counters from the canonical archive_responses table —
    #    that way re-running the script never double-counts.
    counts = query(
        f"SELECT choice, COUNT(*) AS n FROM {sch}.archive_responses "
        f"WHERE researcher_id = %s GROUP BY choice",
        (args.init,),
    )
    breakdown = {row["choice"]: int(row["n"]) for row in counts}
    total = sum(breakdown.values())

    # Clear the staged issue so a stale current_issue can never be reused;
    # also bump papers_seen + the breakdown counter.
    exec_sql(
        f"""
        UPDATE {sch}.archive_interview_sessions
           SET papers_seen    = %s,
               choice_counts  = %s::jsonb,
               current_issue  = NULL,
               last_active_at = now()::text
         WHERE session_id = %s
        """,
        (total, json.dumps(breakdown, ensure_ascii=False), args.session),
    )

    # 3. Report the new state + a 10-step trigger hint for the skill.
    print(json.dumps({
        "ok": True,
        "papers_seen": total,
        "breakdown":   breakdown,
        "meta_review_due": (total % 10 == 0 and total > 0),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
