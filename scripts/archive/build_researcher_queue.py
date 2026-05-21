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
_TAXONOMY_PATH = _REPO_ROOT / "state" / "archive" / "taxonomy.json"

CHUNK_BOUNDS = {"recent": (0, 5), "mid": (5, 10), "classic": (10, 999)}
_DEFAULT_CHUNK_MIX = {"recent": 120, "mid": 60, "classic": 20}

# Composite ranking weights — see HARNESS-ARCHIVE-DESIGN.md §P14 §4.
# composite(p,r) = W_COS·cos + W_DIM·dim_score + min(MAX_COMBO_BONUS,
#                                                    COMBO_STEP·n_combos)
W_COS, W_DIM       = 0.55, 0.30
COMBO_STEP         = 0.05
MAX_COMBO_BONUS    = 0.15
COS_FLOOR          = 0.18   # under-threshold cosine cannot be rescued by dim score
TIER_S_COS, TIER_S_DIM = 0.40, 0.50
TIER_A_COS_HIGH, TIER_A_DIM_HIGH = 0.40, 0.30
TIER_A_COS_MID,  TIER_A_DIM_MID  = 0.30, 0.60
TIER_B_COS                       = 0.30


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


# ---------------------------------------------------------- P14 helpers

def _load_taxonomy() -> dict | None:
    if not _TAXONOMY_PATH.exists():
        return None
    return json.loads(_TAXONOMY_PATH.read_text("utf-8"))


def _load_paper_dim_tags() -> dict[str, dict[str, list[str]]]:
    """Return {canonical_id: {dim: [cat_code, ...]}}.

    Prefers state/archive/filter_decisions_dim.jsonl (denorm mirror) if
    present; falls back to a DB query against archive_paper_dim_tags.
    """
    mirror = _REPO_ROOT / "state" / "archive" / "filter_decisions_dim.jsonl"
    out: dict[str, dict[str, list[str]]] = {}
    if mirror.exists():
        for r in _iter_jsonl(mirror):
            out[r["canonical_id"]] = r.get("dim_tags", {}) or {}
        return out
    # DB fallback
    sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
    from _db import query_json  # noqa: E402
    rows = query_json(
        "SELECT canonical_id, dimension, category_code "
        "FROM csnl_paper_rec.archive_paper_dim_tags "
        "ORDER BY canonical_id, dimension, strength DESC"
    )
    for r in rows:
        d = out.setdefault(r["canonical_id"], {})
        d.setdefault(r["dimension"], []).append(r["category_code"])
    return out


def _load_paper_lab_tags() -> dict[str, list[str]]:
    """Return {canonical_id: [BDM, SD, ...]} from archive_filter_decisions."""
    mirror = _REPO_ROOT / "state" / "archive" / "filter_decisions.jsonl"
    out: dict[str, list[str]] = {}
    if mirror.exists():
        for r in _iter_jsonl(mirror):
            tags = r.get("lab_scope_tags") or []
            out[r["canonical_id"]] = list(tags)
        return out
    sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
    from _db import query_json  # noqa: E402
    rows = query_json(
        "SELECT canonical_id, lab_scope_tags "
        "FROM csnl_paper_rec.archive_filter_decisions"
    )
    for r in rows:
        t = r.get("lab_scope_tags")
        if isinstance(t, str):
            try:
                t = json.loads(t)
            except Exception:
                t = []
        out[r["canonical_id"]] = list(t or [])
    return out


def _load_latest_profile(init: str) -> dict:
    """Return latest archive_profile_verifications row for init (or {})."""
    sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
    from _db import query_json  # noqa: E402
    rows = query_json(
        "SELECT dim_preferences, chunk_mix "
        "FROM csnl_paper_rec.archive_profile_verifications "
        f"WHERE researcher_id = '{init}' "
        "ORDER BY confirmed_at DESC LIMIT 1"
    )
    if not rows:
        return {}
    out = {}
    for k in ("dim_preferences", "chunk_mix"):
        v = rows[0].get(k)
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                v = None
        if v:
            out[k] = v
    return out


