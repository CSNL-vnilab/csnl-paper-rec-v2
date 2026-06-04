#!/usr/bin/env python3
"""
scripts/archive/ingest_live_papers.py — P26d Phase A.

Ingest the genuinely-new live-discovered papers (state/archive/discovery_run/
found/*.jsonl, the accepts NOT already in archive_papers) into
csnl_paper_rec.archive_papers with source='live_search', so they can flow
through the synopsis + digest/interview pipeline like archive papers.

- Unions all found/<INIT>.jsonl by canonical_id (a paper relevant to several
  researchers is ONE corpus row).
- Skips canonical_ids already present in archive_papers (no clobber).
- Backfills authors_json / pub_date / is_preprint / venue by DOI from OpenAlex
  (lightweight HTTP, same approach as backfill_abstracts.py).
- Inserts via exec_many ON CONFLICT (canonical_id) DO NOTHING.

DEFAULT = dry-run (counts only). Pass --apply to write. REVERSIBLE:
  DELETE FROM archive_papers WHERE source='live_search';

BOUNDARY: writes ONLY archive_papers (source='live_search'); never
archive_responses; csnl_research read-only. Requires the P26d migration
(source column) to have been applied first.
"""
from __future__ import annotations
import glob
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(HERE))
import _db  # noqa: E402
from _common import norm_title, kst_iso  # noqa: E402

FOUND = ROOT / "state/archive/discovery_run/found"
UA = "csnl-paper-rec/1.0 (mailto:lab@example.org)"
PREPRINT_VENUES = ("arxiv", "biorxiv", "medrxiv", "psyarxiv", "preprint", "research square", "ssrn", "osf")


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _openalex_meta(doi: str) -> dict:
    """Return {authors:[...], pub_date, venue, is_preprint} from OpenAlex by DOI."""
    try:
        d = _get(f"https://api.openalex.org/works/https://doi.org/{urllib.parse.quote(doi)}?mailto=lab@example.org")
    except Exception:
        return {}
    authors = []
    for a in (d.get("authorships") or []):
        nm = ((a.get("author") or {}).get("display_name") or "").strip()
        if nm:
            authors.append(nm)
    loc = (d.get("primary_location") or {}).get("source") or {}
    venue = (loc.get("display_name") or "").strip()
    pub_date = (d.get("publication_date") or "").strip()
    typ = (d.get("type") or "").lower()
    is_pre = bool(d.get("is_paratext")) is False and (
        typ == "preprint" or any(p in venue.lower() for p in PREPRINT_VENUES))
    return {"authors": authors, "pub_date": pub_date, "venue": venue, "is_preprint": is_pre}


def main():
    apply = "--apply" in sys.argv
    _db.load_env()
    schema = _db.ledger_schema()

    # ---- union found/*.jsonl by canonical_id --------------------------------
    papers: dict[str, dict] = {}
    rels: dict[str, set] = {}
    for fp in sorted(glob.glob(str(FOUND / "*.jsonl"))):
        init = os.path.basename(fp)[:-6]
        for line in open(fp, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            cid = o.get("canonical_id")
            if not cid:
                continue
            rels.setdefault(cid, set()).add(init)
            # prefer the record that has an abstract
            if cid not in papers or (not (papers[cid].get("abstract") or "").strip()
                                     and (o.get("abstract") or "").strip()):
                papers[cid] = o
    print(f"union of found/*.jsonl: {len(papers)} unique papers "
          f"(relevant to researchers: {sum(len(v) for v in rels.values())} pairs)")

    # ---- which already in archive_papers ------------------------------------
    cids = list(papers.keys())
    present = set()
    CH = 500
    for i in range(0, len(cids), CH):
        chunk = cids[i:i + CH]
        in_list = ",".join("'" + c + "'" for c in chunk)  # 32-hex, safe
        got = _db.query_json(
            f"SELECT canonical_id FROM {schema}.archive_papers WHERE canonical_id IN ({in_list})")
        present |= {r["canonical_id"] for r in got}
    new_cids = [c for c in cids if c not in present]
    print(f"already in archive_papers: {len(present)} | NEW to ingest: {len(new_cids)}")
    # how many NEW have a DOI / abstract
    have_doi = sum(1 for c in new_cids if (papers[c].get("doi") or "").strip())
    have_abs = sum(1 for c in new_cids if (papers[c].get("abstract") or "").strip())
    print(f"  of NEW: with DOI={have_doi}, with abstract={have_abs}")

    if not apply:
        print("\nDRY-RUN (no writes). Re-run with --apply to ingest. "
              "Ensure P26d migration (source column) is applied first.")
        # show 5 samples
        for c in new_cids[:5]:
            p = papers[c]
            print(f"  e.g. [{','.join(sorted(rels[c]))}] {(p.get('title') or '')[:80]}")
        return

    # ---- backfill metadata + build rows -------------------------------------
    now = kst_iso()
    rows = []
    bf = 0
    for c in new_cids:
        p = papers[c]
        doi = (p.get("doi") or "").strip().replace("https://doi.org/", "") or None
        meta = {}
        if doi:
            meta = _openalex_meta(doi)
            if meta:
                bf += 1
            time.sleep(0.34)
        authors = meta.get("authors") or []
        venue = (p.get("venue") or "").strip() or meta.get("venue") or None
        pub_date = meta.get("pub_date") or None
        is_pre = meta.get("is_preprint")
        if is_pre is None:
            is_pre = any(pp in (venue or "").lower() for pp in PREPRINT_VENUES)
        year = p.get("year")
        try:
            year = int(year) if year else None
        except Exception:
            year = None
        title = (p.get("title") or "").strip() or None
        rows.append((
            c, doi, title, norm_title(title) if title else None,
            json.dumps(authors, ensure_ascii=False) if authors else None,
            venue, year, pub_date, is_pre,
            (p.get("abstract") or "").strip() or None,
            now, now,
        ))
    sql = (
        f"INSERT INTO {schema}.archive_papers "
        f"(canonical_id, doi, title, title_norm, authors_json, venue, year, "
        f" pub_date, is_preprint, abstract, source, first_seen_at, last_updated_at) "
        f"VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,'live_search',%s,%s) "
        f"ON CONFLICT (canonical_id) DO NOTHING"
    )
    _db.exec_many(sql, rows)
    chk = _db.query_json(
        f"SELECT count(*) c FROM {schema}.archive_papers WHERE source='live_search'")
    print(f"INSERTED {len(rows)} rows (backfilled meta for {bf}); "
          f"archive_papers.source='live_search' total now: {chk[0]['c']}")


if __name__ == "__main__":
    main()
