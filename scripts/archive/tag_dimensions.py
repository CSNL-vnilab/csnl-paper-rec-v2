#!/usr/bin/env python3
"""
scripts/archive/tag_dimensions.py — assign 4-dimension sub-tags to every
in-scope archive paper, deterministic / rule-based / no LLM keys.

Pipeline position: run AFTER merge_dedupe_filter.py and BEFORE
compute_embeddings.py. The tagger is read-only on archive_papers /
archive_filter_decisions and writes to archive_paper_dim_tags +
archive_filter_decisions.dim_tags.

Operator-run:
    ! python scripts/archive/tag_dimensions.py            # dry-run JSONL
    ! python scripts/archive/tag_dimensions.py --apply    # write DB
    ! python scripts/archive/tag_dimensions.py --rebuild-all --apply
        # TRUNCATE archive_paper_dim_tags first (taxonomy version upgrade only).

Algorithm (mirrors the design in HARNESS-ARCHIVE-DESIGN.md §P14):
  For each in-scope paper, against each dimension d in taxonomy:
    haystack = lower(title || ' ' || abstract || ' ' || venue)
    for (cat, kw_bag) in taxonomy.dimensions[d]:
        hits = sum(1 for kw in kw_bag if kw.lower() in haystack)
        strength = min(1.0, hits / sqrt(len(kw_bag)))
    keep cats with strength >= 0.15, top-3 per dimension.

  classics_smb-only papers with zero hits across all dims are left
  untagged (we don't manufacture dim claims the abstract doesn't support).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import kst_iso  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ARCHIVE   = _REPO_ROOT / "state" / "archive"
_TAXONOMY_PATH = _ARCHIVE / "taxonomy.json"
_IN_PAPERS    = _ARCHIVE / "merged_papers.jsonl"
_IN_FILTERS   = _ARCHIVE / "filter_decisions.jsonl"
_OUT_TAGS     = _ARCHIVE / "dim_tags.jsonl"
_OUT_MIRROR   = _ARCHIVE / "filter_decisions_dim.jsonl"
_OUT_REPORT   = _ARCHIVE / "dim_tag_report.json"

STRENGTH_THRESHOLD = 0.15
TOP_K_PER_DIM      = 3

# Word-boundary regex match for short or all-uppercase keywords. Plain
# substring is fine for multi-word phrases like "natural scene", but a
# 3-char acronym like "ANS" or "ERP" or a bare word like "rat" gets
# spurious hits ("ans" inside "scans"/"answer"/"Hans"; "rat" inside
# "rate"/"strategy"/"separate"). This pre-filter removes those.
def _make_matcher(kw: str):
    """Return a callable haystack→bool that decides whether `kw` matches.

    Strategy:
      - kw with whitespace (phrase) → simple substring on lowercased haystack.
      - kw of length ≤ 5 or all-uppercase (acronym) → regex with \\b boundaries
        on the *original-case* haystack (so 'ANS' matches 'ANS' but not 'Hans').
        Latin-letter boundaries; Korean kws pass through as plain substring.
      - everything else → lowercased substring.
    """
    if " " in kw:
        kw_l = kw.lower()
        return lambda h_low, _h_orig: kw_l in h_low
    # CJK / non-ASCII keyword — plain substring suffices.
    if any(ord(c) > 127 for c in kw):
        return lambda h_low, _h_orig: kw in h_low
    # Latin acronym or short word: require word boundaries on the *original*
    # haystack so casing carries discriminating power (RSA ≠ rsa elsewhere).
    if len(kw) <= 5 or kw.isupper():
        pat = re.compile(r"\b" + re.escape(kw) + r"\b")
        return lambda _h_low, h_orig: bool(pat.search(h_orig))
    kw_l = kw.lower()
    return lambda h_low, _h_orig: kw_l in h_low


# ----------------------------------------------------------------- IO

def _iter_jsonl(path: Path) -> Iterable[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", newline="") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            if line.strip():
                yield json.loads(line)


def load_taxonomy() -> dict:
    if not _TAXONOMY_PATH.exists():
        raise SystemExit(f"taxonomy not found: {_TAXONOMY_PATH}")
    return json.loads(_TAXONOMY_PATH.read_text("utf-8"))


# ----------------------------------------------------------------- tagger

def tag_paper(paper: dict, taxonomy: dict,
              _matcher_cache: dict | None = None) -> dict[str, list[tuple[str, float, list[str]]]]:
    """For one paper, return {dim: [(cat_code, strength, hit_keywords), ...]}.

    Dim entries are top-K=3 cats with strength >= threshold. Short / uppercase
    acronyms ('ANS', 'ERP', 'rat', 'IEM') match with word boundaries on the
    original-case haystack — phrases and lowercase words still use cheap
    lowercased substring.
    """
    title    = paper.get("title") or ""
    abstract = paper.get("abstract") or ""
    venue    = paper.get("venue") or ""
    h_orig = f"{title}\n{abstract}\n{venue}"
    h_low  = h_orig.lower()
    out: dict[str, list[tuple[str, float, list[str]]]] = {}
    cache = _matcher_cache if _matcher_cache is not None else {}
    for dim, cats in taxonomy["dimensions"].items():
        scored: list[tuple[str, float, list[str]]] = []
        for code, c in cats.items():
            kws = (c.get("kw") or []) + (c.get("kw_ko") or [])
            if not kws:
                continue
            hits: list[str] = []
            for kw in kws:
                m = cache.get(kw)
                if m is None:
                    m = _make_matcher(kw)
                    cache[kw] = m
                if m(h_low, h_orig):
                    hits.append(kw)
            if not hits:
                continue
            strength = min(1.0, len(hits) / math.sqrt(len(kws)))
            if strength >= STRENGTH_THRESHOLD:
                scored.append((code, round(strength, 3), hits[:6]))
        scored.sort(key=lambda x: -x[1])
        out[dim] = scored[:TOP_K_PER_DIM]
    return out


# ----------------------------------------------------------------- IO

def _read_in_scope_papers() -> list[dict]:
    """Yield merged_papers rows where filter_decisions.is_lab_relevant=true."""
    filters = {f["canonical_id"]: f for f in _iter_jsonl(_IN_FILTERS)}
    out = []
    for p in _iter_jsonl(_IN_PAPERS):
        fd = filters.get(p["canonical_id"])
        if fd is None or fd.get("is_lab_relevant", True):
            out.append(p)
    return out


def _papers_from_db() -> list[dict]:
    """Fallback when JSONL files are not present (operator on a fresh box)."""
    sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
    from _db import query_json
    return query_json("""
        SELECT p.canonical_id, p.title, p.abstract, p.venue
          FROM csnl_paper_rec.archive_papers p
          LEFT JOIN csnl_paper_rec.archive_filter_decisions f
            ON f.canonical_id = p.canonical_id
         WHERE coalesce(f.is_lab_relevant, TRUE) = TRUE
    """)


# ----------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="UPSERT into DB. Default: dry-run JSONL only.")
    ap.add_argument("--rebuild-all", action="store_true",
                    help="TRUNCATE archive_paper_dim_tags before applying. "
                         "Use ONLY when bumping taxonomy.json version.")
    ap.add_argument("--from-db", action="store_true",
                    help="Pull papers from archive_papers instead of JSONL "
                         "(use after upstream JSONLs were already applied).")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    taxonomy = load_taxonomy()
    version = taxonomy.get("version", "v1.unknown")
    print(f"[tag_dim] taxonomy={version}  dims={list(taxonomy['dimensions'].keys())}")

    if args.from_db or not _IN_PAPERS.exists():
        papers = _papers_from_db()
        print(f"[tag_dim] loaded {len(papers)} papers from DB (filter=in-scope)")
    else:
        papers = _read_in_scope_papers()
        print(f"[tag_dim] loaded {len(papers)} in-scope papers from JSONL")

    if args.limit:
        papers = papers[: args.limit]

    rows_norm: list[dict] = []   # for archive_paper_dim_tags
    mirror:     list[dict] = []   # for archive_filter_decisions.dim_tags
    tagged_at = kst_iso()
    n_untagged = 0
    n_tagged_by_dim = {"focus": 0, "method": 0, "stim": 0, "subj": 0}
    matcher_cache: dict = {}      # kw → callable
    t0 = time.time()
    for i, p in enumerate(papers, 1):
        cid = p["canonical_id"]
        tagged = tag_paper(p, taxonomy, _matcher_cache=matcher_cache)
        any_tag = False
        mirror_dims: dict[str, list[str]] = {}
        for dim, scored in tagged.items():
            mirror_dims[dim] = [code for code, _, _ in scored]
            if scored:
                n_tagged_by_dim[dim] += 1
                any_tag = True
                for code, strength, hits in scored:
                    rows_norm.append({
                        "canonical_id":   cid,
                        "dimension":      dim,
                        "category_code":  code,
                        "strength":       strength,
                        "match_evidence": {"hits": hits, "src": "title+abstract+venue"},
                        "tagged_at":      tagged_at,
                        "tagger_version": version,
                    })
        if not any_tag:
            n_untagged += 1
        mirror.append({
            "canonical_id": cid,
            "dim_tags":     mirror_dims,
        })
        if i % 1000 == 0:
            rate = i / max(time.time() - t0, 0.01)
            print(f"[tag_dim] {i}/{len(papers)}  rate={rate:.0f}/s")

    report = {
        "version":       version,
        "papers_total":  len(papers),
        "untagged":      n_untagged,
        "tagged_rows":   len(rows_norm),
        "tagged_by_dim": n_tagged_by_dim,
        "threshold":     STRENGTH_THRESHOLD,
        "top_k_per_dim": TOP_K_PER_DIM,
    }
    print("[tag_dim] " + json.dumps(report, ensure_ascii=False))

    _ARCHIVE.mkdir(parents=True, exist_ok=True)
    with _OUT_TAGS.open("w", encoding="utf-8") as f:
        for r in rows_norm:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with _OUT_MIRROR.open("w", encoding="utf-8") as f:
        for m in mirror:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    _OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(f"[tag_dim] wrote {_OUT_TAGS.name}, {_OUT_MIRROR.name}, {_OUT_REPORT.name}")

    if args.apply:
        sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
        from _db import load_env, ledger_schema, _conn  # noqa: E402
        try:
            import psycopg2  # noqa: F401
            import psycopg2.extras
        except ImportError:
            raise SystemExit("psycopg2-binary required for --apply")
        load_env()
        sch = ledger_schema()

        if args.rebuild_all:
            print(f"[tag_dim] --rebuild-all: TRUNCATE {sch}.archive_paper_dim_tags")
            conn = _conn()
            try:
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute(f"TRUNCATE {sch}.archive_paper_dim_tags")
            finally:
                conn.close()

        # 1. UPSERT the normalized rows in chunks.
        upsert_norm = f"""
            INSERT INTO {sch}.archive_paper_dim_tags
              (canonical_id, dimension, category_code, strength, match_evidence,
               tagged_at, tagger_version)
            VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s)
            ON CONFLICT (canonical_id, dimension, category_code) DO UPDATE SET
              strength       = EXCLUDED.strength,
              match_evidence = EXCLUDED.match_evidence,
              tagged_at      = EXCLUDED.tagged_at,
              tagger_version = EXCLUDED.tagger_version;
        """
        CHUNK = 500
        n_done = 0
        t1 = time.time()
        for i in range(0, len(rows_norm), CHUNK):
            batch = rows_norm[i : i + CHUNK]
            params = [(r["canonical_id"], r["dimension"], r["category_code"],
                       r["strength"],
                       json.dumps(r["match_evidence"], ensure_ascii=False),
                       r["tagged_at"], r["tagger_version"]) for r in batch]
            conn = _conn()
            try:
                conn.autocommit = False
                with conn.cursor() as cur:
                    psycopg2.extras.execute_batch(cur, upsert_norm, params, page_size=100)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            n_done += len(batch)
            if (i // CHUNK) % 5 == 0:
                rate = n_done / max(time.time() - t1, 0.01)
                print(f"[tag_dim:apply] norm {n_done}/{len(rows_norm)}  rate={rate:.0f}/s")
        print(f"[tag_dim] normalized UPSERTed: {n_done}")

        # 2. Delete any stale (cid, dim, cat) rows older than this tag run
        # — happens when a paper's title/abstract changed and dropped a cat.
        conn = _conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {sch}.archive_paper_dim_tags "
                    f"WHERE tagged_at <> %s AND tagger_version = %s",
                    (tagged_at, version),
                )
                stale = cur.rowcount
                if stale:
                    print(f"[tag_dim] pruned {stale} stale rows (same version, older tagged_at)")
        finally:
            conn.close()

        # 3. Update the JSONB mirror on archive_filter_decisions in chunks.
        mirror_sql = f"""
            UPDATE {sch}.archive_filter_decisions
               SET dim_tags = %s::jsonb
             WHERE canonical_id = %s;
        """
        m_done = 0
        for i in range(0, len(mirror), CHUNK):
            batch = mirror[i : i + CHUNK]
            params = [(json.dumps(m["dim_tags"], ensure_ascii=False), m["canonical_id"])
                      for m in batch]
            conn = _conn()
            try:
                conn.autocommit = False
                with conn.cursor() as cur:
                    psycopg2.extras.execute_batch(cur, mirror_sql, params, page_size=100)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            m_done += len(batch)
        print(f"[tag_dim] mirror UPDATEd: {m_done}")
    else:
        print("[tag_dim] dry-run only. Re-run with --apply to UPSERT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
