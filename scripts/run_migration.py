#!/usr/bin/env python3
"""
scripts/run_migration.py — apply one SQL migration file via psycopg2 when
the local environment doesn't have the psql CLI installed (which is the
case on the lab's macOS researcher machines).

The lab's data plane uses pipeline/_db.py for everything else; this
script borrows the same connection helpers and uses an autocommit
connection so the BEGIN/COMMIT block inside the migration SQL is what
defines the transaction boundary (rather than psycopg2's implicit one).

OPERATOR-RUN (writes to csnl_paper_rec):
    ! python3 scripts/run_migration.py state/migrations/2026-05-28_drop_tell_me_more.sql

Idempotent if the SQL itself is idempotent. Streams Postgres NOTICE
messages from the migration script back to stdout so the operator can
see the pre/post sanity-rail counts.

No prod-DB connection unless invoked by the operator with `!`. csnl_research
is never touched (the schema name is hard-checked by pipeline/_db.py).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
from _db import load_env, ledger_schema  # noqa: E402


def _have_psycopg2() -> bool:
    try:
        import psycopg2  # noqa: F401
        return True
    except ImportError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply one SQL migration file.")
    ap.add_argument("path", help="Path to the .sql migration to apply.")
    args = ap.parse_args()

    sql_path = Path(args.path).resolve()
    if not sql_path.is_file():
        print(f"FATAL: not a file: {sql_path}", file=sys.stderr)
        return 2
    if not sql_path.name.endswith(".sql"):
        print(f"FATAL: not a .sql file: {sql_path.name}", file=sys.stderr)
        return 2

    load_env()
    schema = ledger_schema()  # also enforces 'not csnl_research'
    sql_text = sql_path.read_text(encoding="utf-8")
    # We don't substitute __SCHEMA__ here — the migration is responsible
    # for naming its own target schema, and ours is hardcoded to
    # csnl_paper_rec. If a future migration parameterises by __SCHEMA__,
    # add a substitution step. For now, sanity-check.
    if "__SCHEMA__" in sql_text:
        print(f"FATAL: migration contains __SCHEMA__ placeholder; substitute "
              f"with ledger_schema()={schema} before running.", file=sys.stderr)
        return 2

    if not _have_psycopg2():
        print("FATAL: psycopg2 not available. Install with: "
              "python3 -m pip install --user psycopg2-binary", file=sys.stderr)
        return 2

    import psycopg2  # noqa: E402

    # Connect via _db._conn() so we honour the same .env config + schema
    # guards everything else uses.
    from _db import _conn  # type: ignore  # noqa: E402
    conn = _conn()
    inserted_notices = 0
    try:
        # autocommit so BEGIN/COMMIT in the migration file defines the
        # transaction (not psycopg2's implicit one — the migration may use
        # multiple BEGIN/COMMIT pairs, or none, and we trust its author).
        conn.autocommit = True

        print(f"[run_migration] applying: {sql_path}", flush=True)
        print(f"[run_migration] schema  : {schema}", flush=True)
        print(f"[run_migration] size    : {len(sql_text)} bytes", flush=True)
        print("-" * 60, flush=True)

        with conn.cursor() as cur:
            try:
                cur.execute(sql_text)
            except psycopg2.Error as e:
                # Surface NOTICE messages that came in before the failure.
                for n in conn.notices:
                    print(f"[NOTICE] {n.rstrip()}", flush=True)
                    inserted_notices += 1
                print(f"\nERROR: {e.pgcode or ''} {e}", file=sys.stderr, flush=True)
                return 1

        # Drain notices from the success path too.
        for n in conn.notices:
            print(f"[NOTICE] {n.rstrip()}", flush=True)
            inserted_notices += 1
        print("-" * 60, flush=True)
        print(f"[run_migration] OK — {inserted_notices} NOTICE messages.",
              flush=True)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
