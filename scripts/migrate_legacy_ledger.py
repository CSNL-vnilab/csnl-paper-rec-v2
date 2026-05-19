#!/usr/bin/env python3
"""
scripts/migrate_legacy_ledger.py — one-shot port of the predecessor's
sqlite ledger into the v2 csnl_paper_rec PostgreSQL schema.

SOURCE (READ-ONLY): the predecessor checkout's state/ledger.sqlite
  default: /Users/csnl/csnl_on_ai/csnl-paper-rec/state/ledger.sqlite
  override: --src <path>  or  $CPR_LEGACY_SQLITE
That sqlite is ALREADY in the new column shape (run_id, unit_id,
member_init, …) — the predecessor's own legacy migration ran there
(8 paper_recommendations + 1 read + 3 JOP exclusion_rules; messages /
feedback empty). So this is a straight, idempotent table copy with DOI
normalization and ON CONFLICT DO NOTHING.

TARGET: $CPR_LEDGER_SCHEMA (default csnl_paper_rec) — created by
scripts/init_db.py first. csnl_research is never touched.

OPERATOR-RUN:
    ! python scripts/init_db.py
    ! python scripts/migrate_legacy_ledger.py
Idempotent — safe to re-run.
"""
import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from _db import load_env, exec_many, query_json, ledger_schema  # noqa: E402

_DEFAULT_SRC = Path("/Users/csnl/csnl_on_ai/csnl-paper-rec/state/ledger.sqlite")


def _norm_doi(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    return s.strip().lower()


def _rows(con: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    cur = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    if not cur.fetchone():
        return []
    con.row_factory = sqlite3.Row
    return con.execute(f"SELECT * FROM {table}").fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.environ.get("CPR_LEGACY_SQLITE", str(_DEFAULT_SRC)))
    args = ap.parse_args()

    load_env()
    schema = ledger_schema()
    src = Path(args.src)
    if not src.exists():
        print(f"ERROR: source sqlite not found: {src}")
        return 1

    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    summary: dict[str, int] = {}

    # paper_recommendations  (PK unit_id, paper_doi)
    rows = _rows(con, "paper_recommendations")
    payload = [(
        r["run_id"], r["unit_id"], r["member_init"], r["channel_id"], r["slack_ts"],
        _norm_doi(r["paper_doi"]), r["paper_title"], r["paper_date"], r["tier"],
        r["posted_at"],
    ) for r in rows]
    summary["paper_recommendations"] = exec_many(
        f"INSERT INTO {schema}.paper_recommendations "
        "(run_id,unit_id,member_init,channel_id,slack_ts,paper_doi,"
        "paper_title,paper_date,tier,posted_at) VALUES "
        "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", payload)

    # recommendation_messages  (PK id, UNIQUE channel_id,message_ts)
    rows = _rows(con, "recommendation_messages")
    payload = [(
        r["id"], r["channel_id"], r["message_ts"], r["unit_id"],
        _norm_doi(r["paper_doi"]), r["posted_at"], r["context_json"],
    ) for r in rows]
    summary["recommendation_messages"] = exec_many(
        f"INSERT INTO {schema}.recommendation_messages "
        "(id,channel_id,message_ts,unit_id,paper_doi,posted_at,context_json) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", payload)

    # paper_recommendations_read  (no unique → dedup vs existing)
    rows = _rows(con, "paper_recommendations_read")
    existing = {
        (x["unit_id"], x["member_init"], _norm_doi(x["paper_doi"]))
        for x in query_json(
            f"SELECT unit_id,member_init,paper_doi FROM {schema}.paper_recommendations_read")
    }
    payload = []
    for r in rows:
        key = (r["unit_id"], r["member_init"], _norm_doi(r["paper_doi"]))
        if key in existing:
            continue
        existing.add(key)
        payload.append((r["unit_id"], r["member_init"], _norm_doi(r["paper_doi"]),
                        r["paper_title"], r["marked_read_at"]))
    summary["paper_recommendations_read"] = exec_many(
        f"INSERT INTO {schema}.paper_recommendations_read "
        "(unit_id,member_init,paper_doi,paper_title,marked_read_at) "
        "VALUES (%s,%s,%s,%s,%s)", payload)

    # feedback_events  (PK id, UNIQUE idem_key)
    rows = _rows(con, "feedback_events")
    payload = [(
        r["id"], r["occurred_at"], _norm_doi(r["recommendation_doi"]),
        r["unit_id"], r["member_init"], r["signal"], r["payload_json"],
        r["idem_key"],
    ) for r in rows]
    summary["feedback_events"] = exec_many(
        f"INSERT INTO {schema}.feedback_events "
        "(id,occurred_at,recommendation_doi,unit_id,member_init,signal,"
        "payload_json,idem_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT DO NOTHING", payload)

    # exclusion_rules  (UNIQUE unit_id,excluded_term)
    rows = _rows(con, "exclusion_rules")
    payload = [(
        r["unit_id"], r["member_init"], r["excluded_term"], r["reason"],
        r["declared_at"], r["source"],
    ) for r in rows]
    summary["exclusion_rules"] = exec_many(
        f"INSERT INTO {schema}.exclusion_rules "
        "(unit_id,member_init,excluded_term,reason,declared_at,source) "
        "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", payload)

    con.close()

    print("\n" + "=" * 56)
    print(f"  MIGRATION → {schema}  (source: {src})")
    print("=" * 56)
    total = 0
    for t, n in summary.items():
        total += n
        print(f"  {t:32s} : {n} row(s) written (idempotent)")
    print(f"\n  TOTAL written this run: {total}")
    # Post-state counts (authoritative)
    for t in summary:
        c = query_json(f"SELECT count(*) n FROM {schema}.{t}")[0]["n"]
        print(f"  {t:32s} : {c} row(s) now in ledger")
    print("=" * 56)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
