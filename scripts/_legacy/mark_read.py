#!/usr/bin/env python3
"""
scripts/mark_read.py — record that a researcher has read a paper, so the
never-re-recommend dedup (rules/04) permanently excludes it.

Operator override 2026-05-19: JOP read the prior recommendation
(10.7554/elife.101277, "Endogenous precision of the number sense"). Insert
it into csnl_paper_rec.paper_recommendations_read (idempotent).

OPERATOR-RUN (Postgres write, csnl_paper_rec only — csnl_research untouched):
    ! python scripts/mark_read.py
    ! python scripts/mark_read.py --unit JOP --member JOP \
        --doi 10.x/y --title "..."   # generic form
"""
import argparse
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from _db import load_env, exec_many, query_json, ledger_schema  # noqa: E402

_KST = timezone(timedelta(hours=9))


def ndoi(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    return re.sub(r"^doi:\s*", "", s, flags=re.I).strip().lower()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--unit", default="JOP")
    ap.add_argument("--member", default="JOP")
    ap.add_argument("--doi", default="10.7554/elife.101277")
    ap.add_argument("--title", default="Endogenous precision of the number sense")
    a = ap.parse_args()
    load_env()
    s = ledger_schema()
    doi = ndoi(a.doi)

    existing = query_json(
        f"SELECT 1 AS x FROM {s}.paper_recommendations_read "
        f"WHERE unit_id='{a.unit}' AND member_init='{a.member}' "
        f"AND lower(paper_doi)='{doi}'")
    if existing:
        print(f"[mark_read] already recorded: {a.member}/{a.unit} {doi} — no-op")
        return 0

    n = exec_many(
        f"INSERT INTO {s}.paper_recommendations_read "
        "(unit_id,member_init,paper_doi,paper_title,marked_read_at) "
        "VALUES (%s,%s,%s,%s,%s)",
        [(a.unit, a.member, doi, a.title,
          datetime.now(_KST).isoformat(timespec="seconds"))])
    cnt = query_json(
        f"SELECT count(*) AS n FROM {s}.paper_recommendations_read")[0]["n"]
    print(f"[mark_read] inserted {n} row ({a.member}/{a.unit} {doi}); "
          f"paper_recommendations_read now {cnt} row(s).")
    print("[mark_read] dedup will now permanently exclude this paper for the unit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
