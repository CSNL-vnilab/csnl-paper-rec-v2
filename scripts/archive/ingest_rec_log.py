#!/usr/bin/env python3
"""
scripts/archive/ingest_rec_log.py — ingest the 7-year CWLL recommendation
log into the csnl_paper_archive layer.

Input: /Volumes/CSNL_new-1/Memory/CWLL/logs/metadata_log.csv (override with
--path). Each CSV row is one Weekly_LS_Letter PDF; the `dois` column is a
';'-separated list of DOIs that may include book chapters / standards /
non-paper artefacts (we will filter those in a separate merge pass).

Operator-run:
    ! python scripts/archive/ingest_rec_log.py             # dry-run JSONL
    ! python scripts/archive/ingest_rec_log.py --enrich    # also hit OpenAlex
    ! python scripts/archive/ingest_rec_log.py --apply     # write DB

Enrichment uses pipeline/crawl.mjs (keyless OpenAlex via Node) when --enrich
is passed. We only enrich DOIs we haven't seen in earlier runs (cached in
state/archive/openalex_cache.json).

Defaults are conservative: dry-run, no network, JSONL emitted for review.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    ArchivePaperRow, canonical_id, kst_iso, norm_doi, norm_title, write_jsonl,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_JSONL_OUT = _REPO_ROOT / "state" / "archive" / "cwll_papers.jsonl"
_CACHE     = _REPO_ROOT / "state" / "archive" / "openalex_cache.json"
_CRAWL_MJS = _REPO_ROOT / "pipeline" / "crawl.mjs"

DEFAULT_CSV = "/Volumes/CSNL_new-1/Memory/CWLL/logs/metadata_log.csv"


# ----------------------------------------------------- DOI parse from CSV

def _split_dois(cell: str) -> list[str]:
    if not cell:
        return []
    parts = [p.strip() for p in cell.replace(",", ";").split(";")]
    out = []
    seen = set()
    for p in parts:
        d = norm_doi(p)
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def read_csv(path: Path) -> list[dict]:
    """Yield {source_file, dois:[...]} rows. confirmed > 0 only."""
    rows = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            try:
                if int(r.get("confirmed") or 0) <= 0:
                    continue
            except ValueError:
                continue
            dois = _split_dois(r.get("dois") or "")
            rows.append({
                "source_file": r.get("source_file") or "",
                "blocks_found": r.get("blocks_found") or "",
                "review":       r.get("review") or "",
                "status":       r.get("status") or "",
                "dois":         dois,
            })
    return rows


# ------------------------------------------------ optional OpenAlex enrich

def _load_cache() -> dict:
    if _CACHE.exists():
        try:
            return json.loads(_CACHE.read_text("utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(c: dict) -> None:
    """Atomic write — temp file + os.replace — so Ctrl-C never corrupts."""
    _CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CACHE.with_suffix(_CACHE.suffix + ".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _CACHE)


# Sentinel used in cache: True means "OpenAlex returned 404 / definitely not
# present, do not retry"; the absence of a key means we never tried; None in
# the cache means a transient error (the caller does NOT persist that into
# the JSONL cache file — see _openalex_lookup_one and main()).
_NOT_FOUND = {"_openalex_status": "not_found"}


def _openalex_lookup_one(doi: str, *, timeout_s: int = 25) -> dict | None:
    """Single OpenAlex GET by DOI. Returns:
      - the OA work dict          (success)
      - `_NOT_FOUND`              (HTTP 404 — definitely missing)
      - None                      (transient — caller must NOT cache as miss)

    Honours 429 with exponential backoff. Sleeps 150ms between successful
    requests (call site enforces, not here).
    """
    import requests  # available per requirements.txt
    url = f"https://api.openalex.org/works/doi:{doi}?mailto=csnl@vnilab.local"
    headers = {"User-Agent": "csnl-paper-rec/2.1 (+archive ingest)"}
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=timeout_s, headers=headers)
        except Exception:
            time.sleep(1.0 * (attempt + 1))
            continue
        if r.status_code == 200:
            try:
                return r.json()
            except Exception:
                return None
        if r.status_code == 404:
            return _NOT_FOUND
        if r.status_code == 429 or 500 <= r.status_code < 600:
            time.sleep(2 ** attempt + 0.5)
            continue
        # Other 4xx (malformed DOI etc.) — caller can decide; treat as
        # transient so we don't poison the cache on a one-off bad row.
        return None
    return None


def _abstract_from_inverted(inv: dict | None) -> str | None:
    if not inv:
        return None
    out: list[str | None] = []
    for w, ps in inv.items():
        for p in ps:
            while len(out) <= p:
                out.append(None)
            out[p] = w
    return (" ".join(x for x in out if x)).strip() or None


def _build_row_from_oa(doi: str, oa: dict, source_file: str) -> ArchivePaperRow:
    loc = oa.get("primary_location") or {}
    src = loc.get("source") or {}
    venue = src.get("display_name") or None
    is_preprint = (oa.get("type") or "").lower().find("preprint") >= 0 or (
        (src.get("type") or "").lower() == "repository"
    )
    title = (oa.get("title") or "").strip() or None
    authors = [
        (a.get("author") or {}).get("display_name") or ""
        for a in (oa.get("authorships") or [])
    ]
    authors = [a for a in authors if a]
    pub_date = oa.get("publication_date") or None
    year = oa.get("publication_year")
    try:
        year = int(year) if year else None
    except Exception:
        year = None
    abstract = _abstract_from_inverted(oa.get("abstract_inverted_index"))
    cid = canonical_id(doi, title, year)
    return ArchivePaperRow(
        canonical_id=cid,
        doi=doi,
        title=title,
        title_norm=norm_title(title),
        authors_json=authors,
        venue=venue,
        year=year,
        pub_date=pub_date,
        is_preprint=is_preprint,
        abstract=abstract,
        page_count=None,
        pdf_path=None,
        _source="cwll_rec_log",
        _source_ref=source_file,
        _source_payload={"openalex_id": oa.get("id"), "type": oa.get("type")},
    )


def _row_from_doi_only(doi: str, source_file: str) -> ArchivePaperRow:
    cid = canonical_id(doi, None, None)
    return ArchivePaperRow(
        canonical_id=cid,
        doi=doi,
        title=None,
        title_norm="",
        authors_json=[],
        venue=None,
        year=None,
        pub_date=None,
        is_preprint=False,
        abstract=None,
        page_count=None,
        pdf_path=None,
        _source="cwll_rec_log",
        _source_ref=source_file,
        _source_payload={"openalex_lookup": "skipped"},
    )


# -------------------------------------------------------------------- DB

def _apply_to_db(rows: list[ArchivePaperRow], schema: str) -> tuple[int, int]:
    sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
    from _db import exec_many  # noqa: E402

    paper_sql = f"""
        INSERT INTO {schema}.archive_papers
          (canonical_id, doi, title, title_norm, authors_json, venue, year,
           pub_date, is_preprint, abstract, page_count, pdf_path,
           first_seen_at, last_updated_at)
        VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (canonical_id) DO UPDATE SET
          doi             = COALESCE(EXCLUDED.doi, {schema}.archive_papers.doi),
          title           = COALESCE(EXCLUDED.title, {schema}.archive_papers.title),
          authors_json    = COALESCE(EXCLUDED.authors_json, {schema}.archive_papers.authors_json),
          venue           = COALESCE(EXCLUDED.venue, {schema}.archive_papers.venue),
          year            = COALESCE(EXCLUDED.year, {schema}.archive_papers.year),
          pub_date        = COALESCE(EXCLUDED.pub_date, {schema}.archive_papers.pub_date),
          abstract        = COALESCE(EXCLUDED.abstract, {schema}.archive_papers.abstract),
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
    paper_rows = []
    src_rows = []
    for r in rows:
        paper_rows.append((
            r.canonical_id, r.doi, r.title, r.title_norm,
            json.dumps(r.authors_json, ensure_ascii=False), r.venue,
            r.year, r.pub_date, r.is_preprint, r.abstract,
            r.page_count, r.pdf_path,
            r.first_seen_at, r.last_updated_at,
        ))
        src_rows.append((
            r.canonical_id, r._source, r._source_ref,
            json.dumps(r._source_payload, ensure_ascii=False),
            r.last_updated_at,
        ))
    return (exec_many(paper_sql, paper_rows), exec_many(src_sql, src_rows))


