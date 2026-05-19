#!/usr/bin/env python3
"""
pipeline/00_select_projects.py — Query csnl_research.projects from Supabase
and emit state/runs/<RID>/01_active_projects.json.

READ-ONLY against csnl_research (SELECT only; never writes that schema).
Connects via SUPABASE_DB_* env vars using psycopg2 + postgres pooler role.

Usage:
  python pipeline/00_select_projects.py [RUN_ID]
  python pipeline/00_select_projects.py --run-id=20260518-1430

Emits: 01_active_projects.json
  {"run_id":..., "generated_at":..., "projects":[{...},...]}
"""
import os
import sys
import json

# Pipeline utilities (workstream 3 shared helpers)
sys.path.insert(0, os.path.dirname(__file__))
from _util import dump_stage, kst_now_str, resolve_run_id

# ---------------------------------------------------------------------------
# Exact query from BUILD_SPEC.md — do not modify
# ---------------------------------------------------------------------------
_QUERY = """
SELECT init, project_slug, title, phase, confidence_avg,
       to_char(last_updated_at AT TIME ZONE 'Asia/Seoul','YYYY-MM-DD HH24:MI') AS last_updated_kst,
       purpose_jsonb, background_jsonb, connected_graph_jsonb,
       manipulation_variables_jsonb, modalities_jsonb
FROM csnl_research.projects
WHERE phase IN ('data_collection','analysis','manuscript_draft')
  AND confidence_avg >= 0.7
ORDER BY init, project_slug
"""

# Friendly column aliases (jsonb columns rename to drop _jsonb suffix)
_COL_MAP = {
    "purpose_jsonb":               "purpose",
    "background_jsonb":            "background",
    "connected_graph_jsonb":       "connected_graph",
    "manipulation_variables_jsonb":"manipulation_variables",
    "modalities_jsonb":            "modalities",
}


def _env_required(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


def _connect():
    """Open psycopg2 connection from SUPABASE_DB_* env vars.

    Expected env:
      SUPABASE_DB_HOST, SUPABASE_DB_PORT, SUPABASE_DB_NAME,
      SUPABASE_DB_USER, SUPABASE_DB_PASSWORD
    The pooler role is typically 'postgres' or a read-only role.
    """
    try:
        import psycopg2
    except ImportError:
        raise RuntimeError("psycopg2 not installed — pip install psycopg2-binary")

    conn = psycopg2.connect(
        host=_env_required("SUPABASE_DB_HOST"),
        port=int(os.environ.get("SUPABASE_DB_PORT", "5432")),
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"),  # always 'postgres' on Supabase
        user=_env_required("SUPABASE_DB_USER"),
        password=_env_required("SUPABASE_DB_PASSWORD"),
        connect_timeout=15,
        # Use read-committed; we never need repeatable-read for a read-only snapshot
        options="-c default_transaction_isolation=read\\ committed",
    )
    # Belt-and-suspenders: set session to read-only so any accidental DML fails fast
    with conn.cursor() as cur:
        cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    return conn


def _row_to_dict(cur, row) -> dict:
    """Convert a DB row tuple to a dict using cursor.description column names."""
    cols = [d.name for d in cur.description]
    d = dict(zip(cols, row))
    # Rename _jsonb columns to clean aliases; parse if returned as str
    result = {}
    for k, v in d.items():
        key = _COL_MAP.get(k, k)
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                pass
        result[key] = v
    # confidence_avg: psycopg2 may return Decimal — coerce to float
    if "confidence_avg" in result and result["confidence_avg"] is not None:
        result["confidence_avg"] = float(result["confidence_avg"])
    return result


def _remap(d: dict) -> dict:
    """Apply _COL_MAP aliases + coerce confidence_avg to float."""
    out = {_COL_MAP.get(k, k): v for k, v in d.items()}
    if out.get("confidence_avg") is not None:
        out["confidence_avg"] = float(out["confidence_avg"])
    return out


def _select_via_psql() -> list[dict]:
    """Fallback when psycopg2 is unavailable: use the `psql` CLI (the lab's
    standard tooling; postgres pooler role bypasses RLS for read-only use).
    Wraps the canonical query in json_agg so jsonb columns survive intact.
    """
    import subprocess

    host = _env_required("SUPABASE_DB_HOST")
    port = os.environ.get("SUPABASE_DB_PORT", "5432")
    user = _env_required("SUPABASE_DB_USER")
    dbname = os.environ.get("SUPABASE_DB_NAME", "postgres")
    wrapped = f"SELECT coalesce(json_agg(t), '[]'::json) FROM ( {_QUERY.rstrip().rstrip(';')} ) t;"
    env = dict(os.environ, PGPASSWORD=_env_required("SUPABASE_DB_PASSWORD"))
    proc = subprocess.run(
        ["psql", "-h", host, "-p", str(port), "-U", user, "-d", dbname,
         "-tAc", wrapped],
        capture_output=True, text=True, env=env, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql fallback failed: {proc.stderr.strip()}")
    return [_remap(r) for r in json.loads(proc.stdout.strip() or "[]")]


def select_projects() -> list[dict]:
    """Run the canonical query and return rows as dicts.

    Primary path: psycopg2. Fallback: `psql` CLI (psycopg2 not installed in
    every lab env; psql + ~/.claude/csnl-archive/.env is the working pattern).
    """
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        return _select_via_psql()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_QUERY)
            rows = cur.fetchall()
            return [_row_to_dict(cur, r) for r in rows]
    finally:
        conn.close()


def main():
    run_id = resolve_run_id()
    print(f"[00_select_projects] run_id={run_id}")

    projects = select_projects()
    print(f"[00_select_projects] fetched {len(projects)} active project(s)")

    payload = {
        "run_id": run_id,
        "generated_at": kst_now_str(),
        "projects": projects,
    }

    out = dump_stage(run_id, "01_active_projects.json", payload)
    print(f"[00_select_projects] wrote {out}")


if __name__ == "__main__":
    main()
