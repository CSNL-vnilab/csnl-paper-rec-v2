#!/usr/bin/env python3
"""
scripts/archive/weekly_recommend.py — operator cron: per-researcher weekly
top-N unread-paper batch.

After a researcher's profile is established (≥1 archive_profile_verifications
row) and their queue is populated (archive_researcher_queues), this script
picks the top-N papers they have NOT yet responded to and persists them as
that week's recommendation batch. The batch is stable across re-runs (UPSERT
on (researcher_id, week_iso, canonical_id)) so the operator can regenerate
without surprising researchers with a different list.

The output is a Korean markdown digest the operator manually distributes
(Slack / lab DM / email). No external send paths from this script.

Usage:
    python3 scripts/archive/weekly_recommend.py                # all researchers, N=5
    python3 scripts/archive/weekly_recommend.py --top 7        # top 7 per researcher
    python3 scripts/archive/weekly_recommend.py JOP            # just JOP
    python3 scripts/archive/weekly_recommend.py --dry-run      # preview only, no DB write
    python3 scripts/archive/weekly_recommend.py --week 2026-W22 # explicit ISO week
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

_DEFAULT_TOP = 5
_ALL_RIDS = ("BHL", "BYL", "JOP", "JYK", "MSY", "SMJ", "SYJ")


def _first_author(authors_json) -> str:
    """archive_papers.authors_json shape: [{"name":"...","position":1,...}] or
    [{"family":"...","given":"..."}] or simple list of strings. Return the
    first author name or empty string."""
    if not authors_json:
        return ""
    if isinstance(authors_json, str):
        try:
            authors_json = json.loads(authors_json)
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


def _iso_week(date: _dt.date) -> str:
    y, w, _ = date.isocalendar()
    return f"{y}-W{w:02d}"


def _kst_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=9))).isoformat()


def _why_ko(dim_match_json: str | None) -> str:
    """Extract the top render_ko signal from queue's dim_match JSON."""
    if not dim_match_json:
        return "관심 phrase 매칭"
    try:
        dm = json.loads(dim_match_json) if isinstance(dim_match_json, str) else dim_match_json
        sigs = dm.get("top_signals") or []
        if sigs and isinstance(sigs[0], dict):
            return sigs[0].get("render_ko") or sigs[0].get("phrase") or "관심 phrase 매칭"
    except (ValueError, AttributeError, TypeError):
        pass
    return "관심 phrase 매칭"


def _pick_unread(rid: str, top: int) -> list[dict]:
    """Top-N unread papers ranked by composite (P17 honors latest dim_prefs
    only at pick_next.py time; this batch uses the snapshot the queue carries
    as of build_researcher_queue.py's last apply — the cron weekly cadence
    matches the queue rebuild cadence)."""
    sql = f"""
SELECT q.canonical_id, q.composite, q.tier, q.dim_match,
       p.title, p.year, p.authors_json, p.venue
  FROM csnl_paper_rec.archive_researcher_queues q
  JOIN csnl_paper_rec.archive_papers p
    ON p.canonical_id = q.canonical_id
 WHERE q.researcher_id = '{rid}'
   AND NOT EXISTS (
     SELECT 1 FROM csnl_paper_rec.archive_responses r
      WHERE r.researcher_id = q.researcher_id
        AND r.canonical_id  = q.canonical_id
   )
 ORDER BY q.composite DESC NULLS LAST, q.rank_in_chunk ASC
 LIMIT {int(top)}
"""
    return query_json(sql)


def _persist(rid: str, week_iso: str, picks: list[dict], now_iso: str) -> int:
    rows = []
    for i, p in enumerate(picks, start=1):
        rows.append((
            rid,
            week_iso,
            p["canonical_id"],
            i,
            float(p["composite"]) if p.get("composite") is not None else None,
            _why_ko(p.get("dim_match")),
            now_iso,
        ))
    if not rows:
        return 0
    return exec_many(
        "INSERT INTO csnl_paper_rec.archive_weekly_picks "
        "(researcher_id, week_iso, canonical_id, rank, composite, why_ko, generated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (researcher_id, week_iso, canonical_id) DO UPDATE SET "
        "  rank = EXCLUDED.rank, composite = EXCLUDED.composite, "
        "  why_ko = EXCLUDED.why_ko, generated_at = EXCLUDED.generated_at",
        rows,
    )


def _render_korean(rid: str, week_iso: str, picks: list[dict]) -> str:
    if not picks:
        return f"## {rid} — {week_iso}\n\n읽지 않은 paper 가 큐에 없습니다.\n"
    lines = [f"## {rid} — {week_iso} 추천 ({len(picks)}편)\n"]
    for i, p in enumerate(picks, start=1):
        title = (p.get("title") or "").strip() or "(제목 없음)"
        year = p.get("year") or "----"
        first = _first_author(p.get("authors_json"))
        venue = (p.get("venue") or "").strip()
        why = _why_ko(p.get("dim_match"))
        tier = p.get("tier") or "-"
        head = f"{i}. **{title}** ({year})"
        meta = []
        if first:
            meta.append(first)
        if venue:
            meta.append(venue)
        if meta:
            head += " — " + " / ".join(meta)
        lines.append(head)
        lines.append(f"   - 추천 근거: {why}  ·  tier {tier}")
    lines.append("")
    lines.append(
        "응답하려면 클로드 세션에서 "
        "`/csnl-paper-archive-interview:paper-interview` 를 실행하세요. "
        "이 batch 는 다음 주 추천 직전까지 유효합니다."
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("researcher", nargs="?", default=None)
    ap.add_argument("--top", type=int, default=_DEFAULT_TOP)
    ap.add_argument("--week", default=None, help="ISO week like 2026-W22 (default: current)")
    ap.add_argument("--dry-run", action="store_true", help="Render only; no DB write")
    args = ap.parse_args()

    load_env()
    week_iso = args.week or _iso_week(_dt.date.today())
    now_iso = _kst_now_iso()

    if args.researcher:
        rids = [args.researcher.strip().upper()]
    else:
        rids = list(_ALL_RIDS)

    total_written = 0
    for rid in rids:
        picks = _pick_unread(rid, args.top)
        md = _render_korean(rid, week_iso, picks)
        print(md)
        if not args.dry_run:
            n = _persist(rid, week_iso, picks, now_iso)
            total_written += n

    if not args.dry_run:
        print(f"\n[weekly_recommend] persisted {total_written} rows to archive_weekly_picks "
              f"(week={week_iso}, researchers={len(rids)}).", file=sys.stderr)
    else:
        print("\n[weekly_recommend] dry-run; no DB writes.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
