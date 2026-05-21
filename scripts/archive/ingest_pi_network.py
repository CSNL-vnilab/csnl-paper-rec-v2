#!/usr/bin/env python3
"""
scripts/archive/ingest_pi_network.py — for each PI in pi_network_data.json,
fetch their last-10-year publications via OpenAlex (keyless) and emit
ArchivePaperRow JSONL.

Operator-run:
    ! python scripts/archive/ingest_pi_network.py            # dry-run JSONL
    ! python scripts/archive/ingest_pi_network.py --apply    # write DB
    ! python scripts/archive/ingest_pi_network.py --limit-pis 5  # smoke test

Behavior:
  - Authors are looked up by display_name on OpenAlex /authors; the best
    match (highest works_count) is selected. Multiple-author collisions
    yield the most prolific candidate — same-named non-lab authors are
    handled by the lab-relevance filter downstream, not here.
  - Per author: /works?filter=author.id:<Axxx>,from_publication_date:<10y>
    paginated cursor; per_page=200.
  - Cache per author_id at state/archive/openalex_pi_cache.json.
  - Each work emits one row tagged source='pi_network', source_ref=PI id.
  - Network use is restricted to api.openalex.org — no other endpoints.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    ArchivePaperRow, canonical_id, kst_iso, norm_doi, norm_title, write_jsonl,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_JSONL_OUT = _REPO_ROOT / "state" / "archive" / "pi_network_papers.jsonl"
_CACHE     = _REPO_ROOT / "state" / "archive" / "openalex_pi_cache.json"
_DEFAULT_PI_JSON = Path("/Users/csnl/Downloads/pi_network_data.json")

OA_MAILTO = "csnl@vnilab.local"
USER_AGENT = "csnl-paper-rec/2.1 (+archive/pi_network)"


def _get(url: str, *, timeout: int = 30, retries: int = 4) -> dict | None:
    """OpenAlex GET with 429/5xx exponential backoff.

    Returns:
      - parsed JSON dict on 200
      - None on 404 (caller may interpret as "definitively missing")
      - None on persistent transient failure (call site must NOT cache
        this into a persistent file; just re-try next run)
    """
    import requests
    for i in range(retries):
        try:
            r = requests.get(
                url, timeout=timeout,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        except Exception:
            time.sleep(1.2 * (i + 1))
            continue
        if r.status_code == 200:
            try:
                return r.json()
            except Exception:
                return None
        if r.status_code == 404:
            return None  # caller distinguishes via separate code path if needed
        if r.status_code == 429 or 500 <= r.status_code < 600:
            time.sleep(min(2 ** i, 30) + 0.5)
            continue
        return None
    return None


def _save_cache(c: dict) -> None:
    """Atomic write of the OpenAlex author/works cache."""
    _CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CACHE.with_suffix(_CACHE.suffix + ".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _CACHE)


def find_author_id(display_name: str, affiliation_hint: str | None = None) -> str | None:
    """Return best OpenAlex author id (e.g. 'A1234567890') or None.

    Looks at the top 20 search results, prefers matches where affiliation
    contains the hint (substring match with hint split on whitespace, so
    "Seoul Nat'l Univ" vs "Seoul National University" still scores). When
    no hint matches, picks the most prolific candidate.
    """
    from urllib.parse import quote_plus
    url = (f"https://api.openalex.org/authors?search={quote_plus(display_name)}"
           f"&per-page=20&mailto={OA_MAILTO}")
    j = _get(url)
    if not j or not j.get("results"):
        return None
    results = j["results"]

    if affiliation_hint:
        # Tokenize the hint into word stems and score by how many tokens
        # appear in each result's known affiliations (case-insensitive).
        tokens = [t.lower() for t in re.split(r"[^A-Za-z0-9]+", affiliation_hint)
                  if len(t) >= 4]
        if tokens:
            def _score(r: dict) -> int:
                affs = " ".join((a.get("display_name") or "").lower()
                                for a in (r.get("last_known_institutions") or []))
                return sum(1 for t in tokens if t in affs)
            ranked = sorted(results, key=lambda r: (-_score(r),
                                                    -(r.get("works_count") or 0)))
            if _score(ranked[0]) > 0:
                aid = (ranked[0].get("id") or "").rsplit("/", 1)[-1]
                return aid or None

    # Fallback: most prolific.
    results = sorted(results, key=lambda r: -(r.get("works_count") or 0))
    aid = results[0].get("id") or ""
    return aid.rsplit("/", 1)[-1] or None


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


def works_for_author(author_oid: str, since_iso: str) -> list[dict]:
    """All works for author_oid since since_iso, cursor-paginated.

    Returns whatever pages we successfully retrieved; on transient failure
    mid-cursor, returns the partial list rather than poisoning the cache.
    """
    out: list[dict] = []
    cursor = "*"
    base = (
        f"https://api.openalex.org/works"
        f"?filter=author.id:{author_oid},from_publication_date:{since_iso}"
        f"&per-page=200&mailto={OA_MAILTO}"
    )
    while True:
        url = f"{base}&cursor={cursor}"
        j = _get(url)
        if not j:
            return out
        out.extend(j.get("results", []) or [])
        cursor = (j.get("meta") or {}).get("next_cursor")
        if not cursor:
            return out
        time.sleep(0.2)  # polite pool ≤ 10 rps; 5 rps is the safe shoulder


def _row_from_work(w: dict, pi_id: str) -> ArchivePaperRow:
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    title = (w.get("title") or "").strip() or None
    doi = norm_doi(w.get("doi"))
    year = w.get("publication_year")
    try:
        year = int(year) if year else None
    except Exception:
        year = None
    is_pre = (
        (w.get("type") or "").lower().find("preprint") >= 0
        or (src.get("type") or "").lower() == "repository"
    )
    authors = [
        (a.get("author") or {}).get("display_name") or ""
        for a in (w.get("authorships") or [])
    ]
    # find PI's position among authors
    pi_pos = None
    for i, a in enumerate(w.get("authorships") or []):
        if (a.get("author") or {}).get("id", "").endswith(pi_id):
            pi_pos = i + 1
            break
    cid = canonical_id(doi, title, year)
    return ArchivePaperRow(
        canonical_id=cid,
        doi=doi,
        title=title,
        title_norm=norm_title(title),
        authors_json=[a for a in authors if a],
        venue=src.get("display_name") or None,
        year=year,
        pub_date=w.get("publication_date"),
        is_preprint=is_pre,
        abstract=_abstract_from_inverted(w.get("abstract_inverted_index")),
        page_count=None,
        pdf_path=None,
        _source="pi_network",
        _source_ref=pi_id,
        _source_payload={
            "openalex_id":   w.get("id"),
            "type":          w.get("type"),
            "pi_position":   pi_pos,
            "is_corresp":    any(
                ((a.get("author") or {}).get("id", "")).endswith(pi_id)
                and a.get("is_corresponding")
                for a in (w.get("authorships") or [])
            ),
        },
    )


# ----------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=str(_DEFAULT_PI_JSON),
                    help="pi_network_data.json path")
    ap.add_argument("--years", type=int, default=10,
                    help="Lookback window in years (default 10).")
    ap.add_argument("--limit-pis", type=int, default=None,
                    help="Cap number of PIs (smoke test).")
    ap.add_argument("--apply", action="store_true",
                    help="UPSERT into DB. Default: dry-run JSONL only.")
    args = ap.parse_args()

    pi_path = Path(args.path)
    if not pi_path.exists():
        print(f"ERROR: {pi_path} not found", file=sys.stderr)
        return 2
    pi_data = json.loads(pi_path.read_text("utf-8"))
    nodes = pi_data.get("nodes") or []
    if args.limit_pis:
        nodes = nodes[: args.limit_pis]
    print(f"[ingest_pi] PIs to process: {len(nodes)}  (lookback={args.years}y)")

    cache: dict = {}
    if _CACHE.exists():
        try:
            cache = json.loads(_CACHE.read_text("utf-8"))
        except Exception:
            cache = {}

    since = (datetime.now(timezone.utc) - timedelta(days=365 * args.years)).date().isoformat()
    out_rows: list[ArchivePaperRow] = []
    n_no_id = 0
    n_dirty = 0  # checkpoint write counter
    for i, n in enumerate(nodes, 1):
        name = n.get("full_name") or n.get("id")
        if not name:
            continue
        # Resolve author OID (cached). Do NOT cache None — keep retrying
        # transient lookup failures across runs.
        key = f"id::{name}"
        if key in cache and cache[key]:
            oid = cache[key]
        else:
            oid = find_author_id(name, affiliation_hint=n.get("affiliation"))
            if oid:
                cache[key] = oid
                n_dirty += 1
        if not oid:
            n_no_id += 1
            print(f"[ingest_pi] [{i}/{len(nodes)}] no OpenAlex id for: {name}")
            # No cache write here (deliberate — leave it retryable).
            if n_dirty >= 10:
                _save_cache(cache); n_dirty = 0
            continue

        # Fetch works (cached per author × since). Empty list is a valid
        # cached value ("queried, none found"); only re-fetch on missing key.
        wkey = f"works::{oid}::{since}"
        if wkey in cache:
            works = cache[wkey]
        else:
            works = works_for_author(oid, since)
            if works:                     # only cache non-empty result;
                cache[wkey] = works       # an empty list may indicate a
                n_dirty += 1              # transient mid-cursor failure
            if n_dirty >= 5:
                _save_cache(cache); n_dirty = 0
        print(f"[ingest_pi] [{i}/{len(nodes)}] {name}  oid={oid}  works={len(works)}")
        for w in works:
            out_rows.append(_row_from_work(w, oid))

    if n_dirty:
        _save_cache(cache)
    print(f"[ingest_pi] total rows={len(out_rows)}  unresolved_pis={n_no_id}")
    print(f"[ingest_pi] writing {_JSONL_OUT.relative_to(_REPO_ROOT)}")
    write_jsonl(_JSONL_OUT, out_rows)

    if args.apply:
        sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
        from _db import load_env, ledger_schema, exec_many  # noqa: E402
        load_env()
        sch = ledger_schema()
        paper_sql = f"""
            INSERT INTO {sch}.archive_papers
              (canonical_id, doi, title, title_norm, authors_json, venue, year,
               pub_date, is_preprint, abstract, page_count, pdf_path,
               first_seen_at, last_updated_at)
            VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (canonical_id) DO UPDATE SET
              doi             = COALESCE(EXCLUDED.doi, {sch}.archive_papers.doi),
              title           = COALESCE(EXCLUDED.title, {sch}.archive_papers.title),
              authors_json    = COALESCE(EXCLUDED.authors_json, {sch}.archive_papers.authors_json),
              venue           = COALESCE(EXCLUDED.venue, {sch}.archive_papers.venue),
              year            = COALESCE(EXCLUDED.year, {sch}.archive_papers.year),
              pub_date        = COALESCE(EXCLUDED.pub_date, {sch}.archive_papers.pub_date),
              abstract        = COALESCE(EXCLUDED.abstract, {sch}.archive_papers.abstract),
              last_updated_at = EXCLUDED.last_updated_at;
        """
        src_sql = f"""
            INSERT INTO {sch}.archive_paper_sources
              (canonical_id, source, source_ref, source_payload, observed_at)
            VALUES (%s,%s,%s,%s::jsonb,%s)
            ON CONFLICT (canonical_id, source, source_ref) DO UPDATE SET
              source_payload = EXCLUDED.source_payload,
              observed_at    = EXCLUDED.observed_at;
        """
        paper_rows = []
        src_rows = []
        for r in out_rows:
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
        n_p = exec_many(paper_sql, paper_rows)
        n_s = exec_many(src_sql, src_rows)
        print(f"[ingest_pi] OK — papers UPSERTed: {n_p}  sources: {n_s}")
    else:
        print("[ingest_pi] dry-run only. Re-run with --apply to UPSERT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
