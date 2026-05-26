#!/usr/bin/env python3
"""
scripts/archive/validate_drift.py — operator-side drift monitor for the
recommendation system. Reads `archive_responses` to compute rolling
30-day per-researcher MCQ quality + flags stale fingerprints.

This is P19a Voice C live monitoring — NOT the CWLL backtest (cut per
codex finding #1 — operator-curated CWLL log is circular as ground
truth). The validate_recommendation.py + validate_weekly_digest.py
scripts proposed by Voice C are also deferred to P20 alongside the
non-circular ground-truth survey hook (codex #9).

Operator-run:
    ! python scripts/archive/validate_drift.py             # all active
    ! python scripts/archive/validate_drift.py JOP         # single

Outputs a per-researcher report:
  - n MCQs (total + last 30 days)
  - mcq_precision_30d = (save_later + tell_me_more) /
                        (save_later + tell_me_more + not_relevant)
    activates once n_30d >= 10
  - already_read_rate_30d = already_read / (all MCQs in 30d)
  - drift flags: mcq_precision_drop / novelty_collapse / topic_shift /
                 no_fingerprint

Postgres read-only. No LLM. No DB writes (alert state stays in the
text report; operator decides to act).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

PRECISION_FLOOR = 0.45
NOVELTY_CEIL    = 0.40
MIN_N_FOR_FLAGS = 10


def _fmt_pct(x):
    return f"{x:.0%}" if x is not None else "—"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("researcher", nargs="?", default=None)
    ap.add_argument("--window-days", type=int, default=30)
    args = ap.parse_args()

    sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
    from _db import load_env, query_json
    load_env()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.window_days)).isoformat()

    if args.researcher:
        inits = [args.researcher.strip().upper()]
    else:
        rows = query_json(
            "SELECT DISTINCT init FROM csnl_research.projects "
            "WHERE phase IN ('data_collection','analysis','manuscript_draft') "
            "  AND confidence_avg >= 0.7 ORDER BY init"
        )
        inits = [r["init"] for r in rows]

    print(f"{'init':6s}  {'n_all':>6s}  {'n_30d':>6s}  "
          f"{'precision_30d':>14s}  {'already_read_30d':>17s}  flags")
    print("-" * 80)

    for init in inits:
        n_all = query_json(
            f"SELECT COUNT(*) AS n FROM csnl_paper_rec.archive_responses "
            f"WHERE researcher_id = '{init}'"
        )[0]["n"]
        rows = query_json(f"""
            SELECT choice, COUNT(*) AS n
              FROM csnl_paper_rec.archive_responses
             WHERE researcher_id = '{init}'
               AND responded_at >= '{cutoff}'
             GROUP BY choice
        """)
        by_choice = {r["choice"]: int(r["n"]) for r in rows}
        n_30d  = sum(by_choice.values())
        n_save = by_choice.get("save_later", 0) + by_choice.get("tell_me_more", 0)
        n_neg  = by_choice.get("not_relevant", 0)
        n_read = by_choice.get("already_read", 0)
        precision_30d = (n_save / (n_save + n_neg)) if (n_save + n_neg) > 0 else None
        already_read_rate = (n_read / n_30d) if n_30d > 0 else None

        flags = []
        if n_30d >= MIN_N_FOR_FLAGS:
            if precision_30d is not None and precision_30d < PRECISION_FLOOR:
                flags.append("mcq_precision_drop")
            if already_read_rate is not None and already_read_rate > NOVELTY_CEIL:
                flags.append("novelty_collapse")

        fp_path = _REPO_ROOT / "state" / "archive" / "fingerprints" / f"{init}.json"
        if fp_path.exists():
            try:
                fp = json.loads(fp_path.read_text("utf-8"))
                fp_built = fp.get("built_at")
                proj_rows = query_json(f"""
                    SELECT MAX(last_updated_at)::text AS m
                      FROM csnl_research.projects
                     WHERE init = '{init}'
                       AND phase IN ('data_collection','analysis','manuscript_draft')
                """)
                proj_latest = proj_rows[0]["m"] if proj_rows else None
                if fp_built and proj_latest and str(proj_latest) > str(fp_built):
                    flags.append("topic_shift")
            except Exception:
                pass
        else:
            flags.append("no_fingerprint")

        print(f"{init:6s}  {n_all:>6d}  {n_30d:>6d}  "
              f"{_fmt_pct(precision_30d):>14s}  {_fmt_pct(already_read_rate):>17s}  "
              f"{','.join(flags) if flags else '—'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
