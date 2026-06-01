#!/usr/bin/env python3
"""
scripts/weekly/capture_responses.py — poll the Notion digest DB for Status
changes and fold them back into the ledger.

Design §9. For every pending digest row (response_choice IS NULL, sent within
the cooldown window, page id present) it reads the Notion Status, and when the
researcher has picked one of the response options it:
  (a) UPDATEs archive_weekly_digests.response_choice / response_at, and
  (b) UPSERTs archive_responses with ON CONFLICT (researcher_id, canonical_id)
      DO NOTHING — the 무손상 contract: a pre-existing answer (from the
      interview or an earlier capture) is preserved, never overwritten, with a
      warn log, and
  (c) best-effort mirrors the answered paper into the "논문 리스트" history DB
      (only when NOTION_HISTORY_DB_ID is set).

Belief update: when a researcher's cumulative archive_responses count crosses a
multiple of 10 it is FLAGGED (belief_update_due) — never auto-run. The belief
updater is an LLM agent and the unattended path is LLM-free (DECISIONS-v3); the
operator runs the update + the next queue rebuild, which is what feeds the
learned preferences into the following week's digest (§9).

Efficiency: one paginated query of the digest DB (status for every page) rather
than one GET per pending row. Boundary: writes archive_weekly_digests +
archive_responses (additive, never overwrite) + optional history DB rows.
Operator-run; --apply gates all writes.

Usage:
    python3 scripts/weekly/capture_responses.py            # dry-run
    python3 scripts/weekly/capture_responses.py --apply     # write
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _notion as N            # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
from _db import load_env, query_json, exec_many, ledger_schema  # noqa: E402

KST = timezone(timedelta(hours=9))
COOLDOWN_WEEKS = 8


def _pending_rows(sch: str) -> list[dict]:
    return query_json(f"""
        SELECT digest_id, week_iso, researcher_id, canonical_id,
               notion_page_id
          FROM {sch}.archive_weekly_digests
         WHERE response_choice IS NULL
           AND notion_page_id IS NOT NULL
           AND sent_at > now() - interval '{int(COOLDOWN_WEEKS)} weeks'
         ORDER BY researcher_id, week_iso
    """)


def _status_by_page(db_id: str, status_prop: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for page in N.query_database(db_id):
        out[page.get("id")] = N.read_response_label(page, status_prop)
    return out


def _existing_responses(sch: str) -> dict[tuple, str]:
    rows = query_json(
        f"SELECT researcher_id, canonical_id, choice "
        f"FROM {sch}.archive_responses")
    return {(r["researcher_id"], r["canonical_id"]): r["choice"] for r in rows}


def _response_totals(sch: str) -> dict[str, int]:
    rows = query_json(
        f"SELECT researcher_id, count(*) n FROM {sch}.archive_responses "
        f"GROUP BY researcher_id")
    return {r["researcher_id"]: int(r["n"]) for r in rows}


def _history_existing(db_id: str, props: dict) -> set[tuple]:
    seen: set[tuple] = set()
    for page in N.query_database(db_id):
        cid = N.read_rich_text(page, props.get("canonical_id", "canonical_id")).strip()
        sel = (page.get("properties") or {}).get(props.get("researcher", "Researcher")) or {}
        rid = ((sel.get("select") or {}) or {}).get("name") or ""
        if cid:
            seen.add((rid, cid))
    return seen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    load_env()
    sch = ledger_schema()
    db_id = N.digest_db_id()
    props = N.digest_props()
    status_prop = props["status"]

    pending = _pending_rows(sch)
    if not pending:
        print("[capture] no pending digest rows. Nothing to poll.")
        return 0
    print(f"[capture] {len(pending)} pending row(s) to check.")

    status_map = _status_by_page(db_id, status_prop)
    existing = _existing_responses(sch)

    # Plan the changes first (so dry-run and apply share one code path).
    to_capture: list[dict] = []   # {digest_id, rid, cid, week, choice, page_id, preexisting}
    missing_page = 0
    for r in pending:
        pid = r["notion_page_id"]
        if pid in status_map:
            label = status_map[pid]
        else:
            # Page id is staged in the ledger but absent from the DB listing
            # (page deleted/moved, or a stale id). Warn loudly + try a direct
            # fetch before giving up — never silently lose a response (codex).
            missing_page += 1
            print(f"[capture] ⚠ page {pid} for {r['researcher_id']}/"
                  f"{r['canonical_id'][:10]} missing from DB listing; "
                  f"trying direct fetch.", file=sys.stderr)
            try:
                label = N.read_response_label(N.retrieve_page(pid), status_prop)
            except Exception as e:
                print(f"[capture]   direct fetch failed: {type(e).__name__}: "
                      f"{str(e)[:120]} — left pending.", file=sys.stderr)
                continue
        choice = N.classify_status(label)
        if choice is None:
            continue
        key = (r["researcher_id"], r["canonical_id"])
        to_capture.append({**r, "label": label, "choice": choice,
                           "preexisting": existing.get(key)})

    if missing_page:
        print(f"[capture] ⚠ {missing_page} pending row(s) had a page id missing "
              f"from the DB listing (see warnings above).")
    if not to_capture:
        print("[capture] no Status changes detected (all still 미응답).")
        return 0

    for c in to_capture:
        pre = (f"  ⚠ archive_responses already has '{c['preexisting']}' — "
               f"PRESERVED (무손상), not overwritten") if c["preexisting"] else ""
        print(f"[capture] {c['researcher_id']} {c['canonical_id'][:10]} "
              f"'{c['label']}' → {c['choice']}{pre}")

    if not args.apply:
        n_new = sum(1 for c in to_capture if not c["preexisting"])
        print(f"[capture] dry-run only. Would update {len(to_capture)} digest row(s); "
              f"{n_new} new archive_responses insert(s), "
              f"{len(to_capture) - n_new} preserved. Re-run with --apply.")
        return 0

    # ---- apply ----
    # History mirror is best-effort and MUST NOT block the primary capture: if
    # the history DB is mis-configured, disable it and carry on (codex HIGH).
    hist_id = N.history_db_id()
    hist_seen: set = set()
    if hist_id:
        try:
            hist_seen = _history_existing(hist_id, props)
        except Exception as e:
            print(f"[capture] history mirror disabled (setup failed): "
                  f"{type(e).__name__}: {str(e)[:140]}", file=sys.stderr)
            hist_id = None
    today_iso = datetime.now(KST).strftime("%Y-%m-%d")

    affected_rids: set[str] = set()
    inserted = preserved = 0
    for c in to_capture:
        if c["preexisting"]:
            # No archive_responses write (무손상) — only the digest ledger
            # records this week's Notion state.
            exec_many(
                f"UPDATE {sch}.archive_weekly_digests "
                f"SET response_choice = %s, response_at = now() WHERE digest_id = %s",
                [(c["choice"], c["digest_id"])],
            )
            preserved += 1
            continue
        # NEW response: write the PERMANENT truth row FIRST, then mark the
        # digest answered. If the digest UPDATE fails after this commit, the
        # next poll sees preexisting=True and just retries the digest update —
        # the truth row is never lost (codex CRITICAL: capture ordering).
        detail = {"source": "weekly_notion", "week": c["week_iso"],
                  "notion_page_id": c["notion_page_id"]}
        exec_many(
            f"INSERT INTO {sch}.archive_responses "
            f"(researcher_id, canonical_id, session_id, choice, choice_detail, "
            f"responded_at) VALUES (%s,%s,%s,%s,%s::jsonb, now()::text) "
            f"ON CONFLICT (researcher_id, canonical_id) DO NOTHING",
            [(c["researcher_id"], c["canonical_id"], f"weekly-{c['week_iso']}",
              c["choice"], json.dumps(detail, ensure_ascii=False))],
        )
        exec_many(
            f"UPDATE {sch}.archive_weekly_digests "
            f"SET response_choice = %s, response_at = now() WHERE digest_id = %s",
            [(c["choice"], c["digest_id"])],
        )
        inserted += 1
        affected_rids.add(c["researcher_id"])
        if hist_id and (c["researcher_id"], c["canonical_id"]) not in hist_seen:
            try:
                N.create_page(hist_id, {
                    "Title":        N.p_title(c["canonical_id"]),
                    "Researcher":   N.p_select(c["researcher_id"]),
                    "Status":       N.p_select(_hist_status(c["choice"])),
                    "Week":         N.p_rich_text(c["week_iso"]),
                    "canonical_id": N.p_rich_text(c["canonical_id"]),
                    "Responded At": N.p_date(today_iso),
                })
                hist_seen.add((c["researcher_id"], c["canonical_id"]))
            except Exception as e:
                print(f"[capture] history mirror skip "
                      f"{c['researcher_id']}/{c['canonical_id'][:10]}: "
                      f"{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)

    print(f"[capture] applied — digest updated={len(to_capture)} "
          f"archive_responses inserted={inserted} preserved={preserved}")

    # Belief-update-due: persist a DURABLE flag (no LLM) when a researcher's
    # cumulative response count crosses a 10-multiple, so an unattended cron
    # crossing leaves a machine-readable signal for the operator.
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
                  "`build_researcher_queue.py --apply` so next week's digest "
                  "reflects the learned preferences.")
    return 0


def _hist_status(choice: str) -> str:
    """Map an archive choice to the history DB's Status select option label
    (the same labels send_notion / the digest DB use for the response)."""
    inv = {v: k for k, v in N.status_choice_map().items()}  # choice -> label
    return inv.get(choice, choice)


if __name__ == "__main__":
    raise SystemExit(main())