# ----------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=os.environ.get("CSNL_CWLL_LOG", DEFAULT_CSV),
                    help="Path to metadata_log.csv")
    ap.add_argument("--enrich", action="store_true",
                    help="Look each DOI up on OpenAlex (cached). Network I/O.")
    ap.add_argument("--apply", action="store_true",
                    help="UPSERT into DB. Default: dry-run JSONL only.")
    ap.add_argument("--limit-dois", type=int, default=None,
                    help="Cap total unique DOIs (smoke test).")
    args = ap.parse_args()

    csv_path = Path(args.path)
    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        return 2

    rows_csv = read_csv(csv_path)
    print(f"[ingest_rec_log] rows={len(rows_csv)}  (confirmed > 0 only)")

    # Collect unique (doi, first source_file) for stable provenance.
    seen: dict[str, str] = {}
    for r in rows_csv:
        for d in r["dois"]:
            seen.setdefault(d, r["source_file"])
    if args.limit_dois:
        seen = dict(list(seen.items())[: args.limit_dois])
    print(f"[ingest_rec_log] unique DOIs={len(seen)}")

    cache = _load_cache()
    out_rows: list[ArchivePaperRow] = []
    n_enriched = 0
    n_cache_hits = 0
    n_transient = 0
    if args.enrich:
        for i, (doi, src_file) in enumerate(seen.items(), 1):
            if doi in cache and cache[doi] is not None:
                oa = cache[doi]
                n_cache_hits += 1
            else:
                oa = _openalex_lookup_one(doi)
                # CACHING RULES:
                # - real hit (dict with 'id'): cache it
                # - definitive 404: cache the _NOT_FOUND sentinel
                # - transient None: DO NOT cache (poisoning a future run is
                #   worse than re-trying — we'll just enrich again next time)
                if isinstance(oa, dict) and oa.get("id"):
                    cache[doi] = oa
                    n_enriched += 1
                elif oa is _NOT_FOUND:
                    cache[doi] = _NOT_FOUND
                else:
                    n_transient += 1
                time.sleep(0.15)  # OpenAlex polite-pool guidance: ≤ 10 rps
                if i % 25 == 0:
                    _save_cache(cache)
                    print(f"[ingest_rec_log] enriched {i}/{len(seen)}  "
                          f"hits={n_enriched}  transient_skips={n_transient}")
            if isinstance(oa, dict) and oa.get("id"):
                out_rows.append(_build_row_from_oa(doi, oa, src_file))
            else:
                out_rows.append(_row_from_doi_only(doi, src_file))
        _save_cache(cache)
    else:
        for doi, src_file in seen.items():
            cv = cache.get(doi)
            if isinstance(cv, dict) and cv.get("id"):
                n_cache_hits += 1
                out_rows.append(_build_row_from_oa(doi, cv, src_file))
            else:
                out_rows.append(_row_from_doi_only(doi, src_file))

    print(f"[ingest_rec_log] enriched={n_enriched}  cache_hits={n_cache_hits}  "
          f"transient_skips={n_transient}  rows={len(out_rows)}")
    print(f"[ingest_rec_log] writing {_JSONL_OUT.relative_to(_REPO_ROOT)}")
    write_jsonl(_JSONL_OUT, out_rows)

    if args.apply:
        sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
        from _db import load_env, ledger_schema  # noqa: E402
        load_env()
        sch = ledger_schema()
        print(f"[ingest_rec_log] applying to schema={sch} (UPSERT)…")
        n_p, n_s = _apply_to_db(out_rows, sch)
        print(f"[ingest_rec_log] OK — papers UPSERTed: {n_p}  sources: {n_s}")
    else:
        print("[ingest_rec_log] dry-run only. Re-run with --apply to UPSERT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
