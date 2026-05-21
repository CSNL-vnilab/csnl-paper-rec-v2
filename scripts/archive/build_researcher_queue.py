#!/usr/bin/env python3
"""
scripts/archive/build_researcher_queue.py — per-researcher pre-computed
ranked recommendation queue (top-N, split into 3 age chunks).

Operator-run, per researcher (or batch via --all):
    ! python scripts/archive/build_researcher_queue.py BHL
    ! python scripts/archive/build_researcher_queue.py BHL --top 300 --apply
    ! python scripts/archive/build_researcher_queue.py --all --apply

Pipeline:
  1. Read csnl_research.projects for the researcher's active projects
     (re-uses pipeline.00_select_projects path; READ-ONLY).
  2. Build a researcher query string from purpose/background/manipulation
     fields — pure concat, no LLM call. (Embedding model already handles
     semantic compression.)
  3. Embed the query with the SAME backend/model used for the archive
     (CSNL_EMBED_BACKEND + CSNL_EMBED_MODEL).
  4. Load archive_paper_embeddings (only is_lab_relevant=true), compute
     cosine similarity, split into recent/mid/classic (≤5y, 5–10y, >10y
     relative to today), rank within chunk, keep top-N per chunk.
  5. Emit state/archive/queues/<researcher>.jsonl + (optionally) UPSERT
     archive_researcher_queues.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import kst_iso  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_OUT_DIR   = _REPO_ROOT / "state" / "archive" / "queues"

CHUNK_BOUNDS = {"recent": (0, 5), "mid": (5, 10), "classic": (10, 999)}


# -------------------------------------------------------- researcher interest

_INTEREST_QUERY = """
SELECT init, project_slug, title, phase, confidence_avg,
       purpose_jsonb, background_jsonb, connected_graph_jsonb,
       manipulation_variables_jsonb, modalities_jsonb
FROM csnl_research.projects
WHERE init = %s
  AND phase IN ('data_collection','analysis','manuscript_draft')
  AND confidence_avg >= 0.7
