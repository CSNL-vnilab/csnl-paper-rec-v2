#!/usr/bin/env python3
"""
scripts/archive/ingest_classics.py — ingest the lab's classic PDF archive
(/Volumes/Papers or env override) into the csnl_paper_archive layer.

Operator-run:
    ! python scripts/archive/ingest_classics.py            # dry-run JSONL
    ! python scripts/archive/ingest_classics.py --apply    # write DB

Strategy (cheap-first):
  1. Walk the archive directory; for each *.pdf:
     a. Parse Author_YYYY_Title.pdf via regex (no PDF I/O).
     b. If --read-pdf (default ON), open with pypdf to read page_count and
        the first page text, then DOI-regex over that text. This is cheap
        (single xref + 1 page) and skipped on pypdf errors.
  2. Build an ArchivePaperRow per file; canonical_id is sha256(doi) or
     sha256(norm_title|year).
  3. Emit state/archive/classics_papers.jsonl (always — for audit/idempotency).
  4. If --apply, UPSERT into archive_papers + archive_paper_sources.

Constraints:
  - No network I/O. No LLM calls. No external API keys.
  - csnl_research is not touched. Writes only land in csnl_paper_rec.archive_*.
  - Idempotent: re-running with the same archive produces the same canonical_ids.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    ArchivePaperRow, canonical_id, extract_doi_from_text, kst_iso,
    norm_title, parse_filename, write_jsonl,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_JSONL_OUT = _REPO_ROOT / "state" / "archive" / "classics_papers.jsonl"

DEFAULT_ROOT = "/Volumes/Papers"


# ---------------------------------------------------------------- PDF probe

def _safe_pdf_probe(pdf_path: Path) -> tuple[int | None, str]:
    """Return (page_count, first_page_text). Empty/None on failure.

    Uses pypdf if available; otherwise skipped. We never raise — a corrupt
    PDF should never abort a 5K-file ingest.
    """
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return (None, "")
    try:
        reader = PdfReader(str(pdf_path), strict=False)
        n = len(reader.pages)
        try:
            txt = reader.pages[0].extract_text() or ""
        except Exception:
            txt = ""
        # DOI may also live on the second page (PDF cover-page case).
        if n >= 2 and "10." not in txt:
            try:
                txt += "\n" + (reader.pages[1].extract_text() or "")
            except Exception:
                pass
        return (n, txt[:8000])
    except Exception:
        return (None, "")


# ----------------------------------------------------------------- walker

def walk_archive(root: Path, *, read_pdf: bool, limit: int | None):
    """Yield ArchivePaperRow per *.pdf found under root."""
    n = 0
    for p in sorted(root.rglob("*.pdf")):
        if p.name.startswith("._") or p.name.startswith("."):
            # SMB metadata files (._foo.pdf) — skip
            continue
        meta = parse_filename(p.name)
        title = meta.get("title")
        year = meta.get("year")
        page_count = None
        doi = None
        first_text = ""
        if read_pdf:
            page_count, first_text = _safe_pdf_probe(p)
            doi = extract_doi_from_text(first_text)
        cid = canonical_id(doi, title, year)
        author_hint = meta.get("author")
        row = ArchivePaperRow(
            canonical_id=cid,
            doi=doi,
            title=title,
            title_norm=norm_title(title),
            authors_json=[author_hint] if author_hint else [],
            venue=None,
            year=year,
            pub_date=(f"{year}-01-01" if year else None),
            is_preprint=False,
            abstract=None,
            page_count=page_count,
            pdf_path=str(p),
            _source="classics_smb",
            _source_ref=p.name,
            _source_payload={
                "parsed_author":   author_hint,
                "looks_truncated": meta.get("looks_truncated", False),
                "size_bytes":      p.stat().st_size if p.exists() else None,
            },
        )
        yield row
        n += 1
        if limit and n >= limit:
            return


# -------------------------------------------------------------------- DB

def _apply_to_db(rows: list[ArchivePaperRow], schema: str) -> tuple[int, int]:
    """UPSERT archive_papers + archive_paper_sources for the given rows.

    psycopg2 path uses execute_batch; psql fallback would be too slow at
    this volume (operator should `pip install psycopg2-binary` if needed).
    """
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
          page_count      = COALESCE(EXCLUDED.page_count, {schema}.archive_papers.page_count),
          pdf_path        = COALESCE(EXCLUDED.pdf_path, {schema}.archive_papers.pdf_path),
          last_updated_at = EXCLUDED.last_updated_at;
    """
    import json as _json
    paper_rows = []
    src_rows = []
    for r in rows:
        paper_rows.append((
            r.canonical_id, r.doi, r.title, r.title_norm,
            _json.dumps(r.authors_json, ensure_ascii=False), r.venue,
            r.year, r.pub_date, r.is_preprint, r.abstract,
            r.page_count, r.pdf_path,
            r.first_seen_at, r.last_updated_at,
        ))
        src_rows.append((
            r.canonical_id, r._source, r._source_ref,
            _json.dumps(r._source_payload, ensure_ascii=False),
            r.last_updated_at,
        ))
    n_p = exec_many(paper_sql, paper_rows)

    src_sql = f"""
        INSERT INTO {schema}.archive_paper_sources
          (canonical_id, source, source_ref, source_payload, observed_at)
        VALUES (%s,%s,%s,%s::jsonb,%s)
        ON CONFLICT (canonical_id, source, source_ref) DO UPDATE SET
          source_payload = EXCLUDED.source_payload,
          observed_at    = EXCLUDED.observed_at;
    """
    n_s = exec_many(src_sql, src_rows)
    return (n_p, n_s)


# ----------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("CSNL_CLASSICS_ROOT", DEFAULT_ROOT),
                    help="Archive root (default /Volumes/Papers or $CSNL_CLASSICS_ROOT)")
    ap.add_argument("--no-read-pdf", action="store_true",
                    help="Skip pypdf probe (no DOI, no page_count). Fast.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Stop after N files (smoke test).")
    ap.add_argument("--apply", action="store_true",
                    help="UPSERT into DB. Default: dry-run JSONL only.")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"ERROR: archive root not found: {root}", file=sys.stderr)
        return 2

    read_pdf = not args.no_read_pdf
    rows = list(walk_archive(root, read_pdf=read_pdf, limit=args.limit))
    n_files = len(rows)
    n_doi = sum(1 for r in rows if r.doi)
    n_yr  = sum(1 for r in rows if r.year)
    print(f"[ingest_classics] root={root}  files={n_files}  doi={n_doi}  year={n_yr}")
    print(f"[ingest_classics] writing {_JSONL_OUT.relative_to(_REPO_ROOT)}")
    write_jsonl(_JSONL_OUT, rows)

    if args.apply:
        sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
        from _db import load_env, ledger_schema  # noqa: E402
        load_env()
        sch = ledger_schema()
        print(f"[ingest_classics] applying to schema={sch} (UPSERT)…")
        n_p, n_s = _apply_to_db(rows, sch)
        print(f"[ingest_classics] OK — papers UPSERTed: {n_p}  sources: {n_s}")
    else:
        print("[ingest_classics] dry-run only. Re-run with --apply to UPSERT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
