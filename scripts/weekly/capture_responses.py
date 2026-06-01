#!/usr/bin/env python3
"""
scripts/weekly/capture_responses.py — read the 읽음 checkboxes on "이번 주 논문
추천" and reconcile the board into the ledger (first step of the Wednesday
routine).

Reconcile-based + idempotent. It looks at every digest row that still has a
Notion page and decides per state:

  • NULL row, page live, 읽음 ☑ (read)  → UPSERT archive_responses already_read
    (ON CONFLICT DO NOTHING = 무손상) → mark digest already_read → archive the
    Notion page (it leaves the board, freeing a slot for build to refill 1).
  • NULL row, page live, ☐            → leave (rolls over to next week).
  • already_read row, page still live  → LINGERING (a prior archive failed) →
    archive the page now.
  • NULL row, page NOT live            → ORPHAN (page externally archived/
    deleted): fetch it; if it was checked, reconcile to already_read; if it is
    gone (404), null its page id so send re-creates it (keeps the board full).

Order within a read is mark-then-archive, so a failed archive leaves a
self-consistent (already_read, live) state that the LINGERING branch fixes on
the next run — build is therefore never fooled by an archived-but-still-active
slot. archive_responses is additive-only (truth never overwritten).

Belief update: a researcher's cumulative archive_responses count crossing a
multiple of 10 is FLAGGED in archive_weekly_belief_due (no LLM). The "논문 리스트"
history DB is owned by mirror_history.py (run later in the routine).

Boundary: writes archive_weekly_digests + archive_responses (additive); archives
Notion pages. Operator/cron-run; --apply gates writes.

Usage:
    python3 scripts/weekly/capture_responses.py            # dry-run
    python3 scripts/weekly/capture_responses.py --apply     # write
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _notion as N            # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
from _db import load_env, query_json, exec_many, ledger_schema  # noqa: E402


def _page_rows(sch: str) -> list[dict]:
    """Digest rows that still carry a Notion page id (response_choice is only
    ever NULL or 'already_read' in the rolling model)."""
    return query_json(f"""
        SELECT digest_id, week_iso, researcher_id, canonical_id,
               notion_page_id, response_choice
          FROM {sch}.archive_weekly_digests
         WHERE notion_page_id IS NOT NULL
         ORDER BY researcher_id, week_iso
    """)


def _read_by_page(db_id: str, read_prop: str) -> dict[str, bool]:
    """{live_page_id: 읽음 checkbox} — query_database lists NON-archived pages."""
    return {p.get("id"): N.read_checkbox(p, read_prop)
            for p in N.query_database(db_id)}


def _existing_responses(sch: str) -> dict[tuple, str]:
    rows = query_json(f"SELECT researcher_id, canonical_id, choice "
                      f"FROM {sch}.archive_responses")
    return {(r["researcher_id"], r["canonical_id"]): r["choice"] for r in rows}


def _response_totals(sch: str) -> dict[str, int]:
    rows = query_json(f"SELECT researcher_id, count(*) n FROM {sch}.archive_responses "
                      f"GROUP BY researcher_id")
    return {r["researcher_id"]: int(r["n"]) for r in rows}


def _mark_read(sch: str, c: dict, existing: dict) -> bool:
    """Truth row FIRST (already_read, DO NOTHING), then mark the digest consumed.
    Returns True if a NEW archive_responses row was inserted."""
    is_new = (c["researcher_id"], c["canonical_id"]) not in existing
    if is_new:
        detail = {"source": "weekly_notion_checkbox", "week": c["week_iso"],
                  "notion_page_id": c["notion_page_id"]}
        exec_many(
            f"INSERT INTO {sch}.archive_responses "
            f"(researcher_id, canonical_id, session_id, choice, choice_detail, "
            f"responded_at) VALUES (%s,%s,%s,'already_read',%s::jsonb, now()::text) "
            f"ON CONFLICT (researcher_id, canonical_id) DO NOTHING",
            [(c["researcher_id"], c["canonical_id"], f"weekly-{c['week_iso']}",
              json.dumps(detail, ensure_ascii=False))],
        )
    exec_many(
        f"UPDATE {sch}.archive_weekly_digests "
        f"SET response_choice='already_read', response_at=now() WHERE digest_id=%s",
        [(c["digest_id"],)],
    )
    return is_new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    load_env()
    sch = ledger_schema()
    db_id = N.digest_db_id()
    props = N.digest_props()
    read_prop = props["read"]

    # Abort on schema drift — otherwise read_checkbox returns False for every
    # page and we would silently capture nothing (codex finding).
    ok, problems = N.validate_digest_db(db_id)
    if not ok:
        print("[capture] ABORT — digest DB schema invalid:", file=sys.stderr)
        for p in problems:
            print(f"    - {p}", file=sys.stderr)
        print("    fix: python3 scripts/weekly/provision_notion.py --db digest --apply",
              file=sys.stderr)
        return 2

    rows = _page_rows(sch)
    if not rows:
        print("[capture] no digest rows with a Notion page. Nothing to check.")
        return 0
    live = _read_by_page(db_id, read_prop)
    existing = _existing_responses(sch)

    reads, lingering, orphans = [], [], []
    for r in rows:
        pid = r["notion_page_id"]
        rc = r["response_choice"]
        if pid in live:
            if rc == "already_read":
                lingering.append(r)             # read but page still on the board
            elif rc is None and live[pid]:
                reads.append(r)                 # newly checked → read
            # NULL + unchecked → rolls over
        else:
            if rc is None:
                orphans.append(r)               # page externally archived/deleted

    print(f"[capture] board pages={len(live)}  read(checked)={len(reads)}  "
          f"lingering={len(lingering)}  orphan={len(orphans)}")
    for c in reads:
        pre = (f"  ⚠ already in archive_responses as '{existing.get((c['researcher_id'], c['canonical_id']))}'"
               f" — PRESERVED (무손상)"
               if (c["researcher_id"], c["canonical_id"]) in existing else "")
        print(f"[capture] {c['researcher_id']} {c['canonical_id'][:10]} ☑ 읽음 → already_read{pre}")

    if not args.apply:
        n_new = sum(1 for c in reads
                    if (c["researcher_id"], c["canonical_id"]) not in existing)
        print(f"[capture] dry-run only. Would: read {len(reads)} "
              f"({n_new} new archive_responses), archive {len(reads)+len(lingering)} "
              f"page(s), reconcile {len(orphans)} orphan(s). Re-run with --apply.")
        return 0

    affected_rids: set[str] = set()
    inserted = preserved = archived = failed = 0

    # 1. newly-read papers: mark, then archive the page.
    for c in reads:
        is_new = _mark_read(sch, c, existing)
        if is_new:
            inserted += 1
            affected_rids.add(c["researcher_id"])
        else:
            preserved += 1
        try:
            N._request("PATCH", f"/pages/{c['notion_page_id']}",
                       json_body={"archived": True})
            archived += 1
        except Exception as e:
            failed += 1
            print(f"[capture] archive FAIL {c['researcher_id']}/{c['canonical_id'][:10]} "
                  f"— digest already marked read; LINGERING branch retries next run: "
                  f"{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)

    # 2. lingering (already_read but page still live): archive now.
    for c in lingering:
        try:
            N._request("PATCH", f"/pages/{c['notion_page_id']}",
                       json_body={"archived": True})
            archived += 1
        except Exception as e:
            failed += 1
            print(f"[capture] lingering archive FAIL {c['researcher_id']}/"
                  f"{c['canonical_id'][:10]}: {type(e).__name__}: {str(e)[:120]}",
                  file=sys.stderr)

    # 3. orphans (NULL row, page not live): fetch + reconcile.
    for c in orphans:
        try:
            page = N.retrieve_page(c["notion_page_id"])
        except Exception as e:
            # Page is gone (deleted) — free the slot so send re-creates it.
            print(f"[capture] orphan {c['researcher_id']}/{c['canonical_id'][:10]}: "
                  f"page not retrievable ({type(e).__name__}) → clearing page id "
                  f"for re-send.", file=sys.stderr)
            exec_many(f"UPDATE {sch}.archive_weekly_digests "
                      f"SET notion_page_id=NULL WHERE digest_id=%s", [(c["digest_id"],)])
            continue
        if N.read_checkbox(page, read_prop):
            is_new = _mark_read(sch, c, existing)   # page already archived; just mark
            if is_new:
                inserted += 1
                affected_rids.add(c["researcher_id"])
            else:
                preserved += 1
            print(f"[capture] orphan reconciled (read): {c['researcher_id']}/"
                  f"{c['canonical_id'][:10]}")
        else:
            print(f"[capture] ⚠ orphan {c['researcher_id']}/{c['canonical_id'][:10]} "
                  f"page archived but unchecked — left active for review.",
                  file=sys.stderr)

    print(f"[capture] applied — read_new={inserted} preserved={preserved} "
          f"pages_archived={archived} failed={failed}")

    if affected_rids:
        totals = _response_totals(sch)
        due = sorted(r for r in affected_rids
                     if totals.get(r, 0) > 0 and totals.get(r, 0) % 10 == 0)
        for rid in due:
            exec_many(
                f"INSERT INTO {sch}.archive_weekly_belief_due "
                f"(researcher_id, at_response_count) VALUES (%s,%s) "
                f"ON CONFLICT (researcher_id, at_response_count) DO NOTHING",
                [(rid, totals[rid])],
            )
        if due:
            print(f"[capture] belief_update_due persisted to "
                  f"archive_weekly_belief_due: {due}")
            print("[capture]   → operator: run the belief update + "
                  "`build_researcher_queue.py --apply`.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
