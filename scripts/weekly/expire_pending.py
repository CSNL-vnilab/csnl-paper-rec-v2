#!/usr/bin/env python3
"""
scripts/weekly/expire_pending.py — close the response window on delivered-but-
unanswered digest rows by marking them response_choice='expired'.

Design §6 / §3: run Sunday 18:00 KST, just before the next Monday build. An
expired paper enters the 8-week cooldown (archive_paper_cooldown view includes
NULL + 'expired'), so it can resurface later but won't be re-sent next week.

무손상: this touches ONLY archive_weekly_digests. archive_responses is never
written here — a non-response is not a response.

Only rows that were actually delivered (notion_page_id IS NOT NULL) are
expired; built-but-unsent rows are left for send_notion.py to retry (and are
surfaced as a heads-up). Time-based threshold (sent_at age) so the sweep is
robust to exactly when it runs.

Usage:
    python3 scripts/weekly/expire_pending.py            # dry-run
    python3 scripts/weekly/expire_pending.py --apply     # mark expired
    python3 scripts/weekly/expire_pending.py --after-days 6 --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
from _db import load_env, query_json, exec_sql, ledger_schema  # noqa: E402

EXPIRE_AFTER_DAYS = 6   # sent Mon 09:00 → ~6.4 days old by Sun 18:00 sweep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--after-days", type=int, default=EXPIRE_AFTER_DAYS,
                    help=f"Expire delivered+pending rows older than this many "
                         f"days (default {EXPIRE_AFTER_DAYS}).")
    args = ap.parse_args()
    load_env()
    sch = ledger_schema()
    days = int(args.after_days)
    # Bound the window: a negative/zero value would make the interval point into
    # the future and expire EVERY delivered+pending row immediately; an absurdly
    # large one is almost certainly an operator typo (codex finding).
    if not (1 <= days <= 56):
        print(f"ERROR: --after-days must be 1..56 (56d = the 8-week cooldown), "
              f"got {days}", file=sys.stderr)
        return 2

    to_expire = query_json(f"""
        SELECT digest_id, week_iso, researcher_id, canonical_id, sent_at
          FROM {sch}.archive_weekly_digests
         WHERE response_choice IS NULL
           AND notion_page_id IS NOT NULL
           AND sent_at < now() - interval '{days} days'
         ORDER BY week_iso, researcher_id
    """)
    stale_unsent = query_json(f"""
        SELECT count(*) AS n
          FROM {sch}.archive_weekly_digests
         WHERE response_choice IS NULL
           AND notion_page_id IS NULL
           AND sent_at < now() - interval '{days} days'
    """)
    n_stale = int(stale_unsent[0]["n"]) if stale_unsent else 0

    print(f"[expire] {len(to_expire)} delivered+unanswered row(s) older than "
          f"{days}d to mark 'expired'.")
    by_week: dict[str, int] = {}
    for r in to_expire:
        by_week[r["week_iso"]] = by_week.get(r["week_iso"], 0) + 1
    for wk in sorted(by_week):
        print(f"    {wk}: {by_week[wk]}")
    if n_stale:
        print(f"[expire] heads-up: {n_stale} built-but-UNSENT row(s) older than "
              f"{days}d (notion_page_id NULL) — left for send_notion.py to retry, "
              f"not expired.")

    if not args.apply:
        print("[expire] dry-run only. Re-run with --apply to mark expired.")
        return 0
    if not to_expire:
        print("[expire] nothing to expire.")
        return 0
    exec_sql(f"""
        UPDATE {sch}.archive_weekly_digests
           SET response_choice = 'expired'
         WHERE response_choice IS NULL
           AND notion_page_id IS NOT NULL
           AND sent_at < now() - interval '{days} days'
    """)
    print(f"[expire] marked {len(to_expire)} row(s) expired. "
          f"archive_responses untouched (무손상).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