ORDER BY project_slug
"""


def _interest_text_from_row(r: dict) -> str:
    """Concat the fields that capture *what* this researcher cares about."""
    parts: list[str] = []
    purpose = r.get("purpose") or r.get("purpose_jsonb") or {}
    bg      = r.get("background") or r.get("background_jsonb") or {}
    mv      = r.get("manipulation_variables") or r.get("manipulation_variables_jsonb") or {}
    cg      = r.get("connected_graph") or r.get("connected_graph_jsonb") or {}
    if purpose.get("research_question"):
        parts.append(str(purpose["research_question"]))
    if purpose.get("hypothesis"):
        parts.append(str(purpose["hypothesis"]))
    if bg.get("conceptual_anchor"):
        parts.append(str(bg["conceptual_anchor"]))
    for k in ("independent_vars", "dependent_vars"):
        v = mv.get(k)
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
        elif v:
            parts.append(str(v))
    if cg.get("shared_paradigm_with"):
        parts.append("paradigm: " + ", ".join(str(x) for x in cg["shared_paradigm_with"]))
    parts.append(str(r.get("title") or ""))
    return "\n".join(p for p in parts if p).strip()


def _fetch_researcher_projects(init: str) -> list[dict]:
    sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
    from _db import load_env  # noqa: E402
    load_env()
    try:
        import psycopg2
    except ImportError:
        # psql fallback
        import subprocess
        wrapped = (
            "SELECT coalesce(json_agg(t), '[]'::json) FROM ( " +
            _INTEREST_QUERY.replace("%s", f"'{init}'").rstrip().rstrip(";") +
            " ) t;"
        )
        proc = subprocess.run(
            ["psql",
             "-h", os.environ["SUPABASE_DB_HOST"],
             "-p", os.environ.get("SUPABASE_DB_PORT", "5432"),
             "-U", os.environ["SUPABASE_DB_USER"],
             "-d", os.environ.get("SUPABASE_DB_NAME", "postgres"),
             "-tAc", wrapped],
            capture_output=True, text=True,
            env=dict(os.environ, PGPASSWORD=os.environ["SUPABASE_DB_PASSWORD"]),
            timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip())
        return json.loads(proc.stdout.strip() or "[]")
    conn = psycopg2.connect(
        host=os.environ["SUPABASE_DB_HOST"],
        port=int(os.environ.get("SUPABASE_DB_PORT", "5432")),
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"),
        user=os.environ["SUPABASE_DB_USER"],
        password=os.environ["SUPABASE_DB_PASSWORD"],
        connect_timeout=15,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(_INTEREST_QUERY, (init,))
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def _list_researchers() -> list[str]:
    sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
    from _db import load_env, query_json  # noqa: E402
    load_env()
    rows = query_json(
        "SELECT DISTINCT init FROM csnl_research.projects "
        "WHERE phase IN ('data_collection','analysis','manuscript_draft') "
        "  AND confidence_avg >= 0.7 ORDER BY init"
    )
    return [r["init"] for r in rows]


# --------------------------------------------------------- embeddings

def _iter_jsonl(path: Path):
    """Line-iterator that splits on \\n only — see merge_dedupe_filter for
    the same defensive read pattern (U+2028 in OpenAlex abstracts breaks
    str.splitlines)."""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", newline="") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            if line.strip():
                yield json.loads(line)


def _load_archive_embeddings(model_name: str) -> dict[str, list[float]]:
    """Load embeddings for is_lab_relevant=true papers from JSONL (preferred
    for portability) or from DB if JSONL is missing."""
    jl = _REPO_ROOT / "state" / "archive" / "embeddings.jsonl"
    out: dict[str, list[float]] = {}
    if jl.exists():
        for r in _iter_jsonl(jl):
            if r.get("model_name") != model_name:
                continue
            out[r["canonical_id"]] = r["embedding_json"]
        return out
    # DB fallback
    sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
    from _db import load_env, query_json  # noqa: E402
    load_env()
    rows = query_json(
        "SELECT canonical_id, embedding_json::text AS ej FROM csnl_paper_rec.archive_paper_embeddings "
        f"WHERE model_name = '{model_name}'"
    )
    for r in rows:
        out[r["canonical_id"]] = json.loads(r["ej"])
    return out


def _load_filter_decisions() -> dict[str, dict]:
    jl = _REPO_ROOT / "state" / "archive" / "filter_decisions.jsonl"
    return {d["canonical_id"]: d for d in _iter_jsonl(jl)}


def _load_papers() -> dict[str, dict]:
    jl = _REPO_ROOT / "state" / "archive" / "merged_papers.jsonl"
    return {p["canonical_id"]: p for p in _iter_jsonl(jl)}


def _cosine(a: list[float], b: list[float]) -> float:
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return s / (na * nb)


def _try_numpy_cosine(query_vec: list[float], mat: list[list[float]]) -> list[float] | None:
    try:
        import numpy as np
    except ImportError:
        return None
    q = np.asarray(query_vec, dtype="float32")
    M = np.asarray(mat, dtype="float32")
    qn = np.linalg.norm(q)
    Mn = np.linalg.norm(M, axis=1)
    denom = np.where(Mn == 0, 1.0, Mn) * (qn if qn else 1.0)
    return (M @ q / denom).tolist()


# ---------------------------------------------------------------- chunking

def _chunk_for(paper: dict, today: datetime) -> str:
    """Pick chunk from year if known, else from pub_date prefix, else classic.

    Preprints with year=null but pub_date set should not all collapse into
    classic. Negative ages (in-press papers dated ahead) map to recent.
    """
    yr = paper.get("year")
    if yr is None:
        pd = paper.get("pub_date") or ""
        if isinstance(pd, str) and len(pd) >= 4 and pd[:4].isdigit():
            yr = int(pd[:4])
    if yr is None:
        return "classic"
    age = today.year - int(yr)
    if age < 0:
        age = 0
    for chunk, (lo, hi) in CHUNK_BOUNDS.items():
        if lo <= age < hi:
            return chunk
    return "classic"


# ----------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("researcher", nargs="?",
                    help="Researcher init (e.g. BHL). Required unless --all.")
    ap.add_argument("--all", action="store_true",
                    help="Build queues for every active researcher.")
    ap.add_argument("--top", type=int, default=120,
                    help="Top-N per chunk (default 120 → 360 papers/researcher).")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backend", default=os.environ.get("CSNL_EMBED_BACKEND", "local"),
                    choices=("local", "voyage", "jina", "openai"))
    ap.add_argument("--operator-approved-remote-embed", action="store_true",
                    dest="operator_approved_remote_embed",
                    help="Required when --backend is voyage/jina/openai. "
                         "Embedding the researcher's interest text via a "
                         "third-party API needs the same operator gate as "
                         "the archive embedding pass.")
    args = ap.parse_args()
    # Normalize researcher init.
    if args.researcher:
        args.researcher = args.researcher.strip().upper()

    if not args.researcher and not args.all:
        print("ERROR: pass a researcher init, or --all", file=sys.stderr)
        return 2

    # Import the embedding backend lazily so dry-runs of the script don't
    # require sentence-transformers to be installed.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from compute_embeddings import _make_backend, compose_text, _assert_remote_approved  # noqa: E402
    _assert_remote_approved(args.backend, args.operator_approved_remote_embed)
    backend = _make_backend(args.backend)
    model_name = getattr(backend, "model_name", args.backend)

    papers = _load_papers()
    filters = _load_filter_decisions()
    embeddings = _load_archive_embeddings(model_name)
    if not embeddings:
        print(f"ERROR: no embeddings found for model={model_name}. "
              f"Run compute_embeddings.py first.", file=sys.stderr)
        return 2

    inits = [args.researcher] if args.researcher else _list_researchers()
    print(f"[queue] researchers={inits}  archive_papers={len(papers)}  "
          f"embedded={len(embeddings)}")

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone(timedelta(hours=9)))
    queue_rows_all: list[dict] = []
    # One build_token PER (researcher × this builder invocation). Two
    # builders running in the same second won't collide because the token
    # is a fresh UUID, not a timestamp.
    build_tokens: dict[str, str] = {}

    for init in inits:
        rows = _fetch_researcher_projects(init)
        if not rows:
            print(f"[queue] {init}: no active projects — skipping")
            continue
        text = "\n\n".join(_interest_text_from_row(r) for r in rows)
        if not text.strip():
            print(f"[queue] {init}: empty interest text — skipping")
            continue
        qv = backend.encode([text])[0]

        # Score every embedded paper.
        cids = [c for c, f in filters.items() if f.get("is_lab_relevant", True)]
        cids = [c for c in cids if c in embeddings and c in papers]
        if not cids:
            print(f"[queue] {init}: no candidate papers (filter or embed missing)")
            continue
        mat = [embeddings[c] for c in cids]
        sims = _try_numpy_cosine(qv, mat)
        if sims is None:
            sims = [_cosine(qv, v) for v in mat]

        # Chunk + per-chunk top-N.
        per_chunk: dict[str, list[tuple[str, float]]] = {"recent": [], "mid": [], "classic": []}
        for c, s in zip(cids, sims):
            chunk = _chunk_for(papers[c] or {}, today)
            per_chunk[chunk].append((c, float(s)))
        rows_out = []
        token = str(uuid.uuid4())
        built_at = kst_iso()
        build_tokens[init] = token
        for chunk, scored in per_chunk.items():
            scored.sort(key=lambda x: -x[1])
            for rank, (c, s) in enumerate(scored[: args.top], start=1):
                rows_out.append({
                    "researcher_id": init,
                    "canonical_id":  c,
                    "chunk":         chunk,
                    "rank_in_chunk": rank,
                    "similarity":    s,
                    "built_at":      built_at,
                    "build_token":   token,
                })
        out_path = _OUT_DIR / f"{init}.jsonl"
        with out_path.open("w", encoding="utf-8") as f:
            for r in rows_out:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"[queue] {init}: rows={len(rows_out)}  → {out_path.name}")
        queue_rows_all.extend(rows_out)

    if args.apply and queue_rows_all:
        # Require psycopg2 — we need a real transaction (DELETE+INSERT or
        # MERGE-style cleanup of stale rows) so the plugin's pick_next.py
        # never observes a half-replaced queue.
        try:
            import psycopg2  # noqa: F401
        except ImportError:
            print("ERROR: build_researcher_queue.py --apply requires "
                  "psycopg2-binary; install it first.", file=sys.stderr)
            return 2
        sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
        from _db import load_env, ledger_schema, _conn  # noqa: E402
        load_env()
        sch = ledger_schema()
        rids = sorted({r["researcher_id"] for r in queue_rows_all})

        # Group rows by researcher so each (researcher's DELETE + INSERT)
        # lands in one transaction. pick_next.py sees either the old queue
        # or the new one — never an empty in-between.
        upsert_sql = f"""
            INSERT INTO {sch}.archive_researcher_queues
              (researcher_id, canonical_id, chunk, rank_in_chunk,
               similarity, built_at, build_token)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (researcher_id, canonical_id) DO UPDATE SET
              chunk          = EXCLUDED.chunk,
              rank_in_chunk  = EXCLUDED.rank_in_chunk,
              similarity     = EXCLUDED.similarity,
              built_at       = EXCLUDED.built_at,
              build_token    = EXCLUDED.build_token;
        """
        # Prune by build_token — guaranteed unique per build run (UUIDv4),
        # so two builds running in the same wall-clock second do not
        # accidentally retain each other's rows.
        prune_sql = f"""
            DELETE FROM {sch}.archive_researcher_queues
             WHERE researcher_id = %s
               AND (build_token IS NULL OR build_token <> %s);
        """
        n_total = 0
        for rid in rids:
            rows = [r for r in queue_rows_all if r["researcher_id"] == rid]
            token = build_tokens[rid]
            conn = _conn()
            try:
                conn.autocommit = False
                with conn.cursor() as cur:
                    # 1. UPSERT all current rows.
                    cur.executemany(upsert_sql, [
                        (r["researcher_id"], r["canonical_id"], r["chunk"],
                         r["rank_in_chunk"], r["similarity"], r["built_at"],
                         r["build_token"])
                        for r in rows
                    ])
                    # 2. Prune any stale rows from a previous build by token.
                    cur.execute(prune_sql, (rid, token))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            n_total += len(rows)
        print(f"[queue] transactional UPSERT: {n_total}  (researchers={len(rids)})")
    else:
        print("[queue] dry-run only. Re-run with --apply to write DB.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
