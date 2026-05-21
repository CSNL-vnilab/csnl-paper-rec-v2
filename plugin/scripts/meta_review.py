#!/usr/bin/env python3
"""
plugin/scripts/meta_review.py — emit a meta-review snapshot every N answers.

Usage:
    # placeholder write (first call at this at_response_count):
    python plugin/scripts/meta_review.py --init <init> --session <sid>
        [--window 10] [--note "..."]

    # researcher-confirmed update (second call, same at_response_count):
    python plugin/scripts/meta_review.py --init <init> --session <sid>
        --proposal-json '@p.json' --apply [--note "..."]

A two-call pattern is idempotent thanks to the UNIQUE (session_id,
at_response_count) constraint on archive_meta_reviews: the second call
UPDATEs the row written by the first (no duplicate row at the same N).

Reads the last `--window` responses for this researcher and returns:
  {
    at_response_count, window, breakdown, recent:[...],
    chunk_breakdown:{recent,mid,classic}, topic_freq:{...},
    proposal: {...},   # echoed back
    applied: bool,
    saved: true
  }

No LLM call inside; the skill computes the proposal via a deterministic
rubric and passes it back as JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _pdb import load_env, exec_sql, query, schema  # noqa: E402


def _read_arg(v: str) -> str:
    if v and v.startswith("@"):
        return Path(v[1:]).read_text(encoding="utf-8")
    return v or "{}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--proposal-json", default="{}")
    ap.add_argument("--apply", action="store_true",
                    help="Mark the proposal as accepted (applied=TRUE).")
    ap.add_argument("--note", default=None)
    args = ap.parse_args()

    args.init = args.init.strip().upper()
    proposal = json.loads(_read_arg(args.proposal_json))
    load_env()
    sch = schema()

    # Window of latest responses + their queue chunk + lab/dim tags + tier.
    rows = query(
        f"""
        SELECT r.canonical_id, r.choice, r.responded_at,
               q.chunk, q.rank_in_chunk, q.tier,
               p.title, p.year,
               f.lab_scope_tags, f.dim_tags
          FROM {sch}.archive_responses r
          LEFT JOIN {sch}.archive_researcher_queues q
            ON q.researcher_id = r.researcher_id AND q.canonical_id = r.canonical_id
          LEFT JOIN {sch}.archive_papers p
            ON p.canonical_id = r.canonical_id
          LEFT JOIN {sch}.archive_filter_decisions f
            ON f.canonical_id = r.canonical_id
         WHERE r.researcher_id = %s
         ORDER BY r.responded_at DESC
         LIMIT %s
        """,
        (args.init, args.window),
    )

    breakdown: dict[str, int] = {}
    chunk_breakdown: dict[str, dict[str, int]] = {}
    tier_breakdown: dict[str, dict[str, int]] = {}
    topic_freq: dict[str, int] = {}
    dim_freq: dict[str, dict[str, int]] = {"focus": {}, "method": {}, "stim": {}, "subj": {}}
    recent = []
    for r in rows:
        c = r["choice"]
        breakdown[c] = breakdown.get(c, 0) + 1
        ch = r.get("chunk") or "unknown"
        chunk_breakdown.setdefault(ch, {})
        chunk_breakdown[ch][c] = chunk_breakdown[ch].get(c, 0) + 1
        tier = r.get("tier") or "unknown"
        tier_breakdown.setdefault(tier, {})
        tier_breakdown[tier][c] = tier_breakdown[tier].get(c, 0) + 1
        tags = r.get("lab_scope_tags")
        if isinstance(tags, str):
            try: tags = json.loads(tags)
            except Exception: tags = []
        for t in (tags or []):
            topic_freq[t] = topic_freq.get(t, 0) + 1
        dtags = r.get("dim_tags")
        if isinstance(dtags, str):
            try: dtags = json.loads(dtags)
            except Exception: dtags = {}
        if isinstance(dtags, dict):
            for dim, cats in dtags.items():
                if dim in dim_freq:
                    for cat in (cats or []):
                        dim_freq[dim][cat] = dim_freq[dim].get(cat, 0) + 1
        recent.append({
            "canonical_id": r["canonical_id"],
            "choice":       c,
            "chunk":        ch,
            "tier":         tier,
            "title":        r.get("title"),
            "year":         r.get("year"),
        })

    total = query(
        f"SELECT COUNT(*) AS n FROM {sch}.archive_responses "
        f"WHERE researcher_id = %s",
        (args.init,),
    )
    at_n = int(total[0]["n"]) if total else 0

    # Idempotent UPSERT keyed on (session_id, at_response_count).
    # Second call (with --apply + real proposal) updates the placeholder.
    mid = str(uuid.uuid4())
    applied_clause_ts = "now()::text" if args.apply else "NULL"
    exec_sql(
        f"""
        INSERT INTO {sch}.archive_meta_reviews
          (id, session_id, researcher_id, at_response_count, choice_breakdown,
           criterion_proposal, applied, applied_at, recorded_at)
        VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, {applied_clause_ts}, now()::text)
        ON CONFLICT (session_id, at_response_count) DO UPDATE SET
          choice_breakdown   = EXCLUDED.choice_breakdown,
          criterion_proposal = EXCLUDED.criterion_proposal,
          applied            = EXCLUDED.applied OR archive_meta_reviews.applied,
          applied_at         = COALESCE(EXCLUDED.applied_at,
                                        archive_meta_reviews.applied_at),
          recorded_at        = EXCLUDED.recorded_at
        """,
        (mid, args.session, args.init, at_n,
         json.dumps({"breakdown": breakdown,
                     "chunk_breakdown": chunk_breakdown,
                     "tier_breakdown": tier_breakdown,
                     "topic_freq": topic_freq,
                     "dim_freq": dim_freq,
                     "note": args.note},
                    ensure_ascii=False),
         json.dumps(proposal, ensure_ascii=False),
         args.apply),
    )

    print(json.dumps({
        "at_response_count": at_n,
        "window":           args.window,
        "breakdown":        breakdown,
        "chunk_breakdown":  chunk_breakdown,
        "tier_breakdown":   tier_breakdown,
        "topic_freq":       topic_freq,
        "dim_freq":         dim_freq,
        "recent":           recent,
        "proposal":         proposal,
        "applied":          bool(args.apply),
        "saved":            True,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
