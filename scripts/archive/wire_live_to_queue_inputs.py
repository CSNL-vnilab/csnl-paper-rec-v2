#!/usr/bin/env python3
"""
scripts/archive/wire_live_to_queue_inputs.py — P26d Phase C, Step 1.

Wire live-discovered papers (archive_papers.source='live_search') into the
queue-builder INPUTS. build_researcher_queue.py reads JSONL *mirrors*
(state/archive/{embeddings,merged_papers,filter_decisions,filter_decisions_dim}.jsonl),
NOT the DB, for papers / embeddings / filter-decisions. So live papers must
be appended to those mirrors (and UPSERTed to the matching DB tables for
consistency) before they can ever surface in a researcher queue.

For every archive_papers row with source='live_search' this script:
  1. Embeds it with the SAME backend/model as the frozen archive
     (compute_embeddings._LocalBackend("BAAI/bge-m3"), 1024-dim normalized).
  2. Idempotently appends (skipping canonical_ids already present) to the
     four JSONL mirrors, matching each file's EXACT existing line shape.
  3. UPSERTs archive_paper_embeddings + archive_filter_decisions in the DB.

Boundaries: writes only csnl_paper_rec (embeddings + filter_decisions) and
the local jsonl mirrors. NEVER touches archive_responses. csnl_research is
not read here at all. Idempotent: re-running appends nothing new and the DB
UPSERTs are ON CONFLICT no-ops.

Operator-run (default dry-run on the DB side; mirrors are always appended
because they are the queue-builder's source of truth and the append is the
whole point — but it is idempotent):
    ! python3 scripts/archive/wire_live_to_queue_inputs.py            # mirrors only
    ! python3 scripts/archive/wire_live_to_queue_inputs.py --apply    # + DB UPSERT
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import kst_iso, norm_title  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ARCHIVE = _REPO_ROOT / "state" / "archive"

_F_EMB   = _ARCHIVE / "embeddings.jsonl"
_F_PAP   = _ARCHIVE / "merged_papers.jsonl"
_F_FILT  = _ARCHIVE / "filter_decisions.jsonl"
_F_DIM   = _ARCHIVE / "filter_decisions_dim.jsonl"

_MODEL_NAME = "BAAI/bge-m3"
_DIM = 1024
_LIVE_SOURCE = "live_search"


# ---------------------------------------------------------------- defensive IO
def _iter_jsonl(path: Path):
    """Split on \\n only (U+2028/U+2029 inside abstracts must not mid-split a
    JSON record). Mirrors the read pattern in compute_embeddings/build_queue."""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", newline="") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            if line.strip():
                yield json.loads(line)


def _existing_cids(path: Path) -> set[str]:
    return {r["canonical_id"] for r in _iter_jsonl(path)}


def _append_jsonl(path: Path, rows: list[dict]) -> int:
    """Append rows to a JSONL mirror (utf-8, ensure_ascii=False to match the
    existing files which write CJK + raw unicode). Ensures the file ends with
    a newline before appending so we never glue onto a partial last line."""
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    # Guarantee a trailing newline on the existing file.
    if path.exists() and path.stat().st_size > 0:
        with path.open("rb") as f:
            f.seek(-1, 2)
            last = f.read(1)
        if last not in (b"\n", b"\r"):
            with path.open("a", encoding="utf-8") as f:
                f.write("\n")
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


# ---------------------------------------------------------------- DB read
def _fetch_live_papers(sch: str) -> list[dict]:
    sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
    from _db import query_json  # noqa: E402
    rows = query_json(
        "SELECT canonical_id, doi, title, title_norm, authors_json, venue, "
        "       year, pub_date, is_preprint, abstract, page_count, pdf_path, "
        "       first_seen_at, last_updated_at "
        f"FROM {sch}.archive_papers "
        f"WHERE source = '{_LIVE_SOURCE}' "
        "ORDER BY canonical_id"
    )
    return rows


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Also UPSERT archive_paper_embeddings + "
                         "archive_filter_decisions in the DB.")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap number of live papers processed (debug).")
    args = ap.parse_args()

    sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
    from _db import load_env, ledger_schema  # noqa: E402
    from compute_embeddings import compose_text, _LocalBackend  # noqa: E402

    load_env()
    sch = ledger_schema()

    papers = _fetch_live_papers(sch)
    if args.limit:
        papers = papers[: args.limit]
    print(f"[wire] live_search papers in DB: {len(papers)}")
    if not papers:
        print("[wire] nothing to do.")
        return 0

    # ---- Embed (reuse the archive backend) --------------------------------
    backend = _LocalBackend(_MODEL_NAME)
    print(f"[wire] embedding backend={_MODEL_NAME} dim={backend.dim}")
    assert backend.dim == _DIM, f"unexpected dim {backend.dim}"

    ts = kst_iso()
    emb_by_cid: dict[str, list[float]] = {}
    skipped_empty = 0
    workable = [p for p in papers if (compose_text(p) or "").strip()]
    skipped_empty = len(papers) - len(workable)
    if skipped_empty:
        print(f"[wire] WARNING: {skipped_empty} live papers have empty composed "
              f"text (no title/abstract/venue) — skipping their embedding")
    for i in range(0, len(workable), args.batch):
        batch = workable[i : i + args.batch]
        vecs = backend.encode([compose_text(p) for p in batch])
        for p, v in zip(batch, vecs):
            emb_by_cid[p["canonical_id"]] = v
        if (i // args.batch) % 5 == 0:
            print(f"[wire] embedded {min(i + args.batch, len(workable))}/{len(workable)}")
    print(f"[wire] embedded N={len(emb_by_cid)}")

    # ---- Build candidate mirror rows (only papers we embedded) ------------
    # embeddings.jsonl
    emb_rows = [{
        "canonical_id":   cid,
        "model_name":     _MODEL_NAME,
        "dim":            _DIM,
        "embedding_json": emb_by_cid[cid],
        "generated_at":   ts,
    } for cid in emb_by_cid]

    # merged_papers.jsonl — full paper record in the EXACT key order/shape of
    # the existing file (keys: abstract..year, see head -1). We recompute
    # title_norm via _common.norm_title for consistency (DB value preferred
    # when present and non-empty).
    pap_by_cid = {p["canonical_id"]: p for p in papers}
    pap_rows = []
    for cid in emb_by_cid:
        p = pap_by_cid[cid]
        tnorm = p.get("title_norm") or norm_title(p.get("title"))
        pap_rows.append({
            "canonical_id":    cid,
            "doi":             p.get("doi"),
            "title":           p.get("title"),
            "title_norm":      tnorm,
            "authors_json":    p.get("authors_json") or [],
            "venue":           p.get("venue"),
            "year":            p.get("year"),
            "pub_date":        p.get("pub_date"),
            "is_preprint":     bool(p.get("is_preprint")),
            "abstract":        p.get("abstract"),
            "page_count":      p.get("page_count"),
            "pdf_path":        p.get("pdf_path"),
            "first_seen_at":   p.get("first_seen_at") or ts,
            "last_updated_at": p.get("last_updated_at") or ts,
        })

    # filter_decisions.jsonl — mark live papers in-scope so they reach a queue.
    filt_rows = [{
        "canonical_id":     cid,
        "is_textbook":      False,
        "is_draft":         False,
        "is_poster":        False,
        "is_review_doc":    False,
        "is_conf_abstract": False,
        "is_lab_relevant":  True,
        "lab_scope_tags":   [],
        "filter_reason":    "p26_live_discovery",
        "decided_at":       ts,
    } for cid in emb_by_cid]

    # filter_decisions_dim.jsonl — empty dim tags (the reasoning gate, not
    # dim-tag overlap, drives inclusion for live papers).
    dim_rows = [{
        "canonical_id": cid,
        "dim_tags":     {"focus": [], "method": [], "stim": [], "subj": []},
    } for cid in emb_by_cid]

    # ---- Append idempotently (skip cids already present per file) ----------
    def _new_only(rows: list[dict], path: Path) -> list[dict]:
        have = _existing_cids(path)
        return [r for r in rows if r["canonical_id"] not in have]

    n_emb  = _append_jsonl(_F_EMB,  _new_only(emb_rows,  _F_EMB))
    n_pap  = _append_jsonl(_F_PAP,  _new_only(pap_rows,  _F_PAP))
    n_filt = _append_jsonl(_F_FILT, _new_only(filt_rows, _F_FILT))
    n_dim  = _append_jsonl(_F_DIM,  _new_only(dim_rows,  _F_DIM))
    print(f"[wire] appended  embeddings.jsonl={n_emb}  merged_papers.jsonl={n_pap}  "
          f"filter_decisions.jsonl={n_filt}  filter_decisions_dim.jsonl={n_dim}")

    # ---- DB UPSERT (optional) --------------------------------------------
    if args.apply:
        from _db import exec_many  # noqa: E402

        # archive_paper_embeddings — PK (canonical_id, model_name).
        emb_sql = (
            f"INSERT INTO {sch}.archive_paper_embeddings "
            "(canonical_id, model_name, dim, embedding_json, generated_at) "
            "VALUES (%s,%s,%s,%s::jsonb,%s) "
            "ON CONFLICT (canonical_id, model_name) DO UPDATE SET "
            "  dim = EXCLUDED.dim, "
            "  embedding_json = EXCLUDED.embedding_json, "
            "  generated_at = EXCLUDED.generated_at"
        )
        emb_params = [
            (cid, _MODEL_NAME, _DIM, json.dumps(emb_by_cid[cid]), ts)
            for cid in emb_by_cid
        ]
        n_db_emb = exec_many(emb_sql, emb_params)

        # archive_filter_decisions — PK canonical_id. Real columns:
        # canonical_id, is_textbook, is_draft, is_poster, is_lab_relevant,
        # lab_scope_tags(jsonb), filter_reason(jsonb), decided_at, dim_tags(jsonb).
        filt_sql = (
            f"INSERT INTO {sch}.archive_filter_decisions "
            "(canonical_id, is_textbook, is_draft, is_poster, is_lab_relevant, "
            " lab_scope_tags, filter_reason, decided_at, dim_tags) "
            "VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb) "
            "ON CONFLICT (canonical_id) DO UPDATE SET "
            "  is_textbook = EXCLUDED.is_textbook, "
            "  is_draft = EXCLUDED.is_draft, "
            "  is_poster = EXCLUDED.is_poster, "
            "  is_lab_relevant = EXCLUDED.is_lab_relevant, "
            "  lab_scope_tags = EXCLUDED.lab_scope_tags, "
            "  filter_reason = EXCLUDED.filter_reason, "
            "  decided_at = EXCLUDED.decided_at, "
            "  dim_tags = EXCLUDED.dim_tags"
        )
        empty_dim = json.dumps({"focus": [], "method": [], "stim": [], "subj": []})
        filt_params = [
            (cid, False, False, False, True,
             json.dumps([]), json.dumps("p26_live_discovery"), ts, empty_dim)
            for cid in emb_by_cid
        ]
        n_db_filt = exec_many(filt_sql, filt_params)
        print(f"[wire] DB UPSERT  archive_paper_embeddings={n_db_emb}  "
              f"archive_filter_decisions={n_db_filt}")
    else:
        print("[wire] DB dry-run (mirrors appended). Re-run with --apply to UPSERT DB.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
