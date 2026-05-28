#!/usr/bin/env python3
"""
scripts/archive/import_synopses.py — load per-paper synopsis JSON cache
from state/archive/synopses/*.json into csnl_paper_rec.archive_paper_synopses.

This is P21 ship-step. The 10h auto-mode loop produced 1205 skeletal
synopsis JSON files; this script lifts them into Postgres so the queue
builder and SKILL.md Stage 2 Block 2 can read them. Idempotent UPSERT
on canonical_id PK.

OPERATOR-RUN (writes to csnl_paper_rec):
    ! python3 scripts/archive/import_synopses.py             # dry-run
    ! python3 scripts/archive/import_synopses.py --apply     # do the writes

Re-run policy: existing rows with review_status='human_approved' are
preserved (we do NOT overwrite review status from the cache); all other
columns are refreshed from the JSON file. Rows missing from the cache
are left alone (we never delete).

No LLM. No network beyond _db.py (csnl_paper_rec write).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
from _db import load_env, exec_sql, exec_many, query_json, ledger_schema  # noqa: E402

KST = timezone(timedelta(hours=9))
SYNOPSES_DIR = _REPO_ROOT / "state" / "archive" / "synopses"

JSONB_FIELDS = (
    "frameworks",
    "key_assumptions",
    "manipulations",
    "key_findings",
    "interpretations",
    "limitations_noted",
    "connecting_signals",
)
ALLOWED_STATUS = {
    "auto_unreviewed",
    "meta_reviewed",
    "human_approved",
    "needs_rework",
}

REQUIRED_WRAPPER_FIELDS = ("canonical_id", "synopsis_version", "generator", "generated_at")


# ---------------------------------------------------------------- normalize


def _kst_iso() -> str:
    return datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")


def _coerce_array(v):
    """Force JSONB array. None/non-list → []."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    # Defensive: a string would corrupt the JSONB column. Treat as single-element.
    return [v]


def _validate_frameworks(fw, cid):
    """frameworks must be a list of dicts shaped {name, role, one_line}.
    Downstream pick_next.py / SKILL.md Stage 2 Block 2 assume that shape;
    a stringy or malformed value would corrupt those readers
    (codex adversarial review finding #8 — MEDIUM).

    Returns the validated list, or raises ValueError so _load_cache can skip
    the file and surface it in the SKIPPED report.
    """
    if not isinstance(fw, list):
        raise ValueError(f"frameworks must be a list, got {type(fw).__name__}")
    for i, item in enumerate(fw):
        if not isinstance(item, dict):
            raise ValueError(f"frameworks[{i}] is not a dict (got {type(item).__name__})")
        if "name" not in item or not isinstance(item.get("name"), str):
            raise ValueError(f"frameworks[{i}] missing string `name`")
    return fw


def _coerce_coverage(v):
    if v is None:
        return None
    if not isinstance(v, (int, float)):
        return None
    f = float(v)
    if f != f:  # NaN
        return None
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def _row_from_json(cid: str, d: dict) -> dict:
    """Map JSON synopsis to a row dict matching the schema columns."""
    status = d.get("review_status") or "auto_unreviewed"
    if status not in ALLOWED_STATUS:
        status = "auto_unreviewed"
    return {
        "canonical_id": cid,
        "synopsis_version": d.get("synopsis_version") or "v1.2026-05-27",
        "frameworks": _validate_frameworks(_coerce_array(d.get("frameworks")), cid),
        "core_question": d.get("core_question"),
        "key_assumptions": _coerce_array(d.get("key_assumptions")),
        "manipulations": _coerce_array(d.get("manipulations")),
        "key_findings": _coerce_array(d.get("key_findings")),
        "interpretations": _coerce_array(d.get("interpretations")),
        "limitations_noted": _coerce_array(d.get("limitations_noted")),
        "connecting_signals": _coerce_array(d.get("connecting_signals")),
        "out_of_scope_note": d.get("out_of_scope_note"),
        "generator": d.get("generator") or "opus-4-7@2026-05-27",
        "review_status": status,
        "generated_at": d.get("generated_at") or _kst_iso(),
        "abstract_coverage": _coerce_coverage(d.get("abstract_coverage")),
    }


def _row_to_tuple(row: dict) -> tuple:
    """Convert row dict to positional tuple matching UPSERT_SQL placeholders."""
    return (
        row["canonical_id"],
        row["synopsis_version"],
        json.dumps(row["frameworks"], ensure_ascii=False),
        row["core_question"],
        json.dumps(row["key_assumptions"], ensure_ascii=False),
        json.dumps(row["manipulations"], ensure_ascii=False),
        json.dumps(row["key_findings"], ensure_ascii=False),
        json.dumps(row["interpretations"], ensure_ascii=False),
        json.dumps(row["limitations_noted"], ensure_ascii=False),
        json.dumps(row["connecting_signals"], ensure_ascii=False),
        row["out_of_scope_note"],
        row["generator"],
        row["review_status"],
        row["generated_at"],
        row["abstract_coverage"],
    )


