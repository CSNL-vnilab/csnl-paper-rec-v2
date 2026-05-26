#!/usr/bin/env python3
"""
scripts/init_db.py — create the csnl_paper_rec PostgreSQL ledger schema +
tables from state/schema.sql (idempotent).

Ported from the predecessor sqlite init_db.py: same intent (apply schema,
verify tables), retargeted to PostgreSQL. Substitutes __SCHEMA__ with
$CPR_LEDGER_SCHEMA. Touches NO data rows. Never touches csnl_research.

OPERATOR-RUN (writes to the lab Supabase):
    ! python scripts/init_db.py

Safe to run repeatedly. Connection comes only from repo .env.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from _db import load_env, exec_sql, query_json, ledger_schema  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCHEMA_SQL         = _REPO_ROOT / "state" / "schema.sql"
_SCHEMA_V3_SQL      = _REPO_ROOT / "state" / "schema_v3.sql"        # cron/state machine
_SCHEMA_ARCHIVE_SQL = _REPO_ROOT / "state" / "schema_archive.sql"  # P13 archive layer

_TABLES = (
    "paper_recommendations",
    "recommendation_messages",
    "paper_recommendations_read",
    "feedback_events",
    "exclusion_rules",
    # v3 additions (cron state machine + evolution audit)
    "cycle_state",
    "evolution_log",
    # P13 archive layer (interview/marketplace plugin reference data)
    "archive_papers",
    "archive_paper_sources",
    "archive_filter_decisions",
    "archive_paper_embeddings",
    "archive_researcher_queues",
    "archive_interview_sessions",
    "archive_profile_verifications",
    "archive_responses",
    "archive_meta_reviews",
    # P14 dimension tagging (composite ranking + chunk mix)
    "archive_paper_dim_tags",
    # P19b evolution-workflow foundation
    "archive_queue_feedback",
    "archive_outcome_signals",
    "archive_evolution_proposals",
)


def main() -> int:
    load_env()
    schema = ledger_schema()
    if not _SCHEMA_SQL.exists():
        print(f"ERROR: schema not found: {_SCHEMA_SQL}")
        return 1

    ddl = _SCHEMA_SQL.read_text(encoding="utf-8").replace("__SCHEMA__", schema)
    print(f"[init_db] applying ledger schema '{schema}' (idempotent)…")
    exec_sql(ddl)
    if _SCHEMA_V3_SQL.exists():
        ddl_v3 = _SCHEMA_V3_SQL.read_text(encoding="utf-8").replace("__SCHEMA__", schema)
        print(f"[init_db] applying v3 cron schema extensions…")
        exec_sql(ddl_v3)
    if _SCHEMA_ARCHIVE_SQL.exists():
        ddl_arc = _SCHEMA_ARCHIVE_SQL.read_text(encoding="utf-8").replace("__SCHEMA__", schema)
        print(f"[init_db] applying P13 archive schema (papers + queues + responses)…")
        exec_sql(ddl_arc)

    present = query_json(
        "SELECT table_name FROM information_schema.tables "
        f"WHERE table_schema = '{schema}' ORDER BY table_name"
    )
    names = sorted(r["table_name"] for r in present)
    print(f"[init_db] tables in {schema}: {names}")

    missing = [t for t in _TABLES if t not in names]
    if missing:
        print(f"ERROR: tables missing after apply: {missing}")
        return 1
    print(f"[init_db] OK — {len(_TABLES)} ledger tables verified in '{schema}'.")
    print("[init_db] csnl_research untouched (read-only interest source).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
