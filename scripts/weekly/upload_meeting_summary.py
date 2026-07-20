#!/usr/bin/env python3
"""
scripts/weekly/upload_meeting_summary.py — push a PB/GRM presentation SUMMARY
into csnl_paper_rec.archive_meeting_summaries.

WHERE THIS FITS
  ingest_grm_nas.py indexes the slide FILES (archive_meeting_materials). This
  script fills the companion SPACE for the human-readable SUMMARIES that come
  later — the flow the lab will drive from a Claude MCP session:
      Paper Blitz  → one row per researcher per paper they summarised
                     (author_initial = the summariser, paper_ref = the paper).
      Research Mtg → one row per presenter (author_initial = presenter,
                     paper_ref = NULL).
  Each summary optionally links back to its slide file via material_id.

INPUT (two shapes)
  --file PATH    A JSON object, or a list of objects. Per-object keys (aliases
                 accepted): date|meeting_date · kind · author|author_initial ·
                 paper_ref · title · summary|summary_text · keywords (list or
                 comma string) · material_id · material_nas_path ·
                 source · source_version.
  flat args      --date --kind --author --paper-ref --title --summary
                 [--keywords ...] [--material-id N | --material-nas-path P].

MATERIAL LINK (read-only resolve)
  material_id is used as given; else --material-nas-path is looked up; else we
  try (meeting_date, kind[, presenter_initial=author]) and link only when the
  match is UNIQUE. Best-effort — a summary still stores with material_id NULL.

BOUNDARY
  * DRY-RUN is the DEFAULT: parse + resolve + print, write NOTHING. Dry-run
    works with NO database (material-link resolve degrades to a note).
  * --apply UPSERTs into csnl_paper_rec.archive_meeting_summaries ONLY (operator
    / Claude-MCP session with .env creds). csnl_research / csnl_ops /
    archive_responses / archive_meeting_materials are never written here. The
    NAS is never touched. source defaults to 'claude-mcp'.
  * UNIQUE(meeting_date,kind,author_initial,paper_ref): when every key column is
    non-NULL the row upserts via ON CONFLICT (preserving an existing
    material_id via COALESCE). When ANY key column is NULL — a GRM with NULL
    paper_ref, OR a PB-style row with a paper_ref but NULL author — ON CONFLICT
    cannot fire (Postgres treats NULL as distinct in a UNIQUE index), so the row
    is made idempotent by a scoped delete-then-insert that matches the NULLs
    with IS NOT DISTINCT FROM (adversarial finding #3).

REQUIREMENTS / CAVEATS
  * --apply REQUIRES psycopg2-binary. The NULL-key delete-then-insert is a
    multi-statement transaction that the psql exec_many fallback (used by
    ingest_grm_nas.py) cannot express atomically, so this script intentionally
    does not provide a psql-only --apply path. psycopg2 is present on the lab
    Python; if you hit a psql-only environment, `python3 -m pip install --user
    psycopg2-binary` first. Dry-run needs nothing.
  * Under --apply with a --file batch, valid items are applied and any invalid
    sibling items are reported to stderr (best-effort, not all-or-nothing) —
    check stderr after a batch apply to confirm nothing was silently dropped.

CLI
  python3 scripts/weekly/upload_meeting_summary.py --file summaries.json
  python3 scripts/weekly/upload_meeting_summary.py \\
      --date 20260610 --kind grm --author BHL \\
      --title "..." --summary "발표 요약 ..."
  ! python3 scripts/weekly/upload_meeting_summary.py --file summaries.json --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))

VALID_KINDS = ("pb", "grm", "focus_grm")
DEFAULT_SOURCE = "claude-mcp"
_INIT_RE = re.compile(r"^[A-Z]{2,8}$")


# ===========================================================================
# Input normalisation
# ===========================================================================

def _norm_date(v: Any) -> Optional[str]:
    """Accept YYYYMMDD or YYYY-MM-DD → store YYYY-MM-DD."""
    if v is None:
        return None
    s = str(v).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _norm_keywords(v: Any) -> Optional[list]:
    if v is None or v == "":
        return None
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    parts = [p.strip() for p in re.split(r"[,\n·]", str(v)) if p.strip()]
    return parts or None


def _norm_one(obj: dict) -> tuple[Optional[dict], list[str]]:
    """One raw object → (normalised summary row | None, errors)."""
    err: list[str] = []
    date = _norm_date(obj.get("meeting_date") or obj.get("date"))
    if not date:
        err.append("meeting_date missing/invalid (need YYYYMMDD or YYYY-MM-DD)")
    kind = (obj.get("kind") or "").strip().lower()
    if kind not in VALID_KINDS:
        err.append(f"kind must be one of {VALID_KINDS} (got {kind!r})")
    author = (obj.get("author_initial") or obj.get("author") or "").strip().upper() or None
    if author and not _INIT_RE.match(author):
        err.append(f"author_initial must match ^[A-Z]{{2,8}}$ (got {author!r})")
        author = None
    summary = (obj.get("summary_text") or obj.get("summary") or "").strip()
    if not summary:
        err.append("summary_text is required")
    if err:
        return None, err
    paper_ref = obj.get("paper_ref")
    paper_ref = paper_ref.strip() if isinstance(paper_ref, str) and paper_ref.strip() else None
    title = obj.get("title")
    title = title.strip() if isinstance(title, str) and title.strip() else None
    row = {
        "meeting_date": date,
        "kind": kind,
        "author_initial": author,
        "paper_ref": paper_ref,
        "title": title,
        "summary_text": summary,
        "keywords": _norm_keywords(obj.get("keywords")),
        "material_id": obj.get("material_id"),
        "material_nas_path": (obj.get("material_nas_path") or "").strip() or None,
        "source": (obj.get("source") or DEFAULT_SOURCE).strip() or DEFAULT_SOURCE,
        "source_version": (obj.get("source_version") or "").strip() or None,
        "raw_jsonb": obj.get("raw_jsonb") or {},
    }
    return row, []


def _collect_inputs(args) -> tuple[list[dict], list[str]]:
    raw: list[dict] = []
    if args.file:
        data = json.loads(Path(args.file).read_text("utf-8"))
        raw = data if isinstance(data, list) else [data]
    else:
        if not (args.date and args.kind and args.summary):
            return [], ["flat mode requires at least --date, --kind, --summary "
                        "(or use --file)"]
        raw = [{
            "date": args.date, "kind": args.kind, "author": args.author,
            "paper_ref": args.paper_ref, "title": args.title,
            "summary": args.summary, "keywords": args.keywords,
            "material_id": args.material_id,
            "material_nas_path": args.material_nas_path,
            "source": args.source, "source_version": args.source_version,
        }]
    rows: list[dict] = []
    errors: list[str] = []
    for i, obj in enumerate(raw):
        row, errs = _norm_one(obj if isinstance(obj, dict) else {})
        if errs:
            errors.append(f"item[{i}]: " + "; ".join(errs))
        else:
            rows.append(row)
    return rows, errors


# ===========================================================================
# Material link resolution (read-only; best-effort)
# ===========================================================================

def _resolve_material(row: dict, sch: str) -> tuple[Optional[int], str]:
    """Return (material_id|None, note). Never raises — read failures degrade to
    (None, reason)."""
    if row.get("material_id") not in (None, ""):
        try:
            return int(row["material_id"]), "explicit"
        except (TypeError, ValueError):
            return None, "explicit material_id not an int — ignored"
    try:
        from _db import query_json
        if row.get("material_nas_path"):
            p = row["material_nas_path"].replace("'", "''")
            res = query_json(
                f"SELECT material_id FROM {sch}.archive_meeting_materials "
                f"WHERE nas_path = '{p}'")
            if res:
                return int(res[0]["material_id"]), "by nas_path"
            return None, "nas_path not found"
        cond = [f"meeting_date = '{row['meeting_date']}'",
                f"kind = '{row['kind']}'"]
        if row.get("author_initial") and row["kind"] in ("grm", "focus_grm"):
            cond.append(f"presenter_initial = '{row['author_initial']}'")
        res = query_json(
            f"SELECT material_id FROM {sch}.archive_meeting_materials "
            f"WHERE {' AND '.join(cond)}")
        if len(res) == 1:
            return int(res[0]["material_id"]), "unique date+kind match"
        if not res:
            return None, "no material for date+kind"
        return None, f"{len(res)} materials match — ambiguous, left NULL"
    except Exception as e:
        return None, f"lookup skipped ({type(e).__name__})"


# ===========================================================================
# Upsert (operator --apply)
# ===========================================================================

def _upsert(rows: list[dict], sch: str) -> int:
    from _db import _conn
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        raise SystemExit(
            "upload_meeting_summary --apply requires psycopg2-binary (the "
            "NULL-key delete-then-insert is a multi-statement transaction the "
            "psql fallback cannot express atomically). Install it with: "
            "python3 -m pip install --user psycopg2-binary")
    conn = _conn()
    n = 0
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            for r in rows:
                kw = json.dumps(r.get("keywords")) if r.get("keywords") else None
                raw = json.dumps(r.get("raw_jsonb") or {}, ensure_ascii=False)
                vals = (r["meeting_date"], r["kind"], r["author_initial"],
                        r["paper_ref"], r["title"], r["summary_text"],
                        kw, r.get("material_id"), r["source"],
                        r.get("source_version"), raw)
                if r["author_initial"] is None or r["paper_ref"] is None:
                    # A NULL in any UNIQUE-key column is "distinct" under the
                    # index, so ON CONFLICT cannot fire — make the row idempotent
                    # by replacing the scoped match first (matching the NULLs
                    # with IS NOT DISTINCT FROM). Covers GRM (NULL paper_ref) AND
                    # a PB-style row with a paper_ref but NULL author. Scoped to
                    # csnl_paper_rec only.
                    cur.execute(
                        f"DELETE FROM {sch}.archive_meeting_summaries "
                        f"WHERE meeting_date=%s AND kind=%s "
                        f"AND author_initial IS NOT DISTINCT FROM %s "
                        f"AND paper_ref IS NOT DISTINCT FROM %s",
                        (r["meeting_date"], r["kind"], r["author_initial"],
                         r["paper_ref"]))
                    cur.execute(f"""
                        INSERT INTO {sch}.archive_meeting_summaries
                          (meeting_date,kind,author_initial,paper_ref,title,
                           summary_text,keywords,material_id,source,
                           source_version,raw_jsonb)
                        VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb)
                    """, vals)
                else:
                    cur.execute(f"""
                        INSERT INTO {sch}.archive_meeting_summaries
                          (meeting_date,kind,author_initial,paper_ref,title,
                           summary_text,keywords,material_id,source,
                           source_version,raw_jsonb)
                        VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb)
                        ON CONFLICT (meeting_date,kind,author_initial,paper_ref)
                        DO UPDATE SET
                          title=EXCLUDED.title,
                          summary_text=EXCLUDED.summary_text,
                          keywords=EXCLUDED.keywords,
                          material_id=COALESCE(EXCLUDED.material_id,
                                               {sch}.archive_meeting_summaries.material_id),
                          source=EXCLUDED.source,
                          source_version=EXCLUDED.source_version,
                          raw_jsonb=EXCLUDED.raw_jsonb
                    """, vals)
                n += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return n


# ===========================================================================
# CLI
# ===========================================================================

def _print_row(r: dict) -> None:
    pr = (r["paper_ref"] or "—")[:48]
    print(f"  [{r['kind']:9}] {r.get('author_initial') or '-':<8} "
          f"date={r['meeting_date']} paper_ref={pr!r}")
    print(f"     title:    {(r.get('title') or '')[:80]!r}")
    print(f"     summary:  {r['summary_text'][:90]!r}")
    print(f"     material: {r.get('material_id')} ({r.get('_material_note','')}) "
          f"· source={r['source']} kw={r.get('keywords')}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", metavar="PATH", default=None,
                    help="JSON object or list of summary objects.")
    ap.add_argument("--date", metavar="YYYYMMDD", default=None)
    ap.add_argument("--kind", choices=VALID_KINDS, default=None)
    ap.add_argument("--author", metavar="INIT", default=None)
    ap.add_argument("--paper-ref", dest="paper_ref", default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--summary", default=None)
    ap.add_argument("--keywords", default=None,
                    help="comma-separated keyword list.")
    ap.add_argument("--material-id", dest="material_id", type=int, default=None)
    ap.add_argument("--material-nas-path", dest="material_nas_path", default=None)
    ap.add_argument("--source", default=DEFAULT_SOURCE)
    ap.add_argument("--source-version", dest="source_version", default=None)
    ap.add_argument("--apply", action="store_true",
                    help="UPSERT into csnl_paper_rec.archive_meeting_summaries.")
    args = ap.parse_args()

    rows, errors = _collect_inputs(args)
    if errors:
        for e in errors:
            print(f"[summary] ERROR {e}", file=sys.stderr)
        if not rows:
            return 2

    # resolve schema + material links (read-only; tolerant of offline)
    sch = "csnl_paper_rec"
    try:
        from _db import load_env, ledger_schema
        load_env()
        sch = ledger_schema()
    except Exception:
        pass
    for r in rows:
        mid, note = _resolve_material(r, sch)
        r["material_id"] = mid
        r["_material_note"] = note

    print(f"[summary] {len(rows)} summary row(s) "
          f"({sum(1 for r in rows if r['kind']=='pb')} pb · "
          f"{sum(1 for r in rows if r['kind'] in ('grm','focus_grm'))} grm):")
    for r in rows:
        _print_row(r)

    if args.apply:
        if not rows:
            print("[summary] nothing to apply.")
            return 0
        for r in rows:
            r.pop("_material_note", None)
            r.pop("material_nas_path", None)
        n = _upsert(rows, sch)
        print(f"\n[summary] UPSERT complete: {n} row(s) into "
              f"{sch}.archive_meeting_summaries.")
        return 0

    print("\n[summary] dry-run only — no DB writes. Re-run with --apply "
          "(operator) to upsert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
