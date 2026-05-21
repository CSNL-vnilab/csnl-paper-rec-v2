"""
plugin/scripts/_pdb.py — minimal DB client for the marketplace plugin.

Resolves env from (in priority order):
  1. environment already loaded
  2. <plugin-dir>/.env                  (wins over the home-dir file)
  3. ~/.csnl-paper-archive/.env          (fallback)

Connects to the lab Supabase via psycopg2 (preferred) or `psql` CLI fallback.
This is intentionally a small subset of pipeline/_db.py — the plugin is
designed to be installable on a researcher's laptop without the full harness.

Boundaries (enforced — see `_assert_archive_write` below):
  * Reads:   any table in `<schema>.archive_*` (plus csnl_research read-only
             via SELECT statements that profile_show.py shapes).
  * Writes:  ONLY tables matching the regex `^archive_(interview_sessions|
             profile_verifications|responses|meta_reviews)$`.
  * NEVER writes to csnl_research, paper_recommendations, feedback_events,
    cycle_state, recommendation_messages, exclusion_rules, evolution_log,
    paper_recommendations_read, archive_papers, archive_paper_sources,
    archive_filter_decisions, archive_paper_embeddings,
    archive_researcher_queues. (Those are operator-only tables.)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

PLUGIN_DIR = Path(__file__).resolve().parent.parent

_ENV_PATHS = [
    PLUGIN_DIR / ".env",
    Path.home() / ".csnl-paper-archive" / ".env",
]


def load_env() -> None:
    for p in _ENV_PATHS:
        if not p.exists():
            continue
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k and k not in os.environ:
                os.environ[k] = v


def schema() -> str:
    s = os.environ.get("CPR_LEDGER_SCHEMA", "csnl_paper_rec").strip()
    if s in ("csnl_research", "public", "information_schema", "pg_catalog"):
        raise RuntimeError(f"Refusing to use {s!r} as the schema.")
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", s):
        raise RuntimeError(f"Schema name has unsafe characters: {s!r}")
    return s


# Strict writeable-table allowlist. Any INSERT/UPDATE/DELETE issued through
# this plugin must target one of these table names; everything else (notably
# archive_papers, archive_paper_sources, archive_filter_decisions,
# archive_paper_embeddings, archive_researcher_queues) is operator-only.
_PLUGIN_WRITABLE = (
    "archive_interview_sessions",
    "archive_profile_verifications",
    "archive_responses",
    "archive_meta_reviews",
)

# Matches INSERT/UPDATE/DELETE INTO <schema>.<table>, capturing the table name.
_WRITE_TARGET_RE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"
    r"([a-zA-Z_][a-zA-Z0-9_]*)\s*\.\s*([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)
_WRITE_KEYWORD_RE = re.compile(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|MERGE|COPY)\b",
                               re.IGNORECASE)


def _strip_sql_comments(sql: str) -> str:
    """Remove `-- line comments` and `/* block comments */` so the write
    detector cannot be hidden behind a comment."""
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return sql


def _has_multi_statement(sql: str) -> bool:
    """Detect more than one statement by counting unquoted semicolons.

    `''`-escaped single quotes inside string literals don't open a real
    string boundary; we walk the string explicitly. A single trailing `;`
    on the last statement is allowed.
    """
    stripped = _strip_sql_comments(sql)
    in_str = False
    n_terminators = 0
    i = 0
    L = len(stripped)
    while i < L:
        ch = stripped[i]
        if in_str:
            if ch == "'":
                if i + 1 < L and stripped[i + 1] == "'":
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if ch == "'":
            in_str = True
        elif ch == ";":
            # Skip trailing whitespace/comments after this semicolon.
            j = i + 1
            while j < L and stripped[j].isspace():
                j += 1
            if j < L:
                n_terminators += 1
                if n_terminators >= 1:
                    return True
        i += 1
    return False


def _assert_archive_write(sql: str) -> None:
    """Belt-and-suspenders: refuse to run a write statement against any
    table outside the plugin's narrow writeable allowlist, and refuse
    multi-statement SQL outright. Called from BOTH query() and exec_sql(),
    so a `WITH del AS (DELETE ... RETURNING)` smuggled through `query()`
    is also rejected.
    """
    if _has_multi_statement(sql):
        raise RuntimeError(
            "Refusing multi-statement SQL. Issue one statement per call."
        )
    stripped = _strip_sql_comments(sql)
    if not _WRITE_KEYWORD_RE.search(stripped):
        return
    targets = _WRITE_TARGET_RE.findall(stripped)
    if not targets:
        # Write keyword without a schema-qualified target — refuse.
        raise RuntimeError("Refusing write SQL with no schema-qualified target")
    # Additionally reject TRUNCATE / DROP / ALTER / MERGE / COPY anywhere
    # in the statement (these aren't covered by the _WRITE_TARGET_RE).
    dangerous = re.search(r"\b(TRUNCATE|DROP|ALTER|MERGE|COPY)\b", stripped,
                          re.IGNORECASE)
    if dangerous:
        raise RuntimeError(
            f"Refusing {dangerous.group(0)} from the plugin layer; "
            f"operator-only operation."
        )
    for sch, tbl in targets:
        if tbl not in _PLUGIN_WRITABLE:
            raise RuntimeError(
                f"Refusing to write to {sch}.{tbl}: plugin allowlist is "
                f"{_PLUGIN_WRITABLE}. (This boundary is enforced in "
                f"plugin/scripts/_pdb.py; ask the operator if you need a "
                f"wider permission.)"
            )


def _req(key: str) -> str:
    v = os.environ.get(key)
    if not v:
        raise RuntimeError(
            f"Missing required env var: {key}. Copy plugin/.env.example to "
            f".env in {PLUGIN_DIR} (or ~/.csnl-paper-archive/.env) and fill it.")
    return v


def _have_psycopg2() -> bool:
    try:
        import psycopg2  # noqa: F401
        return True
    except ImportError:
        return False


def _conn():
    import psycopg2
    return psycopg2.connect(
        host=_req("SUPABASE_DB_HOST"),
        port=int(os.environ.get("SUPABASE_DB_PORT", "5432")),
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"),
        user=_req("SUPABASE_DB_USER"),
        password=_req("SUPABASE_DB_PASSWORD"),
        connect_timeout=20,
    )


def _psql_args() -> list[str]:
    return [
        "psql",
        "-h", _req("SUPABASE_DB_HOST"),
        "-p", os.environ.get("SUPABASE_DB_PORT", "5432"),
        "-U", _req("SUPABASE_DB_USER"),
        "-d", os.environ.get("SUPABASE_DB_NAME", "postgres"),
        "-v", "ON_ERROR_STOP=1",
    ]


def _psql_env() -> dict:
    return dict(os.environ, PGPASSWORD=_req("SUPABASE_DB_PASSWORD"))


def _splice_params(sql: str, params: tuple) -> str:
    """Replace each `%s` placeholder with a safely-quoted SQL literal.

    Uses a tokenize-and-rejoin approach so a literal value containing the
    substring `%s` cannot collide with another placeholder. NUL bytes are
    rejected (Postgres TEXT cannot store them). Numeric / bool / None map
    to their canonical SQL form. Strings escape `'` → `''` and rely on
    Postgres `standard_conforming_strings=on` (the Supabase default) so
    backslashes are literal.
    """
    parts = sql.split("%s")
    if len(parts) - 1 != len(params):
        raise RuntimeError(
            f"placeholder count mismatch: sql has {len(parts)-1} %s, "
            f"params has {len(params)}"
        )
    out = [parts[0]]
    for v, tail in zip(params, parts[1:]):
        if v is None:
            lit = "NULL"
        elif isinstance(v, bool):
            lit = "TRUE" if v else "FALSE"
        elif isinstance(v, (int, float)):
            lit = repr(v)
        else:
            s = str(v)
            if "\x00" in s:
                raise RuntimeError("Refusing SQL literal with NUL byte")
            lit = "'" + s.replace("'", "''") + "'"
        out.append(lit)
        out.append(tail)
    return "".join(out)


def query(sql: str, params: tuple | None = None) -> list[dict]:
    """SELECT → list[dict]. JSON-aggregated when falling back to psql.

    Calls `_assert_archive_write` so that data-modifying CTEs hidden in
    a SELECT (e.g. `WITH del AS (DELETE ... RETURNING ...) SELECT *`)
    are blocked just like a plain INSERT/UPDATE/DELETE.
    """
    _assert_archive_write(sql)
    if _have_psycopg2():
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
                cols = [d.name for d in cur.description]
                rows = cur.fetchall()
                return [dict(zip(cols, r)) for r in rows]
        finally:
            conn.close()
    if params:
        sql = _splice_params(sql, params)
    wrapped = (
        "SELECT coalesce(json_agg(t), '[]'::json) FROM ( " +
        sql.rstrip().rstrip(";") + " ) t;"
    )
    proc = subprocess.run(
        _psql_args() + ["-tAc", wrapped],
        capture_output=True, text=True, env=_psql_env(), timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql query failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout.strip() or "[]")


def exec_sql(sql: str, params: tuple | None = None) -> None:
    _assert_archive_write(sql)
    if _have_psycopg2():
        conn = _conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
        finally:
            conn.close()
        return
    if params:
        sql = _splice_params(sql, params)
    proc = subprocess.run(
        _psql_args() + ["-q", "-c", sql],
        capture_output=True, text=True, env=_psql_env(), timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql exec failed: {proc.stderr.strip()}")