def _derive_dim_prefs(interest_text: str, taxonomy: dict) -> dict:
    """Substring-match the researcher's interest text against the taxonomy
    keyword bags (same matcher tag_dimensions.py uses).

    Returns a dim_preferences dict shaped like the verified profile column.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from tag_dimensions import tag_paper  # noqa: E402
    fake_paper = {"title": interest_text, "abstract": "", "venue": ""}
    tags = tag_paper(fake_paper, taxonomy)
    prefs = {"focus": {}, "method": {}, "stim": {}, "subj": {},
             "combo_bonus": [],
             "source": "auto", "version": 1}
    for dim, scored in tags.items():
        if not scored:
            continue
        # Normalize so the strongest cat per dim gets 1.0.
        m = max(s for _, s, _ in scored) or 1.0
        for code, s, _hits in scored:
            prefs[dim][code] = round(s / m, 3)
    return prefs


def _dim_score(paper_dims: dict[str, list[str]], prefs: dict) -> float:
    """0..1 — average over *researcher-populated dims* of the preference
    weight on any cat the paper carries in that dim.

    The architect's original spec divided by 4 (all dimensions). In
    practice the auto-derive pass often only fills `focus` from a
    researcher's high-level project text, so dividing by 4 caps dim_score
    at 0.25 and prevents any S-tier eligibility. Averaging only over dims
    the researcher actually has weights in fixes the cold-start while
    keeping the strictness for researchers who specified all 4 dims at
    Stage-1.
    """
    contributions: list[float] = []
    for dim in ("focus", "method", "stim", "subj"):
        weights = prefs.get(dim) or {}
        if not weights:
            continue
        cats = paper_dims.get(dim) or []
        if not cats:
            contributions.append(0.0)
            continue
        contributions.append(max(float(weights.get(c, 0.0)) for c in cats))
    if not contributions:
        return 0.0
    return sum(contributions) / len(contributions)


def _combo_hits(paper_dims: dict[str, list[str]], paper_lab: list[str],
                combos: list[dict], pref_codes: set[str] | None = None) -> list[str]:
    """Return the list of relevance combo ids satisfied by the paper.

    Constraints:
      - combo.role == 'guard' is excluded (e.g. CLN_GUARD is a consistency
        check, not a relevance bonus).
      - A combo's codes must all appear in the paper's dim_tags or
        lab_scope_tags.
      - If `pref_codes` is provided (the researcher's preferred codes
        across all dims), the combo fires only when at least one of its
        codes is in pref_codes. This prevents off-topic combos (e.g. a
        clinical paper boosting a perception researcher's queue).
    """
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
        if pref_codes is not None and not (set(codes) & pref_codes):
            continue
        out.append(c["id"])
    return out


def _pref_code_set(prefs: dict) -> set[str]:
    """Flatten the researcher's dim_preferences into a flat code set
    (codes with non-zero weight, across all dims, plus the lab-scope
    codes the researcher has implicitly asked about via combo_bonus)."""
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
    bonus = min(MAX_COMBO_BONUS, COMBO_STEP * n_combos)
    return W_COS * max(0.0, cos) + W_DIM * dim_score + bonus


def _tier(cos: float, dim_score: float, n_combos: int) -> str:
    if cos >= TIER_S_COS and dim_score >= TIER_S_DIM and n_combos >= 1:
        return "S"
    if (cos >= TIER_A_COS_HIGH and dim_score >= TIER_A_DIM_HIGH) \
       or (cos >= TIER_A_COS_MID and dim_score >= TIER_A_DIM_MID):
        return "A"
    if cos >= TIER_B_COS:
        return "B"
    return "C"


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

    # P14: load taxonomy + per-paper dim tags + lab-bucket tags. Missing
    # taxonomy falls back to legacy chunk×cosine-only ranking with no
    # composite score (tier='B' for everyone above the floor).
    taxonomy = _load_taxonomy()
    if taxonomy:
        paper_dims = _load_paper_dim_tags()
        paper_lab  = _load_paper_lab_tags()
        combos     = taxonomy.get("combos") or []
        print(f"[queue] taxonomy v={taxonomy.get('version')}  "
              f"papers_tagged={len(paper_dims)}  combos={len(combos)}")
    else:
        paper_dims, paper_lab, combos = {}, {}, []
        print("[queue] no taxonomy.json — running in legacy mode "
              "(no composite, no tier)")

    inits = [args.researcher] if args.researcher else _list_researchers()
    print(f"[queue] researchers={inits}  archive_papers={len(papers)}  "
          f"embedded={len(embeddings)}")

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone(timedelta(hours=9)))
    queue_rows_all: list[dict] = []
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

        # P14: load latest verified profile (dim_preferences + chunk_mix).
        # Auto-derive from interest text if not yet confirmed.
        profile = _load_latest_profile(init)
        dim_prefs = profile.get("dim_preferences")
        prefs_source = "verified"
        if not dim_prefs and taxonomy:
            dim_prefs = _derive_dim_prefs(text, taxonomy)
            prefs_source = dim_prefs.get("source", "auto")
        if not dim_prefs:
            dim_prefs = {"focus": {}, "method": {}, "stim": {}, "subj": {},
                         "combo_bonus": [], "source": "none", "version": 0}
        chunk_mix = profile.get("chunk_mix") or _DEFAULT_CHUNK_MIX
        # CLI --top overrides chunk_mix when explicitly bumped above the
        # max of the mix (operator's foot-gun escape hatch).
        if args.top and args.top > max(chunk_mix.values()):
            chunk_mix = {k: args.top for k in chunk_mix}
        print(f"[queue] {init}: prefs_source={prefs_source}  "
              f"chunk_mix={chunk_mix}  "
              f"focus_top={list(dim_prefs.get('focus', {}).keys())[:3]}")

        qv = backend.encode([text])[0]

        # Candidate set: in-scope, embedded, present in archive.
        cids = [c for c, f in filters.items() if f.get("is_lab_relevant", True)]
        cids = [c for c in cids if c in embeddings and c in papers]
        if not cids:
            print(f"[queue] {init}: no candidate papers (filter or embed missing)")
            continue
        mat = [embeddings[c] for c in cids]
        sims = _try_numpy_cosine(qv, mat)
        if sims is None:
            sims = [_cosine(qv, v) for v in mat]

        # Score every candidate.
        pref_codes = _pref_code_set(dim_prefs) if taxonomy else set()
        per_chunk: dict[str, list[dict]] = {"recent": [], "mid": [], "classic": []}
        for c, s in zip(cids, sims):
            cos = max(0.0, float(s))
            if cos < COS_FLOOR:
                continue   # cannot be rescued by dim score
            chunk = _chunk_for(papers[c] or {}, today)
            pdims = paper_dims.get(c, {})
            plab  = paper_lab.get(c, [])
            ds = _dim_score(pdims, dim_prefs) if taxonomy else 0.0
            chits = _combo_hits(pdims, plab, combos, pref_codes) if taxonomy else []
            comp = _composite(cos, ds, len(chits))
            tier = _tier(cos, ds, len(chits)) if taxonomy else "B"
            per_chunk[chunk].append({
                "canonical_id":  c,
                "similarity":    cos,
                "dim_score":     ds,
                "combos":        chits,
                "composite":     comp,
                "tier":          tier,
                "dim_match":     {dim: pdims.get(dim) or [] for dim in
                                  ("focus", "method", "stim", "subj")},
            })

        rows_out: list[dict] = []
        token = str(uuid.uuid4())
        built_at = kst_iso()
        build_tokens[init] = token
        for chunk, scored in per_chunk.items():
            scored.sort(key=lambda x: (-x["composite"], -x["similarity"]))
            n = int(chunk_mix.get(chunk, _DEFAULT_CHUNK_MIX[chunk]))
            for rank, cand in enumerate(scored[:n], start=1):
                rows_out.append({
                    "researcher_id": init,
                    "canonical_id":  cand["canonical_id"],
                    "chunk":         chunk,
                    "rank_in_chunk": rank,
                    "similarity":    cand["similarity"],
                    "composite":     cand["composite"],
                    "tier":          cand["tier"],
                    "dim_match":     {
                        "matched": cand["dim_match"],
                        "combos":  cand["combos"],
                        "tier":    cand["tier"],
                    },
                    "built_at":      built_at,
                    "build_token":   token,
                })
        out_path = _OUT_DIR / f"{init}.jsonl"
        with out_path.open("w", encoding="utf-8") as f:
            for r in rows_out:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        # Per-researcher tier breakdown for the operator's log.
        tier_count = {"S": 0, "A": 0, "B": 0, "C": 0}
        for r in rows_out:
            tier_count[r["tier"]] = tier_count.get(r["tier"], 0) + 1
        print(f"[queue] {init}: rows={len(rows_out)}  tiers={tier_count}  "
              f"→ {out_path.name}")
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
               similarity, built_at, build_token,
               tier, composite, dim_match)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT (researcher_id, canonical_id) DO UPDATE SET
              chunk          = EXCLUDED.chunk,
              rank_in_chunk  = EXCLUDED.rank_in_chunk,
              similarity     = EXCLUDED.similarity,
              built_at       = EXCLUDED.built_at,
              build_token    = EXCLUDED.build_token,
              tier           = EXCLUDED.tier,
              composite      = EXCLUDED.composite,
              dim_match      = EXCLUDED.dim_match;
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
                         r["build_token"],
                         r["tier"], r["composite"],
                         json.dumps(r["dim_match"], ensure_ascii=False))
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
