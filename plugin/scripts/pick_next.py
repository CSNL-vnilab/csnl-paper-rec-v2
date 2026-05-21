#!/usr/bin/env python3
"""
plugin/scripts/pick_next.py — return the next paper for the interview.

Usage:
    python plugin/scripts/pick_next.py --init <init> [--session <sid>] \\
        [--chunk recent|mid|classic|auto]

Walks archive_researcher_queues in chunk order (recent → mid → classic),
rank order within chunk, skipping any canonical_id the researcher has
already responded to. Emits one JSON object with the next paper, or
{"done": true} if the queue is exhausted.

Reads-only on archive_papers / archive_filter_decisions / archive_paper_sources.
Never writes — record_choice.py is the only writer for responses.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _pdb import load_env, query, exec_sql, schema  # noqa: E402

_CHUNK_ORDER = ("recent", "mid", "classic")


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

    for ch in chunks:
        sql = f"""
            SELECT q.canonical_id, q.chunk, q.rank_in_chunk, q.similarity,
                   p.doi, p.title, p.authors_json, p.venue, p.year,
                   p.pub_date, p.is_preprint, p.abstract,
                   f.lab_scope_tags
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
             ORDER BY q.rank_in_chunk ASC, q.canonical_id ASC
             LIMIT 1
        """
        rows = query(sql, (args.init, ch))
        if rows:
            r = rows[0]
            # Always serialise json columns (psycopg2 returns Python objects).
            def _j(v):
                if isinstance(v, str):
                    try:
                        return json.loads(v)
                    except Exception:
                        return v
                return v
            r["authors_json"]  = _j(r.get("authors_json"))
            r["lab_scope_tags"] = _j(r.get("lab_scope_tags"))

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
