#!/usr/bin/env python3
"""
plugin/scripts/session_close.py — mark an interview session completed.

Usage:
    python plugin/scripts/session_close.py --session <sid>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _pdb import load_env, exec_sql, schema  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    args = ap.parse_args()
    load_env()
    sch = schema()
    exec_sql(
        f"UPDATE {sch}.archive_interview_sessions "
        f"   SET completed_at = now()::text, last_active_at = now()::text "
        f" WHERE session_id = %s",
        (args.session,),
    )
    print(json.dumps({"ok": True, "session_id": args.session}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