def _upsert_sql(schema: str) -> str:
    return f"""
INSERT INTO {schema}.archive_paper_synopses (
    canonical_id, synopsis_version,
    frameworks, core_question,
    key_assumptions, manipulations,
    key_findings, interpretations,
    limitations_noted, connecting_signals,
    out_of_scope_note, generator,
    review_status, generated_at, abstract_coverage
) VALUES (
    %s, %s,
    %s::jsonb, %s,
    %s::jsonb, %s::jsonb,
    %s::jsonb, %s::jsonb,
    %s::jsonb, %s::jsonb,
    %s, %s,
    %s, %s, %s
)
ON CONFLICT (canonical_id) DO UPDATE SET
    -- Substantive content columns: PRESERVE every field on rows that have
    -- review_status='human_approved'. A human may have hand-edited those rows;
    -- re-running the import must never silently destroy that work.
    -- (codex adversarial review finding #1 — CRITICAL)
    synopsis_version = CASE
        WHEN {schema}.archive_paper_synopses.review_status = 'human_approved'
            THEN {schema}.archive_paper_synopses.synopsis_version
        ELSE EXCLUDED.synopsis_version END,
    frameworks = CASE
        WHEN {schema}.archive_paper_synopses.review_status = 'human_approved'
            THEN {schema}.archive_paper_synopses.frameworks
        ELSE EXCLUDED.frameworks END,
    core_question = CASE
        WHEN {schema}.archive_paper_synopses.review_status = 'human_approved'
            THEN {schema}.archive_paper_synopses.core_question
        ELSE EXCLUDED.core_question END,
    key_assumptions = CASE
        WHEN {schema}.archive_paper_synopses.review_status = 'human_approved'
            THEN {schema}.archive_paper_synopses.key_assumptions
        ELSE EXCLUDED.key_assumptions END,
    manipulations = CASE
        WHEN {schema}.archive_paper_synopses.review_status = 'human_approved'
            THEN {schema}.archive_paper_synopses.manipulations
        ELSE EXCLUDED.manipulations END,
    key_findings = CASE
        WHEN {schema}.archive_paper_synopses.review_status = 'human_approved'
            THEN {schema}.archive_paper_synopses.key_findings
        ELSE EXCLUDED.key_findings END,
    interpretations = CASE
        WHEN {schema}.archive_paper_synopses.review_status = 'human_approved'
            THEN {schema}.archive_paper_synopses.interpretations
        ELSE EXCLUDED.interpretations END,
    limitations_noted = CASE
        WHEN {schema}.archive_paper_synopses.review_status = 'human_approved'
            THEN {schema}.archive_paper_synopses.limitations_noted
        ELSE EXCLUDED.limitations_noted END,
    connecting_signals = CASE
        WHEN {schema}.archive_paper_synopses.review_status = 'human_approved'
            THEN {schema}.archive_paper_synopses.connecting_signals
        ELSE EXCLUDED.connecting_signals END,
    out_of_scope_note = CASE
        WHEN {schema}.archive_paper_synopses.review_status = 'human_approved'
            THEN {schema}.archive_paper_synopses.out_of_scope_note
        ELSE EXCLUDED.out_of_scope_note END,
    abstract_coverage = CASE
        WHEN {schema}.archive_paper_synopses.review_status = 'human_approved'
            THEN {schema}.archive_paper_synopses.abstract_coverage
        ELSE EXCLUDED.abstract_coverage END,
    -- Provenance metadata: always refresh (these track WHO/WHEN wrote the row;
    -- a human approving a row does NOT mean we want to lie about provenance).
    generator    = EXCLUDED.generator,
    generated_at = EXCLUDED.generated_at,
    -- review_status: preserve human_approved; otherwise accept incoming.
    review_status = CASE
        WHEN {schema}.archive_paper_synopses.review_status = 'human_approved'
            THEN {schema}.archive_paper_synopses.review_status
        ELSE EXCLUDED.review_status END;
    -- NOTE: meta_reviewed_at intentionally NOT in SET — that column is owned by
    -- a separate human/agent review step and must survive re-imports untouched.
"""


# --------------------------------------------------------------------- main


