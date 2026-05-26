#!/usr/bin/env python3
"""
plugin/scripts/doctor.py — environment diagnostic for the
csnl-paper-archive-interview plugin.

Runs a series of checks; each prints OK / WARN / FAIL with a
remediation step. Designed to be runnable in ANY shell on a
researcher's machine; only requires Python 3.8+ stdlib (the
checks themselves don't import psycopg2 — they DETECT it).

Usage:
    python3 -m plugin.scripts.doctor              # from anywhere
    python3 <plugin-root>/scripts/doctor.py       # explicit
    python3 ~/.claude/plugins/cache/csnl-marketplace/csnl-paper-archive-interview/*/scripts/doctor.py

Exit code 0 if everything passes; non-zero on the first FAIL.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

KO = {
    "ok":   "  ✓ OK   ",
    "warn": "  ⚠ WARN ",
    "fail": "  ✗ FAIL ",
}


def _msg(level: str, name: str, detail: str = "", fix: str = "") -> None:
    print(f"{KO[level]}{name:30s} {detail}")
    if fix:
        print(f"           → {fix}")


def check_python_version() -> bool:
    v = sys.version_info
    name = f"Python ≥ 3.8"
    detail = f"(running {v.major}.{v.minor}.{v.micro})"
    if v < (3, 8):
        _msg("fail", name, detail,
             "`brew install python@3.11` or use pyenv; "
             "then re-run with python3.11 doctor.py")
        return False
    _msg("ok", name, detail)
    return True


def check_pip() -> bool:
    name = "pip available"
    try:
        import pip  # noqa: F401
        _msg("ok", name, f"({pip.__version__})")
        return True
    except ImportError:
        _msg("fail", name, "(missing)",
             "`python3 -m ensurepip --user` or reinstall Python from python.org")
        return False


def check_psycopg2(auto_install: bool = False) -> bool:
    name = "psycopg2 (DB driver)"
    spec = importlib.util.find_spec("psycopg2")
    if spec is not None:
        import psycopg2
        _msg("ok", name, f"({psycopg2.__version__.split(' ')[0]})")
        return True
    # Fallback: is psql CLI on PATH?
    psql = shutil.which("psql")
    if psql:
        _msg("warn", name, f"(missing — but psql CLI at {psql})",
             "Plugin will use psql fallback. Optional: "
             "`python3 -m pip install --user psycopg2-binary` for ~10× speed.")
        return True
    if auto_install:
        print("  → psycopg2-binary 없음. 자동 설치 시도 중...")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--user",
                 "--quiet", "psycopg2-binary>=2.9"],
                check=True, capture_output=True, text=True, timeout=120,
            )
            _msg("ok", name, "(auto-installed)")
            return True
        except subprocess.CalledProcessError as e:
            _msg("fail", name, "(auto-install failed)",
                 f"Run manually: `python3 -m pip install --user psycopg2-binary`\n"
                 f"           Or install psql CLI (`brew install libpq && brew link --force libpq`).")
            return False
    _msg("fail", name, "(missing)",
         "`python3 -m pip install --user psycopg2-binary` "
         "or `brew install libpq && brew link --force libpq` for psql CLI")
    return False


def find_env_file() -> Path | None:
    """Reproduces the same search order plugin/scripts/_pdb.py uses."""
    plugin_dir = Path(__file__).resolve().parent.parent
    candidates = [
        plugin_dir / ".env",
        Path.home() / ".csnl-paper-archive" / ".env",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def check_env_file() -> Path | None:
    name = ".env file"
    p = find_env_file()
    plugin_dir = Path(__file__).resolve().parent.parent
    home_env = Path.home() / ".csnl-paper-archive" / ".env"
    if not p:
        _msg("fail", name, "(not found in either path)",
             f"Create one of:\n"
             f"           • {home_env}  (recommended — survives reinstalls)\n"
             f"           • {plugin_dir / '.env'}\n"
             f"           Run `python3 {plugin_dir / 'scripts' / 'setup.py'}` "
             f"for an interactive walkthrough.")
        return None
    _msg("ok", name, f"({p})")
    # Check chmod.
    try:
        mode = p.stat().st_mode & 0o777
        if mode & 0o077:
            _msg("warn", "permissions", f"(mode={oct(mode)})",
                 f"`chmod 600 {p}` to prevent other users on this machine from reading it.")
    except OSError:
        pass
    return p


def check_env_values(env_path: Path) -> bool:
    name = "env values present"
    required = ["SUPABASE_DB_HOST", "SUPABASE_DB_USER",
                "SUPABASE_DB_PASSWORD"]
    optional = ["SUPABASE_DB_PORT", "SUPABASE_DB_NAME", "CPR_LEDGER_SCHEMA"]
    seen = {}
    for raw in env_path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        seen[k.strip()] = v.strip()
    missing = [k for k in required if not seen.get(k)]
    if missing:
        _msg("fail", name, f"(missing: {missing})",
             f"Add these to {env_path}; ask jy061100@gmail.com for the values.")
        return False
    extras = {k: seen[k] for k in optional if seen.get(k)}
    _msg("ok", name, f"(host={seen['SUPABASE_DB_HOST']}, "
         f"user={seen['SUPABASE_DB_USER'][:14]}…)")
    if extras:
        _msg("ok", "env optional defaults",
             ", ".join(f"{k}={v}" for k, v in extras.items()))
    # Export them so the subsequent connection check sees them.
    for k, v in seen.items():
        os.environ.setdefault(k, v)
    return True


def check_db_reachability() -> bool:
    name = "Supabase reachability"
    host = os.environ.get("SUPABASE_DB_HOST")
    port = int(os.environ.get("SUPABASE_DB_PORT", "5432"))
    if not host:
        _msg("fail", name, "(env not loaded — earlier failure?)")
        return False
    try:
        with socket.create_connection((host, port), timeout=10):
            pass
    except OSError as e:
        _msg("fail", name, f"({host}:{port} — {e})",
             "Check VPN / corporate proxy. "
             "On campus Wi-Fi the Supabase pooler is normally open.")
        return False
    _msg("ok", name, f"(TCP {host}:{port} reachable)")
    return True


def check_db_auth() -> bool:
    name = "Supabase auth"
    try:
        import psycopg2
    except ImportError:
        # Try psql fallback.
        psql = shutil.which("psql")
        if not psql:
            _msg("fail", name, "(no driver)",
                 "Install psycopg2-binary or psql first")
            return False
        env = dict(os.environ, PGPASSWORD=os.environ.get("SUPABASE_DB_PASSWORD", ""))
        proc = subprocess.run(
            [psql, "-h", os.environ["SUPABASE_DB_HOST"],
             "-p", os.environ.get("SUPABASE_DB_PORT", "5432"),
             "-U", os.environ["SUPABASE_DB_USER"],
             "-d", os.environ.get("SUPABASE_DB_NAME", "postgres"),
             "-tAc", "SELECT 1"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        if proc.returncode != 0:
            _msg("fail", name, "(psql auth failed)",
                 f"Check SUPABASE_DB_USER / SUPABASE_DB_PASSWORD. "
                 f"Error: {proc.stderr.strip()}")
            return False
        _msg("ok", name, "(via psql)")
        return True
    try:
        conn = psycopg2.connect(
            host=os.environ["SUPABASE_DB_HOST"],
            port=int(os.environ.get("SUPABASE_DB_PORT", "5432")),
            dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"),
            user=os.environ["SUPABASE_DB_USER"],
            password=os.environ["SUPABASE_DB_PASSWORD"],
            connect_timeout=15,
        )
    except psycopg2.OperationalError as e:
        _msg("fail", name, "(psycopg2 auth failed)",
             f"Verify SUPABASE_DB_USER + SUPABASE_DB_PASSWORD with the operator.\n"
             f"           Error: {str(e).strip().splitlines()[0]}")
        return False
    conn.close()
    _msg("ok", name, "(connection round-trip OK)")
    return True


def check_archive_schema() -> bool:
    name = "csnl_paper_rec.archive_* tables"
    try:
        import psycopg2
    except ImportError:
        _msg("warn", name, "(skipped — psycopg2 missing)")
        return True
    schema = os.environ.get("CPR_LEDGER_SCHEMA", "csnl_paper_rec")
    expected = ("archive_papers", "archive_researcher_queues",
                "archive_interview_sessions", "archive_responses")
    conn = psycopg2.connect(
        host=os.environ["SUPABASE_DB_HOST"],
        port=int(os.environ.get("SUPABASE_DB_PORT", "5432")),
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"),
        user=os.environ["SUPABASE_DB_USER"],
        password=os.environ["SUPABASE_DB_PASSWORD"],
        connect_timeout=15,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s", (schema,),
            )
            present = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
    missing = [t for t in expected if t not in present]
    if missing:
        _msg("fail", name, f"(missing: {missing})",
             f"Operator needs to run `! python scripts/init_db.py` "
             f"in the harness repo. Contact jy061100@gmail.com.")
        return False
    _msg("ok", name, f"(all {len(expected)} present)")
    return True


def check_queue_for(init: str | None) -> bool:
    if not init:
        return True
    name = f"queue for {init}"
    try:
        import psycopg2
    except ImportError:
        _msg("warn", name, "(skipped — psycopg2 missing)")
        return True
    conn = psycopg2.connect(
        host=os.environ["SUPABASE_DB_HOST"],
        port=int(os.environ.get("SUPABASE_DB_PORT", "5432")),
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"),
        user=os.environ["SUPABASE_DB_USER"],
        password=os.environ["SUPABASE_DB_PASSWORD"],
        connect_timeout=15,
    )
    schema = os.environ.get("CPR_LEDGER_SCHEMA", "csnl_paper_rec")
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT chunk, COUNT(*) "
                f"FROM {schema}.archive_researcher_queues "
                f"WHERE researcher_id = %s GROUP BY chunk ORDER BY chunk",
                (init,),
            )
            by_chunk = {r[0]: r[1] for r in cur.fetchall()}
    finally:
        conn.close()
    if not by_chunk:
        _msg("warn", name, "(no rows)",
             f"Operator hasn't built {init}'s queue yet. "
             f"Ask them to run `! python scripts/archive/build_researcher_queue.py {init} --apply`.")
        return False
    _msg("ok", name, f"({by_chunk})")
    return True


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", default=None,
                    help="Researcher init (e.g. JOP). When given, also "
                         "verifies a queue exists for that researcher.")
    ap.add_argument("--auto-install", action="store_true",
                    help="If psycopg2 missing, attempt `pip install --user "
                         "psycopg2-binary` automatically.")
    args = ap.parse_args()

    print("=" * 62)
    print("CSNL paper-archive-interview — environment doctor")
    print("=" * 62)

    ok = True
    ok &= check_python_version()
    ok &= check_pip()
    ok &= check_psycopg2(auto_install=args.auto_install)
    env = check_env_file()
    if not env:
        ok = False
    else:
        ok &= check_env_values(env)
        if ok:
            ok &= check_db_reachability()
            ok &= check_db_auth()
            ok &= check_archive_schema()
            ok &= check_queue_for(args.init)

    print("=" * 62)
    if ok:
        print("모든 점검 통과. Claude Code 에서 다음 명령으로 시작하세요:")
        if args.init:
            print(f"  /csnl-paper-archive-interview:paper-interview {args.init}")
        else:
            print("  /csnl-paper-archive-interview:paper-interview <YOUR_INIT>")
        return 0
    print("일부 점검 실패. 위의 → 안내를 따라 해결한 뒤 다시 실행해주세요.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
