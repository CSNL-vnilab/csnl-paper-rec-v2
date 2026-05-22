#!/usr/bin/env python3
"""
plugin/scripts/pick_next.py — return the next paper for the interview.

Usage:
    python plugin/scripts/pick_next.py --init <init> [--session <sid>] \\
        [--chunk recent|mid|classic|auto]

Walks archive_researcher_queues in chunk order (recent → mid → classic),
**re-ranks the unanswered subset on the fly** against the researcher's
latest verified dim_preferences (so the next paper after a Stage-4
belief update reflects the new belief — without needing an operator
rebuild). Skips any canonical_id the researcher has already responded
to. Emits one JSON object with the next paper, or {"done": true} if the
chunk is exhausted.

The stored `archive_researcher_queues.composite` is the BUILD-time
score; this script recomputes composite per-paper using the latest
`archive_profile_verifications.dim_preferences` + the paper's dim_tags
(via `archive_filter_decisions.dim_tags`). The cosine `similarity`
column is reused as-is (the paper embedding doesn't change).

Reads-only on archive_papers / archive_filter_decisions /
archive_researcher_queues / archive_profile_verifications.
Writes only `archive_interview_sessions.current_issue` (staging) when
--session is passed — same as before.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _pdb import load_env, query, exec_sql, schema  # noqa: E402

_CHUNK_ORDER = ("recent", "mid", "classic")

# Composite formula — must mirror scripts/archive/build_researcher_queue.py
# so the in-session refresh produces the same shape of score as the
# operator's full rebuild.
_W_COS, _W_DIM    = 0.55, 0.30
_COMBO_STEP       = 0.05
_MAX_COMBO_BONUS  = 0.15
_TIER_S_COS, _TIER_S_DIM = 0.40, 0.50
_TIER_A_COS_HIGH, _TIER_A_DIM_HIGH = 0.40, 0.30
_TIER_A_COS_MID,  _TIER_A_DIM_MID  = 0.30, 0.60
_TIER_B_COS                        = 0.30


def _load_taxonomy_combos() -> list[dict]:
    """Locate plugin/data/taxonomy.json relative to this script and
    return the combos list. Returns [] if missing."""
    tx = Path(__file__).resolve().parent.parent / "data" / "taxonomy.json"
    if not tx.exists():
        return []
    try:
        return json.loads(tx.read_text("utf-8")).get("combos") or []
    except Exception:
        return []


def _dim_score(paper_dims: dict, prefs: dict) -> float:
    """Average over researcher-populated dims of the pref weight on any
    cat the paper carries in that dim. Mirrors build_researcher_queue
    _dim_score()."""
    contributions: list[float] = []
    for dim in ("focus", "method", "stim", "subj"):
        weights = prefs.get(dim) or {}
        if not weights:
            continue
        cats = paper_dims.get(dim) or []
        if not cats:
            contributions.append(0.0)
            continue
        contributions.append(max((float(weights.get(c, 0.0)) for c in cats),
                                 default=0.0))
    if not contributions:
        return 0.0
    return sum(contributions) / len(contributions)


def _combo_hits(paper_dims: dict, paper_lab: list, combos: list,
                pref_codes: set) -> list[str]:
    """Mirrors build_researcher_queue _combo_hits() — guard role
    excluded; researcher's pref_codes must overlap at least one combo
    code."""
    bag: set[str] = set(paper_lab or [])
    for cats in (paper_dims or {}).values():
        bag.update(cats or [])
    out = []
    for c in combos or []:
        if (c.get("role") or "relevance") == "guard":
            continue
        codes = c.get("codes") or []
        if not codes or not all(code in bag for code in codes):
            continue
        if pref_codes and not (set(codes) & pref_codes):
            continue
        out.append(c["id"])
    return out


def _pref_code_set(prefs: dict) -> set[str]:
    out: set[str] = set()
    for dim in ("focus", "method", "stim", "subj"):
        for code, w in (prefs.get(dim) or {}).items():
            if w and w > 0:
                out.add(code)
    for combo in prefs.get("combo_bonus") or []:
        if isinstance(combo, list):
            out.update(combo)
    return out


def _composite(cos: float, dim_score: float, n_combos: int) -> float:
    bonus = min(_MAX_COMBO_BONUS, _COMBO_STEP * n_combos)
    return _W_COS * max(0.0, cos) + _W_DIM * dim_score + bonus


def _tier(cos: float, dim_score: float, n_combos: int) -> str:
    if cos >= _TIER_S_COS and dim_score >= _TIER_S_DIM and n_combos >= 1:
        return "S"
    if (cos >= _TIER_A_COS_HIGH and dim_score >= _TIER_A_DIM_HIGH) \
       or (cos >= _TIER_A_COS_MID and dim_score >= _TIER_A_DIM_MID):
        return "A"
    if cos >= _TIER_B_COS:
        return "B"
    return "C"


def _latest_prefs(sch: str, init: str) -> dict:
    rows = query(
        f"SELECT dim_preferences FROM {sch}.archive_profile_verifications "
        f"WHERE researcher_id = %s AND dim_preferences IS NOT NULL "
        f"ORDER BY confirmed_at DESC LIMIT 1",
        (init,),
    )
    if not rows:
        return {}
    v = rows[0]["dim_preferences"]
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {}
    return v or {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", required=True)
    ap.add_argument("--session", default=None,
                    help="Session UUID — required when staging an issue "
                         "for record_choice.py verification.")
    ap.add_argument("--chunk", default="auto",
                    choices=("recent", "mid", "classic", "auto"))
    args = ap.parse_args()

    # Normalize init: csnl_research keys are uppercase.
    args.init = args.init.strip().upper()
    load_env()
    sch = schema()

    chunks = (args.chunk,) if args.chunk != "auto" else _CHUNK_ORDER

    # P17: refresh ordering using LATEST dim_preferences (not the
    # build-time snapshot). After a Stage-4 belief update, the very next
    # paper this script returns reflects the updated belief — without
    # the operator having to rebuild the queue table.
    prefs = _latest_prefs(sch, args.init)
    combos = _load_taxonomy_combos() if prefs else []
    pref_codes = _pref_code_set(prefs) if prefs else set()

    def _refresh(rows: list[dict]) -> list[dict]:
        """Recompute composite + tier for each row against latest prefs.
        Mutates each row in place; returns the list sorted by fresh
        composite (descending), then by stored rank as tiebreaker."""
        if not prefs:
            return rows  # no verified prefs yet; respect build-time order
        for r in rows:
            cos = float(r.get("similarity") or 0.0)
            pdims = r.get("dim_tags") or {}
            if isinstance(pdims, str):
                try: pdims = json.loads(pdims)
                except Exception: pdims = {}
            plab = r.get("lab_scope_tags") or []
            if isinstance(plab, str):
                try: plab = json.loads(plab)
                except Exception: plab = []
            ds = _dim_score(pdims, prefs)
            chits = _combo_hits(pdims, plab, combos, pref_codes)
            fresh_comp = _composite(cos, ds, len(chits))
            fresh_tier = _tier(cos, ds, len(chits))
            r["composite"]  = fresh_comp
            r["tier"]       = fresh_tier
            r["dim_match"]  = {
                "matched": {dim: pdims.get(dim) or [] for dim in
                            ("focus", "method", "stim", "subj")},
                "combos":  chits,
                "tier":    fresh_tier,
                "refreshed_in_session": True,
            }
        rows.sort(key=lambda r: (-(r["composite"] or 0.0),
                                  r.get("rank_in_chunk") or 9999))
        return rows

    for ch in chunks:
        sql = f"""
            SELECT q.canonical_id, q.chunk, q.rank_in_chunk, q.similarity,
                   p.doi, p.title, p.authors_json, p.venue, p.year,
                   p.pub_date, p.is_preprint, p.abstract,
                   f.lab_scope_tags, f.dim_tags
              FROM {sch}.archive_researcher_queues q
              JOIN {sch}.archive_papers p
                ON p.canonical_id = q.canonical_id
              LEFT JOIN {sch}.archive_filter_decisions f
                ON f.canonical_id = q.canonical_id
             WHERE q.researcher_id = %s
               AND q.chunk = %s
               AND NOT EXISTS (
                 SELECT 1 FROM {sch}.archive_responses r
                  WHERE r.researcher_id = q.researcher_id
                    AND r.canonical_id  = q.canonical_id
               )
        """
        # No LIMIT — we need to re-rank the whole unanswered subset.
        rows = query(sql, (args.init, ch))
        if rows:
            rows = _refresh(rows)
            r = rows[0]
            # Always serialise json columns (psycopg2 returns Python objects).
            def _j(v):
                if isinstance(v, str):
                    try:
                        return json.loads(v)
                    except Exception:
                        return v
                return v
            r["authors_json"]   = _j(r.get("authors_json"))
            r["lab_scope_tags"] = _j(r.get("lab_scope_tags"))
            r["dim_tags"]       = _j(r.get("dim_tags"))
            r["dim_match"]      = _j(r.get("dim_match"))

            # Stage this canonical_id on the session so record_choice.py can
            # verify the choice came from a recent pick_next call. If
            # --session was not passed, we issue without staging (legacy
            # call sites; record_choice will then accept any cid).
            if args.session:
                exec_sql(
                    f"""
                    UPDATE {sch}.archive_interview_sessions
                       SET current_issue   = %s::jsonb,
                           last_active_at  = now()::text
                     WHERE session_id = %s AND researcher_id = %s
                    """,
                    (json.dumps({"canonical_id": r["canonical_id"],
                                 "issued_at":    str(r.get("rank_in_chunk")),
                                 "chunk":        r.get("chunk")}),
                     args.session, args.init),
                )
            print(json.dumps({"paper": r, "done": False}, ensure_ascii=False))
            return 0

    # All chunks exhausted.
    if args.session:
        exec_sql(
            f"""
            UPDATE {sch}.archive_interview_sessions
               SET current_issue = NULL,
                   last_active_at = now()::text
             WHERE session_id = %s AND researcher_id = %s
            """,
            (args.session, args.init),
        )
    print(json.dumps({"done": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