def _load_cache(limit: int | None) -> tuple[list[dict], list[tuple[str, str]]]:
    """Read every JSON in the synopsis cache. Returns (rows, skipped)."""
    if not SYNOPSES_DIR.is_dir():
        raise SystemExit(f"FATAL: synopsis cache not found at {SYNOPSES_DIR}")
    paths = sorted(glob.glob(str(SYNOPSES_DIR / "*.json")))
    if limit:
        paths = paths[:limit]
    rows: list[dict] = []
    skipped: list[tuple[str, str]] = []
    seen_cids: set[str] = set()
    for p in paths:
        fname = os.path.basename(p)
        cid_file = fname[:-5]
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            skipped.append((fname, f"json parse: {e}"))
            continue
        cid_data = d.get("canonical_id")
        if cid_data and cid_data != cid_file:
            # Filename is the source of truth (matches merged_papers.jsonl which
            # is the canonical_id the rest of the pipeline joins on). Don't drop
            # the file — warn loudly and proceed with the filename CID. This
            # matters because the auto-loop's hash discrepancy left 4 priority-
            # queue cids pointing at slightly-different on-disk cids; silently
            # dropping them would discard real synopsis content.
            # (codex adversarial review finding #5 — LOW)
            print(f"[import_synopses] WARN: {fname}: canonical_id mismatch "
                  f"file={cid_file} data={cid_data}; using filename as PK",
                  flush=True)
        if cid_file in seen_cids:
            skipped.append((fname, "duplicate cid in cache"))
            continue
        seen_cids.add(cid_file)
        try:
            rows.append(_row_from_json(cid_file, d))
        except Exception as e:
            skipped.append((fname, f"normalize: {e}"))
    return rows, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description="Import P21 synopsis JSON cache into csnl_paper_rec.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write to Postgres (default = dry-run).")
    ap.add_argument("--limit", type=int, default=None,
                    help="Only process the first N files (for smoke tests).")
    ap.add_argument("--batch-size", type=int, default=200,
                    help="Rows per execute_batch call (psycopg2 path).")
    args = ap.parse_args()

    load_env()
    schema = ledger_schema()

    rows, skipped = _load_cache(args.limit)
    print(f"[import_synopses] parsed={len(rows)} from {SYNOPSES_DIR}", flush=True)
    if skipped:
        print(f"[import_synopses] SKIPPED {len(skipped)} files:", flush=True)
        for fname, reason in skipped[:20]:
            print(f"    {fname}: {reason}", flush=True)
        if len(skipped) > 20:
            print(f"    … +{len(skipped) - 20} more", flush=True)

    # Distribution check (for the dry-run summary).
    in_scope = sum(1 for r in rows if not r["out_of_scope_note"])
    out_of = len(rows) - in_scope
    versions = sorted({r["synopsis_version"] for r in rows})
    generators = sorted({r["generator"] for r in rows})
    statuses: dict[str, int] = {}
    for r in rows:
        statuses[r["review_status"]] = statuses.get(r["review_status"], 0) + 1
    print(f"[import_synopses] in-scope={in_scope}  out-of-scope={out_of}", flush=True)
    print(f"[import_synopses] versions={versions}  generators={generators}", flush=True)
    print(f"[import_synopses] review_status distribution: {statuses}", flush=True)

    if not args.apply:
        print("[import_synopses] DRY-RUN — pass --apply to write.", flush=True)
        if rows:
            sample = dict(rows[0])
            for k in JSONB_FIELDS:
                v = sample[k]
                s = json.dumps(v, ensure_ascii=False)
                sample[k] = s if len(s) <= 120 else s[:117] + "…"
            print("[import_synopses] sample row:")
            print(json.dumps(sample, ensure_ascii=False, indent=2))
        return 0

    # Sanity: count existing rows BEFORE write, so we can compute inserted/updated.
    pre_existing = set()
    try:
        pre = query_json(f"SELECT canonical_id FROM {schema}.archive_paper_synopses")
        pre_existing = {r["canonical_id"] for r in pre}
    except Exception as e:
        # Table missing → operator forgot init_db.py.
        raise SystemExit(f"FATAL: cannot read {schema}.archive_paper_synopses ({e}). "
                         f"Run scripts/init_db.py first.")
    print(f"[import_synopses] pre-existing rows in DB: {len(pre_existing)}", flush=True)

    sql = _upsert_sql(schema)
    tuples = [_row_to_tuple(r) for r in rows]

    # Chunk for backend memory safety. exec_many uses execute_batch internally.
    written = 0
    bs = max(1, args.batch_size)
    for i in range(0, len(tuples), bs):
        chunk = tuples[i:i + bs]
        n = exec_many(sql, chunk)
        written += n
        print(f"[import_synopses] wrote chunk {i//bs + 1} ({len(chunk)} rows, cumulative={written})",
              flush=True)

    # Verify post-state.
    # (codex adversarial review finding #10 — HIGH: the old check compared
    # db_total to len(rows), which falsely passed whenever a prior run had
    # already populated some rows. Fix: compare new-insert sets, so missing
    # writes show up regardless of pre-existing baseline.)
    post = query_json(f"SELECT canonical_id FROM {schema}.archive_paper_synopses")
    post_ids = {r["canonical_id"] for r in post}
    import_cids = {r["canonical_id"] for r in rows}
    expected_new_cids = import_cids - pre_existing
    actual_new_cids = post_ids - pre_existing
    inserted = len(actual_new_cids)
    updated = len(import_cids & pre_existing)
    missing_after_import = (import_cids - post_ids)  # we tried to write these and they aren't there
    print(f"[import_synopses] DONE — inserted={inserted}  updated={updated}  "
          f"db_total={len(post_ids)}", flush=True)

    if missing_after_import:
        print(f"[import_synopses] ERROR: {len(missing_after_import)} cids from cache are "
              f"missing in DB after write. First 10:", flush=True)
        for cid in sorted(missing_after_import)[:10]:
            print(f"    {cid}", flush=True)
        return 1
    if inserted < len(expected_new_cids):
        print(f"[import_synopses] WARN: expected to insert {len(expected_new_cids)} new rows, "
              f"actually inserted {inserted}. Some UPSERTs may have failed silently.",
              flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
