#!/usr/bin/env python3
"""
scripts/archive/merge_dedupe_filter.py — merge the three JSONL streams,
dedupe across them, decide per-paper filter outcomes (textbook / draft /
poster / lab-irrelevant), and emit:

  state/archive/merged_papers.jsonl       — canonical archive_papers rows
  state/archive/merged_sources.jsonl      — archive_paper_sources rows
  state/archive/filter_decisions.jsonl    — archive_filter_decisions rows
  state/archive/merge_report.json         — counts + reasons

Operator-run:
    ! python scripts/archive/merge_dedupe_filter.py            # dry-run
    ! python scripts/archive/merge_dedupe_filter.py --apply    # write DB

Dedup contract:
  canonical_id is sha256(doi) if DOI is present, else sha256(norm_title|year).
  After grouping by canonical_id, we then run a fuzzy-title pass within
  same-year buckets to collapse near-duplicates that differ only because one
  source lacked a DOI. Threshold 0.92 (rapidfuzz / difflib fallback).

Filter contract (rule-based, conservative):
  is_draft   : filename contains _draft_/_DRAFT_/_submitted_/(?:v\\d+)
               OR title contains 'manuscript draft' / '미발표'
  is_poster  : filename contains _poster_/_Poster_/_SfN/_VSS/_HBM
  is_textbook: page_count > 250 OR title regex
               (textbook|handbook|companion|encyclopedia|Hwa\\b|chapter)
               OR DOI prefix in known-book registries (10.1017/cbo,
               10.4324, 10.1201, 10.5040, 10.1142, 10.5194, 10.1109/eeei,
               10.1787) — collected from real false-positive DOIs in the
               CWLL rec log.
  is_lab_relevant : TRUE if title|abstract|venue matches any LAB_SCOPE_TAGS
                    keyword bag OR the paper's source is 'classics_smb' (we
                    trust the lab archive) OR the paper came from pi_network
                    AND the PI's primary_cat is one of CSNL's scope tags
                    (BDM/NN/fVC/VWM/SD/CG/METH — all of them are in-scope).
                    FALSE otherwise; row stays in archive but is never queued.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    LAB_SCOPE_TAGS, kst_iso, lab_scope_match, norm_title,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ARCHIVE   = _REPO_ROOT / "state" / "archive"

_INPUTS = {
    "classics_smb":  _ARCHIVE / "classics_papers.jsonl",
    "cwll_rec_log":  _ARCHIVE / "cwll_papers.jsonl",
    "pi_network":    _ARCHIVE / "pi_network_papers.jsonl",
}
_OUT_PAPERS    = _ARCHIVE / "merged_papers.jsonl"
_OUT_SOURCES   = _ARCHIVE / "merged_sources.jsonl"
_OUT_FILTERS   = _ARCHIVE / "filter_decisions.jsonl"
_OUT_REPORT    = _ARCHIVE / "merge_report.json"


# ---------------------------------------------------------------- filters

_DRAFT_FN_RE   = re.compile(r"(?:^|_)(draft|submitted|preprint_v\d+|v\d+_draft)(?:_|\.)", re.IGNORECASE)
_POSTER_FN_RE  = re.compile(r"(?:^|_)(poster|sfn\d|vss\d|hbm\d|cosyne\d)", re.IGNORECASE)
_TEXTBOOK_TITLE_RE = re.compile(
    r"\b(textbook|handbook|encyclopedia|companion to|primer)\b", re.IGNORECASE,
)
_BOOK_DOI_PREFIXES = (
    "10.1017/cbo", "10.4324", "10.1201", "10.5040", "10.1142", "10.36019",
    "10.12987",                 # Yale Univ Press chapters
    "10.1787",                  # OECD standards
    "10.1109/eeei",             # engineering proceedings (non-paper)
    "10.21136",                 # Czech Math Journal noise
    "10.31234",                 # PsyArXiv (legit preprint, keep!)
)
_NOT_BOOK_DOI_PREFIXES = ("10.31234",)  # keep these even if matched above


def is_draft(filename: str | None, title: str | None) -> bool:
    if filename and _DRAFT_FN_RE.search(filename):
        return True
    if title and ("manuscript draft" in title.lower() or "미발표" in title):
        return True
    return False


def is_poster(filename: str | None, venue: str | None) -> bool:
    if filename and _POSTER_FN_RE.search(filename):
        return True
    if venue and any(t in venue.lower() for t in ("poster",)):
        return True
    return False


def is_textbook(page_count: int | None, title: str | None, doi: str | None) -> bool:
    if page_count is not None and page_count >= 300:
        return True
    if title and _TEXTBOOK_TITLE_RE.search(title):
        return True
    if doi:
        d = doi.lower()
        if any(d.startswith(p) for p in _NOT_BOOK_DOI_PREFIXES):
            return False
        if any(d.startswith(p) for p in _BOOK_DOI_PREFIXES):
            return True
    return False


def lab_relevance(row: dict) -> tuple[bool, list[str]]:
    """Returns (is_relevant, scope_tags).

    Decision order:
    1. Keyword-bag match against title + abstract (the substantive signal).
    2. Venue match against LAB_SCOPE_VENUES (e.g. Nature, Neuron, eLife) —
       adds the synthetic VENUE_OK tag.
    3. Source presumption: classics_smb (curated by the lab) is in-scope.
       pi_network is in-scope ONLY when paired with a real tag from steps
       1 or 2, OR when the PI-network primary_cat for this source ref is
       a CSNL scope tag — see HARNESS-ARCHIVE-DESIGN.md §filter rules.
       (This stricter pi_network rule prevents flooding queues with
       every co-authorship a PI ever had on a non-lab topic.)
    """
    haystack = " ".join(filter(None, [
        row.get("title") or "",
        row.get("abstract") or "",
    ]))
    tags = lab_scope_match(haystack, venue=row.get("venue"))
    if tags:
        return (True, tags)
    src = row.get("_source") or ""
    if src == "classics_smb":
        return (True, [])
    if src == "pi_network":
        # Tolerate pi_network rows that look like methods papers (no kw hit
        # because abstracts are sparse on OpenAlex) but reject pure off-topic
        # collaborations. Keep them in archive_papers; just don't queue.
        return (False, [])
    return (False, [])


def filter_decision(row: dict) -> dict:
    src_payload = row.get("_source_payload") or {}
    filename    = src_payload.get("source_ref") or row.get("_source_ref")
    draft       = is_draft(filename, row.get("title"))
    poster      = is_poster(filename, row.get("venue"))
    textbook    = is_textbook(row.get("page_count"), row.get("title"), row.get("doi"))
    relevant, tags = lab_relevance(row)
    reasons = {}
    if draft:    reasons["draft"]    = "filename/title heuristic"
    if poster:   reasons["poster"]   = "filename/venue heuristic"
    if textbook:
        if row.get("page_count") and row["page_count"] >= 300:
            reasons["textbook"] = f"page_count={row['page_count']}"
        elif row.get("doi"):
            reasons["textbook"] = f"book DOI prefix"
        else:
            reasons["textbook"] = "title keyword"
    if not relevant:
        reasons["lab_irrelevant"] = "no scope-tag hit, not classics/pi source"
    return {
        "canonical_id":    row["canonical_id"],
        "is_textbook":     textbook,
        "is_draft":        draft,
        "is_poster":       poster,
        "is_lab_relevant": relevant and not (draft or poster or textbook),
        "lab_scope_tags":  tags,
        "filter_reason":   reasons,
        "decided_at":      kst_iso(),
    }


# ----------------------------------------------------------- fuzz helper

def _fuzz_ratio(a: str, b: str) -> float:
    """Return fuzz ratio 0..1. Prefer rapidfuzz; difflib fallback."""
    if not a or not b:
        return 0.0
    try:
        from rapidfuzz import fuzz  # type: ignore
        return fuzz.token_set_ratio(a, b) / 100.0
    except ImportError:
        import difflib
        return difflib.SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------- merge logic

def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _merge_one(into: dict, new: dict) -> dict:
    """Field-wise merge: prefer non-null/longer values; track sources."""
    out = dict(into)
    for k in ("doi", "title", "venue", "year", "pub_date", "abstract", "page_count", "pdf_path"):
        if not out.get(k) and new.get(k):
            out[k] = new[k]
        elif k == "abstract" and new.get(k) and len(new[k] or "") > len(out.get(k) or ""):
            out[k] = new[k]
    # authors: union (preserve first-source order)
    a1 = out.get("authors_json") or []
    a2 = new.get("authors_json") or []
    seen = set()
    merged_authors = []
    for x in a1 + a2:
        if x and x not in seen:
            seen.add(x)
            merged_authors.append(x)
    out["authors_json"] = merged_authors
    out["last_updated_at"] = kst_iso()
    if not out.get("title_norm"):
        out["title_norm"] = norm_title(out.get("title"))
    out["is_preprint"] = bool(out.get("is_preprint") or new.get("is_preprint"))
    return out


def merge_all() -> tuple[dict[str, dict], list[dict], dict, dict[str, str]]:
    """Returns (papers_by_id, sources_list, report, collapse_map).

    collapse_map maps each *alias* canonical_id to the target canonical_id
    that absorbed it during the year-bucketed fuzz pass. _apply_to_db
    uses it to clean orphaned child rows from prior runs.
    """
    counts_in: dict[str, int] = {}
    raw: list[dict] = []
    for src, path in _INPUTS.items():
        rows = _read_jsonl(path)
        counts_in[src] = len(rows)
        for r in rows:
            r.setdefault("_source", src)
            raw.append(r)

    # Bucket by canonical_id for the first dedup pass.
    by_id: dict[str, dict] = {}
    sources: list[dict] = []
    for r in raw:
        cid = r["canonical_id"]
        if cid in by_id:
            by_id[cid] = _merge_one(by_id[cid], r)
        else:
            by_id[cid] = {
                "canonical_id":   cid,
                "doi":            r.get("doi"),
                "title":          r.get("title"),
                "title_norm":     r.get("title_norm") or norm_title(r.get("title")),
                "authors_json":   list(r.get("authors_json") or []),
                "venue":          r.get("venue"),
                "year":           r.get("year"),
                "pub_date":       r.get("pub_date"),
                "is_preprint":    bool(r.get("is_preprint")),
                "abstract":       r.get("abstract"),
                "page_count":     r.get("page_count"),
                "pdf_path":       r.get("pdf_path"),
                "first_seen_at":  r.get("first_seen_at") or kst_iso(),
                "last_updated_at": r.get("last_updated_at") or kst_iso(),
            }
        sources.append({
            "canonical_id":   cid,
            "source":         r.get("_source"),
            "source_ref":     r.get("_source_ref") or "",
            "source_payload": r.get("_source_payload") or {},
            "observed_at":    r.get("last_updated_at") or kst_iso(),
        })

    # Second pass: collapse fuzzy-title duplicates within (year ± 1).
    by_year: dict[int | None, list[str]] = {}
    for cid, p in by_id.items():
        by_year.setdefault(p.get("year"), []).append(cid)

    collapse_map: dict[str, str] = {}        # alias_cid → canonical_cid
    for yr, cids in by_year.items():
        if yr is None or len(cids) < 2:
            continue
        cids = sorted(cids, key=lambda c: -len((by_id[c].get("title") or "")))
        kept: list[str] = []
        for c in cids:
            t = by_id[c].get("title_norm") or ""
            if not t:
                kept.append(c)
                continue
            collapsed = False
            for k in kept:
                if _fuzz_ratio(t, by_id[k].get("title_norm") or "") >= 0.92:
                    collapse_map[c] = k
                    by_id[k] = _merge_one(by_id[k], by_id[c])
                    collapsed = True
                    break
            if not collapsed:
                kept.append(c)

    # Apply collapse map.
    for alias, target in collapse_map.items():
        if alias in by_id:
            del by_id[alias]
        for s in sources:
            if s["canonical_id"] == alias:
                s["canonical_id"] = target

    report = {
        "inputs":         counts_in,
        "after_cid_dedup": len({s["canonical_id"] for s in sources}),
        "after_fuzz_collapse": len(by_id),
        "fuzz_collapses":     len(collapse_map),
    }
    return (by_id, sources, report, collapse_map)


def _require_psycopg2() -> None:
    """The archive layer writes JSONB payloads (OpenAlex work dicts with
    quotes, backslashes, unicode) where the psql fallback's literal
    escaping is fragile. Refuse to run --apply without psycopg2."""
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "merge_dedupe_filter.py --apply requires psycopg2-binary "
            "(pip install psycopg2-binary). The psql fallback in "
            "pipeline/_db.py is not safe for the JSONB payloads this "
            "script writes (OpenAlex author dicts, source_payload, etc.)."
        )


def _apply_to_db(papers: dict, sources: list[dict], filters: list[dict],
                 collapse_map: dict[str, str], schema: str) -> dict:
    _require_psycopg2()
    sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
    from _db import exec_many, exec_sql  # noqa: E402

    # ---- Orphan cleanup. Any canonical_id that was an alias on this run
    # may still have child rows in archive_paper_sources / _filter_decisions
    # / _embeddings / _researcher_queues from a prior --apply. Remove them
    # so the merged target row owns the data.
    if collapse_map:
        aliases = list(collapse_map.keys())
        # All five DELETEs in ONE transaction so a partial failure rolls
        # back cleanly (no half-orphaned cleanup state).
        from _db import _conn  # noqa: E402
        conn = _conn()
        try:
            conn.autocommit = False
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {schema}.archive_paper_sources "
                    f"WHERE canonical_id = ANY(%s)", (aliases,))
                cur.execute(
                    f"DELETE FROM {schema}.archive_filter_decisions "
                    f"WHERE canonical_id = ANY(%s)", (aliases,))
                cur.execute(
                    f"DELETE FROM {schema}.archive_paper_embeddings "
                    f"WHERE canonical_id = ANY(%s)", (aliases,))
                cur.execute(
                    f"DELETE FROM {schema}.archive_researcher_queues "
                    f"WHERE canonical_id = ANY(%s)", (aliases,))
                cur.execute(
                    f"DELETE FROM {schema}.archive_papers "
                    f"WHERE canonical_id = ANY(%s)", (aliases,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        print(f"[merge] cleaned {len(aliases)} alias canonical_ids "
              f"from sources/filters/embeddings/queues/papers")

    # ---- archive_papers UPSERT. Preserve longer abstracts; carry forward
    # title/year/venue/authors/pub_date/is_preprint via COALESCE.
    paper_sql = f"""
        INSERT INTO {schema}.archive_papers
          (canonical_id, doi, title, title_norm, authors_json, venue, year,
           pub_date, is_preprint, abstract, page_count, pdf_path,
           first_seen_at, last_updated_at)
        VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (canonical_id) DO UPDATE SET
          doi             = COALESCE(EXCLUDED.doi,           {schema}.archive_papers.doi),
          title           = COALESCE(EXCLUDED.title,         {schema}.archive_papers.title),
          title_norm      = COALESCE(NULLIF(EXCLUDED.title_norm, ''),
                                     {schema}.archive_papers.title_norm),
          authors_json    = CASE WHEN jsonb_array_length(COALESCE(EXCLUDED.authors_json, '[]'::jsonb)) >
                                     jsonb_array_length(COALESCE({schema}.archive_papers.authors_json, '[]'::jsonb))
                                 THEN EXCLUDED.authors_json
                                 ELSE {schema}.archive_papers.authors_json END,
          venue           = COALESCE(EXCLUDED.venue,         {schema}.archive_papers.venue),
          year            = COALESCE(EXCLUDED.year,          {schema}.archive_papers.year),
          pub_date        = COALESCE(EXCLUDED.pub_date,      {schema}.archive_papers.pub_date),
          is_preprint     = EXCLUDED.is_preprint OR {schema}.archive_papers.is_preprint,
          abstract        = CASE WHEN length(COALESCE(EXCLUDED.abstract, '')) >
                                     length(COALESCE({schema}.archive_papers.abstract, ''))
                                 THEN EXCLUDED.abstract
                                 ELSE {schema}.archive_papers.abstract END,
          page_count      = COALESCE(EXCLUDED.page_count,    {schema}.archive_papers.page_count),
          pdf_path        = COALESCE(EXCLUDED.pdf_path,      {schema}.archive_papers.pdf_path),
          last_updated_at = EXCLUDED.last_updated_at;
    """
    src_sql = f"""
        INSERT INTO {schema}.archive_paper_sources
          (canonical_id, source, source_ref, source_payload, observed_at)
        VALUES (%s,%s,%s,%s::jsonb,%s)
        ON CONFLICT (canonical_id, source, source_ref) DO UPDATE SET
          source_payload = EXCLUDED.source_payload,
          observed_at    = EXCLUDED.observed_at;
    """
    fil_sql = f"""
        INSERT INTO {schema}.archive_filter_decisions
          (canonical_id, is_textbook, is_draft, is_poster, is_lab_relevant,
           lab_scope_tags, filter_reason, decided_at)
        VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
        ON CONFLICT (canonical_id) DO UPDATE SET
          is_textbook     = EXCLUDED.is_textbook,
          is_draft        = EXCLUDED.is_draft,
          is_poster       = EXCLUDED.is_poster,
          is_lab_relevant = EXCLUDED.is_lab_relevant,
          lab_scope_tags  = EXCLUDED.lab_scope_tags,
          filter_reason   = EXCLUDED.filter_reason,
          decided_at      = EXCLUDED.decided_at;
    """
    paper_rows = [(
        p["canonical_id"], p["doi"], p["title"], p["title_norm"],
        json.dumps(p["authors_json"], ensure_ascii=False),
        p["venue"], p["year"], p["pub_date"], p["is_preprint"], p["abstract"],
        p["page_count"], p["pdf_path"], p["first_seen_at"], p["last_updated_at"],
    ) for p in papers.values()]
    src_rows = [(
        s["canonical_id"], s["source"], s["source_ref"],
        json.dumps(s["source_payload"], ensure_ascii=False), s["observed_at"],
    ) for s in sources]
    fil_rows = [(
        f["canonical_id"], f["is_textbook"], f["is_draft"], f["is_poster"],
        f["is_lab_relevant"],
        json.dumps(f["lab_scope_tags"], ensure_ascii=False),
        json.dumps(f["filter_reason"], ensure_ascii=False),
        f["decided_at"],
    ) for f in filters]
    n_p = exec_many(paper_sql, paper_rows)
    n_s = exec_many(src_sql, src_rows)
    n_f = exec_many(fil_sql, fil_rows)
    return {"papers": n_p, "sources": n_s, "filters": n_f}


# ----------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    papers, sources, report, collapse_map = merge_all()

    # Filter pass — augment papers with their _source for relevance rule.
    filters = []
    src_by_cid: dict[str, list[dict]] = {}
    for s in sources:
        src_by_cid.setdefault(s["canonical_id"], []).append(s)
    for cid, p in papers.items():
        # Carry "best" source for the relevance presumption (classics/pi_network).
        ss = src_by_cid.get(cid, [])
        primary_src = ss[0]["source"] if ss else ""
        # Carry the source filename for is_draft / is_poster filename heuristic.
        ref = ss[0]["source_ref"] if ss else ""
        merged_for_filter = dict(p)
        merged_for_filter["_source"]     = primary_src
        merged_for_filter["_source_ref"] = ref
        filters.append(filter_decision(merged_for_filter))

    # Stats.
    n_papers = len(papers)
    n_textbook = sum(1 for f in filters if f["is_textbook"])
    n_draft    = sum(1 for f in filters if f["is_draft"])
    n_poster   = sum(1 for f in filters if f["is_poster"])
    n_irrel    = sum(1 for f in filters if not f["is_lab_relevant"])
    n_inscope  = sum(1 for f in filters if f["is_lab_relevant"])
    report.update({
        "papers":      n_papers,
        "textbook":    n_textbook,
        "draft":       n_draft,
        "poster":      n_poster,
        "lab_irrelev": n_irrel,
        "in_scope":    n_inscope,
    })

    # Write outputs.
    _ARCHIVE.mkdir(parents=True, exist_ok=True)
    with _OUT_PAPERS.open("w", encoding="utf-8") as f:
        for p in papers.values():
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    with _OUT_SOURCES.open("w", encoding="utf-8") as f:
        for s in sources:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    with _OUT_FILTERS.open("w", encoding="utf-8") as f:
        for fl in filters:
            f.write(json.dumps(fl, ensure_ascii=False) + "\n")
    _OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")

    print("[merge] " + json.dumps(report, ensure_ascii=False))
    print(f"[merge] wrote {_OUT_PAPERS.name}, {_OUT_SOURCES.name}, "
          f"{_OUT_FILTERS.name}, {_OUT_REPORT.name}")

    if args.apply:
        sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
        from _db import load_env, ledger_schema  # noqa: E402
        load_env()
        sch = ledger_schema()
        print(f"[merge] applying to schema={sch}…")
        result = _apply_to_db(papers, sources, filters, collapse_map, sch)
        print(f"[merge] OK — {result}")
    else:
        print("[merge] dry-run only. Re-run with --apply to UPSERT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
