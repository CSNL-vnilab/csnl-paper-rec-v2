#!/usr/bin/env python3
"""
scripts/archive/list_status.py — operator-side view of each researcher's
paper status (read / to_read / not_interested).

The persistent knowledge base lives in csnl_paper_rec.archive_responses;
the archive_paper_status view (P19e) exposes it in plain-Korean labels.
This script is the friendly CLI on top.

Usage:
    python3 scripts/archive/list_status.py               # all researchers, counts
    python3 scripts/archive/list_status.py JOP           # JOP — per-status list
    python3 scripts/archive/list_status.py JOP --status read      # only read papers

Read-only. No DB writes. Korean researcher-facing labels in output.

`maybe_interested` was retired 2026-05-28 (the 4th MCQ option was removed).
The label is intentionally kept absent from _STATUS_KO; historical rows
(if any) will surface in the count tables under the legacy key but not be
formatted, and operators are pointed to the migration script.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_STATUS_KO = {
    "read":              "이미 읽음",
    "to_read":           "읽을 예정",
    "not_interested":    "관심 없음",
    "skipped":           "건너뜀",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("researcher", nargs="?", default=None)
    ap.add_argument("--status", default=None,
                    choices=list(_STATUS_KO.keys()),
                    help="Filter to one status only")
    ap.add_argument("--limit", type=int, default=30,
                    help="Per-status list cap (default 30)")
    args = ap.parse_args()

    sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
    from _db import load_env, query_json
    load_env()

    # Aggregate counts.
    if args.researcher:
        rid = args.researcher.strip().upper()
        rows = query_json(
            f"SELECT paper_status, COUNT(*) AS n "
            f"FROM csnl_paper_rec.archive_paper_status "
            f"WHERE researcher_id = '{rid}' "
            f"GROUP BY paper_status ORDER BY n DESC"
        )
        if not rows:
            print(f"{rid}: 응답 0건 (archive_responses 비어있음).")
            return 0
        total = sum(int(r["n"]) for r in rows)
        print(f"\n{rid} — 총 {total}편 응답")
        for r in rows:
            label = _STATUS_KO.get(r["paper_status"], r["paper_status"])
            print(f"  {label:18s} ({r['paper_status']:18s})  n={r['n']}")

        statuses = [args.status] if args.status else list(_STATUS_KO.keys())
        for st in statuses:
            papers = query_json(
                f"SELECT s.canonical_id, p.title, p.year, s.responded_at, "
                f"       s.choice_detail "
                f"FROM csnl_paper_rec.archive_paper_status s "
                f"JOIN csnl_paper_rec.archive_papers p "
                f"  ON p.canonical_id = s.canonical_id "
                f"WHERE s.researcher_id = '{rid}' "
                f"  AND s.paper_status = '{st}' "
                f"ORDER BY s.responded_at DESC "
                f"LIMIT {args.limit}"
            )
            if not papers:
                continue
            print(f"\n--- {_STATUS_KO.get(st, st)} ({st}) — 최근 {len(papers)}편 ---")
            for p in papers:
                ts = (p["responded_at"] or "")[:10]
                t = (p["title"] or "")[:75]
                print(f"  {ts}  {p['year'] or '----'}  {t}")
        return 0

    # No researcher specified — aggregate across all.
    rows = query_json(
        "SELECT researcher_id, paper_status, COUNT(*) AS n "
        "FROM csnl_paper_rec.archive_paper_status "
        "GROUP BY researcher_id, paper_status "
        "ORDER BY researcher_id, paper_status"
    )
    by_rid: dict[str, dict[str, int]] = {}
    seen_statuses: set[str] = set()
    for r in rows:
        by_rid.setdefault(r["researcher_id"], {})[r["paper_status"]] = int(r["n"])
        seen_statuses.add(r["paper_status"])
    # Column order: known statuses first (in their _STATUS_KO order), then any
    # legacy/unknown statuses (e.g., 'maybe_interested' from rows that pre-date
    # the 2026-05-28 migration) so they cannot vanish silently from the report.
    # (codex adversarial review finding #10 — MEDIUM)
    legacy = sorted(seen_statuses - set(_STATUS_KO.keys()))
    columns = list(_STATUS_KO.keys()) + legacy
    def _label(s: str) -> str:
        return _STATUS_KO.get(s, s)
    print(f"\n{'init':6s}  " + "  ".join(f"{_label(s):>10s}" for s in columns))
    print("-" * (8 + 12 * len(columns)))
    for rid in sorted(by_rid.keys()):
        counts = by_rid[rid]
        line = f"{rid:6s}  " + "  ".join(
            f"{counts.get(s, 0):>10d}" for s in columns
        )
        print(line)
    if legacy:
        print(f"\n(legacy/unknown statuses surfaced: {', '.join(legacy)} — "
              f"likely pre-migration rows.)")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
