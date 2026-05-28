"""
pipeline/_db.py — shared PostgreSQL access for the v2 ledger.

DATA PLANE = PostgreSQL only (one Supabase project):
  - csnl_research   : interest source — READ-ONLY. NEVER written here.
  - csnl_paper_rec  : recommendation ledger — read-write (this module).

Connection comes ONLY from the repo-local .env (loaded here). Primary path
is psycopg2; fallback is the `psql` CLI (psycopg2 is absent in the system
Python — the lab's proven pattern, same as pipeline/00_select_projects.py).

These scripts are operator-run via `!` — when the operator runs them, the
psql/psycopg2 connection is the operator's own process, not agent-held DB
access. No Anthropic/OpenRouter/Ollama anywhere.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# .env loader (so `! python scripts/foo.py` works without manual export)
# ---------------------------------------------------------------------------

def load_env(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from repo .env into os.environ (no overwrite)."""
    env_path = path or (_REPO_ROOT / ".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if key and key not in os.environ:
            os.environ[key] = val


def _req(key: str) -> str:
    v = os.environ.get(key)
    if not v:
        raise RuntimeError(f"Missing required env var: {key} (fill .env)")
    return v


def ledger_schema() -> str:
    """Read-write ledger schema name (never csnl_research)."""
    s = os.environ.get("CPR_LEDGER_SCHEMA", "csnl_paper_rec").strip()
    if s in ("csnl_research", "public", "information_schema", "pg_catalog"):
        raise RuntimeError(f"Refusing to use {s!r} as the ledger schema.")
    return s


# ---------------------------------------------------------------------------
# psycopg2 primary / psql fallback
# ---------------------------------------------------------------------------

def _have_psycopg2() -> bool:
    try:
        import psycopg2  # noqa: F401
        return True
    except ImportError:
        return False


def _conn():
    """psycopg2 connection from SUPABASE_DB_* (primary path)."""
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


def exec_sql(sql: str) -> None:
    """Execute statement(s) with no result set (DDL / DML). Idempotent-safe."""
    if _have_psycopg2():
        conn = _conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(sql)
        finally:
            conn.close()
        return
    proc = subprocess.run(
        _psql_args() + ["-q", "-c", sql],
        capture_output=True, text=True, encoding="utf-8",
        env=_psql_env(), timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql exec failed: {proc.stderr.strip()}")


def exec_many(sql: str, rows: list[tuple]) -> int:
    """Execute a parameterized INSERT for many rows. Returns affected count.

    psycopg2 path uses real params. psql fallback writes a temp SQL file
    with literal-escaped values (single quotes doubled) — values here are
    our own ledger data, never researcher free-text.
    """
    if not rows:
        return 0
    if _have_psycopg2():
        import psycopg2.extras
        conn = _conn()
        try:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, sql, rows)
            conn.commit()
            n = len(rows)
        finally:
            conn.close()
        return n
    # psql fallback: build VALUES with escaping; sql must use %s placeholders
    def lit(v):
        if v is None:
            return "NULL"
        return "'" + str(v).replace("'", "''") + "'"
    stmts = []
    for r in rows:
        filled = sql
        for v in r:
            filled = filled.replace("%s", lit(v), 1)
        stmts.append(filled if filled.rstrip().endswith(";") else filled + ";")
    # Wrap all statements in an explicit transaction so the psql fallback path
    # matches the psycopg2 path's chunk-atomicity. Without this, a failure on
    # statement N leaves statements 1..N-1 committed and N+1..M skipped
    # (codex adversarial review finding #6 — HIGH).
    payload = "BEGIN;\n" + "\n".join(stmts) + "\nCOMMIT;\n"
    proc = subprocess.run(
        _psql_args() + ["-q", "-v", "ON_ERROR_STOP=1", "-c", payload],
        capture_output=True, text=True, encoding="utf-8",
        env=_psql_env(), timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql exec_many failed: {proc.stderr.strip()}")
    return len(rows)


def query_json(sql: str) -> list[dict]:
    """Run a SELECT and return rows as list[dict] (json_agg wrapped)."""
    wrapped = f"SELECT coalesce(json_agg(t), '[]'::json) FROM ( {sql.rstrip().rstrip(';')} ) t;"
    if _have_psycopg2():
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(wrapped)
                return cur.fetchone()[0] or []
        finally:
            conn.close()
    proc = subprocess.run(
        _psql_args() + ["-tAc", wrapped],
        capture_output=True, text=True, encoding="utf-8",
        env=_psql_env(), timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql query failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout.strip() or "[]")
