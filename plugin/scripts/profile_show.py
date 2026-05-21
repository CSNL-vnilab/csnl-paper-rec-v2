#!/usr/bin/env python3
"""
plugin/scripts/profile_show.py — emit the researcher's current interest
profile as JSON, for the interview's Stage 1 verification step.

Usage:
    python plugin/scripts/profile_show.py <init>

Reads csnl_research.projects (READ-ONLY) — never writes there.
Output: {profile: {topics, methods, authors, projects:[...]}, session_id}.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _pdb import load_env, query, exec_sql, schema  # noqa: E402

_PROJ_QUERY = """
SELECT init, project_slug, title, phase, confidence_avg,
       purpose_jsonb, background_jsonb, connected_graph_jsonb,
       manipulation_variables_jsonb, modalities_jsonb,
       to_char(last_updated_at AT TIME ZONE 'Asia/Seoul','YYYY-MM-DD HH24:MI') AS last_updated_kst
FROM csnl_research.projects
WHERE init = %s
  AND phase IN ('data_collection','analysis','manuscript_draft')
  AND confidence_avg >= 0.7
ORDER BY project_slug
"""


def _topics_from_projects(rows: list[dict]) -> list[str]:
    out = set()
    for r in rows:
        purpose = r.get("purpose_jsonb") or {}
        if isinstance(purpose, str):
            try:
                purpose = json.loads(purpose)
            except Exception:
                purpose = {}
        for k in ("research_question", "hypothesis"):
            v = purpose.get(k)
            if v:
                out.add(str(v))
        bg = r.get("background_jsonb") or {}
        if isinstance(bg, str):
            try:
                bg = json.loads(bg)
            except Exception:
                bg = {}
        if bg.get("conceptual_anchor"):
            out.add(str(bg["conceptual_anchor"]))
    return list(out)


def _methods_from_projects(rows: list[dict]) -> list[str]:
    out = set()
    for r in rows:
        mv = r.get("manipulation_variables_jsonb") or {}
        if isinstance(mv, str):
            try:
                mv = json.loads(mv)
            except Exception:
                mv = {}
        for k in ("independent_vars", "dependent_vars"):
            v = mv.get(k) or []
            if isinstance(v, list):
                for x in v:
                    out.add(str(x))
            elif v:
                out.add(str(v))
        mods = r.get("modalities_jsonb") or {}
        if isinstance(mods, str):
            try:
                mods = json.loads(mods)
            except Exception:
                mods = {}
        if mods:
            for v in mods.values() if isinstance(mods, dict) else []:
                if v:
                    out.add(str(v))
    return list(out)


def _authors_from_projects(rows: list[dict]) -> list[str]:
    out = set()
    for r in rows:
        bg = r.get("background_jsonb") or {}
        if isinstance(bg, str):
            try:
                bg = json.loads(bg)
            except Exception:
                bg = {}
        prior = bg.get("prior_studies") or []
        if isinstance(prior, list):
            for s in prior:
                a = (s or {}).get("first_author") if isinstance(s, dict) else None
                if a:
                    out.add(str(a))
                a = (s or {}).get("authors") if isinstance(s, dict) else None
                if isinstance(a, list):
                    for x in a[:3]:
                        out.add(str(x))
    return list(out)


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: profile_show.py <init>"}))
        return 2
    # csnl_research.projects.init is uppercase; normalize before query so
    # `/paper-interview bhl` and `/paper-interview BHL` are equivalent.
    init = sys.argv[1].strip().upper()
    if not init:
        print(json.dumps({"error": "empty researcher init"}))
        return 2
    load_env()
    sch = schema()
    rows = query(_PROJ_QUERY, (init,))
    if not rows:
        print(json.dumps({
            "researcher_id": init,
            "profile":       {"topics": [], "methods": [], "authors": [], "projects": []},
            "error":         "no_active_projects",
        }, ensure_ascii=False))
        return 0

    sid = str(uuid.uuid4())
    profile = {
        "topics":   _topics_from_projects(rows),
        "methods":  _methods_from_projects(rows),
        "authors":  _authors_from_projects(rows),
        "projects": [
            {
                "slug":  r.get("project_slug"),
                "title": r.get("title"),
                "phase": r.get("phase"),
                "last_updated_kst": r.get("last_updated_kst"),
            }
            for r in rows
        ],
    }

    # P14: also return the most-recent verified dim_preferences/chunk_mix
    # if any; the skill's Stage-1 confirmation can present them as the
    # draft the researcher tweaks.
    verified = query(
        f"SELECT dim_preferences, chunk_mix "
        f"FROM {sch}.archive_profile_verifications "
        f"WHERE researcher_id = %s AND dim_preferences IS NOT NULL "
        f"ORDER BY confirmed_at DESC LIMIT 1",
        (init,),
    )
    if verified:
        v = verified[0]
        def _j(x):
            if isinstance(x, str):
                try: return json.loads(x)
                except Exception: return None
            return x
        profile["dim_preferences"] = _j(v.get("dim_preferences"))
        profile["chunk_mix"]       = _j(v.get("chunk_mix"))
    # Reuse the most recent open session for this researcher if there is one;
    # otherwise open a new one.
    open_rows = query(
        f"SELECT session_id FROM {sch}.archive_interview_sessions "
        f"WHERE researcher_id = %s AND completed_at IS NULL "
        f"ORDER BY started_at DESC LIMIT 1",
        (init,),
    )
    if open_rows:
        sid = open_rows[0]["session_id"]
    else:
        exec_sql(
            f"INSERT INTO {sch}.archive_interview_sessions "
            f"(session_id, researcher_id, started_at, papers_seen, choice_counts) "
            f"VALUES (%s, %s, now()::text, 0, '{{}}'::jsonb)",
            (sid, init),
        )

    print(json.dumps({
        "researcher_id": init,
        "session_id":    sid,
        "profile":       profile,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
