#!/usr/bin/env python3
"""
scripts/archive/paper_blitz_feed.py — operator cron: Wednesday-morning
Paper Blitz schedule from "already_read" responses in the prior 7 days.

The lab runs a 5-min Paper Blitz journal club every Wednesday morning.
Each researcher presents ONE paper they recently marked `already_read`
during the archive interview, then the lab discusses for 5 min.

This script:
  1) For each researcher, finds papers they marked `already_read` in the
     last 7 days that have NOT yet been presented at a Paper Blitz.
  2) Picks ONE paper per researcher (most-recent already_read with the
     highest composite from the queue snapshot, as a quality proxy).
  3) Persists the schedule to archive_paper_blitz.
  4) Renders a Wednesday-morning Korean agenda.

If a researcher has zero new already_read this week, they are listed as
"이번 주 발표 없음 (지난 1주간 새로 읽은 paper 없음)" and skipped — not
forced to present.

Usage:
    python3 scripts/archive/paper_blitz_feed.py                    # next Wed, all researchers
    python3 scripts/archive/paper_blitz_feed.py --date 2026-05-27  # explicit Wed
    python3 scripts/archive/paper_blitz_feed.py --dry-run          # preview only
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))

from _db import exec_many, load_env, query_json  # noqa: E402

_ALL_RIDS = ("BHL", "BYL", "JOP", "JYK", "MSY", "SMJ", "SYJ")


def _first_author(authors_json) -> str:
    if not authors_json:
        return ""
    if isinstance(authors_json, str):
        try:
            import json as _j
            authors_json = _j.loads(authors_json)
        except (ValueError, TypeError):
            return ""
    if not isinstance(authors_json, list) or not authors_json:
        return ""
    a = authors_json[0]
    if isinstance(a, str):
        return a
    if isinstance(a, dict):
        if a.get("name"):
            return str(a["name"])
        fam = a.get("family") or a.get("surname")
        giv = a.get("given") or a.get("first")
        if fam and giv:
            return f"{giv} {fam}"
        return str(fam or giv or "")
    return ""


def _next_wednesday(today: _dt.date) -> _dt.date:
    """Return the upcoming Wednesday (or today, if today is Wednesday)."""
    delta = (2 - today.weekday()) % 7  # weekday(): Mon=0, Wed=2
    return today + _dt.timedelta(days=delta)


def _kst_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=9))).isoformat()


_BLITZ_TABLE_EXISTS: bool | None = None


def _blitz_table_exists() -> bool:
    global _BLITZ_TABLE_EXISTS
    if _BLITZ_TABLE_EXISTS is not None:
        return _BLITZ_TABLE_EXISTS
    rows = query_json(
        "SELECT 1 AS x FROM information_schema.tables "
        "WHERE table_schema='csnl_paper_rec' AND table_name='archive_paper_blitz'"
    )
    _BLITZ_TABLE_EXISTS = bool(rows)
    return _BLITZ_TABLE_EXISTS


def _pick_for_researcher(rid: str, blitz_date: _dt.date) -> dict | None:
    """One paper this researcher marked already_read in the prior 7 days
    that has not yet been scheduled for a Paper Blitz. On first run before
    the operator applies schema_archive.sql, the archive_paper_blitz table
    may not exist yet — we skip the dedup clause and rely on UPSERT later."""
    week_start = (blitz_date - _dt.timedelta(days=7)).isoformat()
    dedup_clause = (
        "AND NOT EXISTS ( SELECT 1 FROM csnl_paper_rec.archive_paper_blitz b "
        " WHERE b.presenter = r.researcher_id AND b.canonical_id = r.canonical_id )"
        if _blitz_table_exists() else ""
    )
    sql = f"""
SELECT r.canonical_id, r.responded_at,
       p.title, p.year, p.authors_json, p.venue,
       q.composite, q.dim_match
  FROM csnl_paper_rec.archive_responses r
  JOIN csnl_paper_rec.archive_papers p
    ON p.canonical_id = r.canonical_id
  LEFT JOIN csnl_paper_rec.archive_researcher_queues q
    ON q.researcher_id = r.researcher_id
   AND q.canonical_id  = r.canonical_id
 WHERE r.researcher_id = '{rid}'
   AND r.choice        = 'already_read'
   AND r.responded_at >= '{week_start}'
   {dedup_clause}
 ORDER BY q.composite DESC NULLS LAST, r.responded_at DESC
 LIMIT 1
"""
    rows = query_json(sql)
    return rows[0] if rows else None


def _persist(blitz_date: str, slots: list[dict], now_iso: str) -> int:
    rows = []
    for i, s in enumerate(slots, start=1):
        rows.append((
            blitz_date,
            s["presenter"],
            s["canonical_id"],
            i,
            "auto_last_week_read",
            now_iso,
        ))
    if not rows:
        return 0
    return exec_many(
        "INSERT INTO csnl_paper_rec.archive_paper_blitz "
        "(blitz_date, presenter, canonical_id, slot_order, picked_from, scheduled_at) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (blitz_date, presenter, canonical_id) DO UPDATE SET "
        "  slot_order = EXCLUDED.slot_order, picked_from = EXCLUDED.picked_from, "
        "  scheduled_at = EXCLUDED.scheduled_at",
        rows,
    )


def _render_korean(blitz_date: _dt.date, slots: list[dict], skipped: list[str]) -> str:
    lines = [f"# Paper Blitz — {blitz_date.isoformat()} (수)\n"]
    if not slots:
        lines.append("이번 주 발표자 없음 (지난 1주간 새로 읽은 paper 없음).")
        lines.append("")
        return "\n".join(lines)
    lines.append(f"발표 {len(slots)}편 · 각 5분 + 토론\n")
    for i, s in enumerate(slots, start=1):
        title = (s.get("title") or "").strip() or "(제목 없음)"
        year = s.get("year") or "----"
        first = _first_author(s.get("authors_json"))
        venue = (s.get("venue") or "").strip()
        meta = " / ".join(x for x in (first, venue) if x)
        head = f"**{i}. {s['presenter']}** — {title} ({year})"
        if meta:
            head += f"  ·  {meta}"
        lines.append(head)
    if skipped:
        lines.append("")
        lines.append(f"발표 없음: {', '.join(skipped)} (지난 1주간 새로 읽은 paper 없음)")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="Blitz Wed date YYYY-MM-DD (default: next Wed)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    load_env()
    today = _dt.date.today()
    blitz_date = _dt.date.fromisoformat(args.date) if args.date else _next_wednesday(today)
    now_iso = _kst_now_iso()

    slots: list[dict] = []
    skipped: list[str] = []
    for rid in _ALL_RIDS:
        pick = _pick_for_researcher(rid, blitz_date)
        if pick is None:
            skipped.append(rid)
            continue
        pick["presenter"] = rid
        slots.append(pick)

    md = _render_korean(blitz_date, slots, skipped)
    print(md)

    if not args.dry_run:
        if not _blitz_table_exists():
            print(
                "[paper_blitz_feed] archive_paper_blitz 테이블이 아직 없습니다. "
                "운영자 측에서 `! python3 scripts/init_db.py` 로 스키마를 적용해주세요.",
                file=sys.stderr,
            )
            return 2
        n = _persist(blitz_date.isoformat(), slots, now_iso)
        print(f"[paper_blitz_feed] persisted {n} rows for {blitz_date.isoformat()}.", file=sys.stderr)
    else:
        print("[paper_blitz_feed] dry-run; no DB writes.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
