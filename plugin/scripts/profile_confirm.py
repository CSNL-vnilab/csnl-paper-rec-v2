#!/usr/bin/env python3
"""
plugin/scripts/profile_confirm.py — record the researcher's Stage 1
verification of their profile (corrections / additions / acks).

Usage:
    python plugin/scripts/profile_confirm.py --session <sid> --init <init> \\
        --snapshot-json @snap.json [--corrections-json @corr.json]

Writes archive_profile_verifications. Idempotent on session_id (UPSERT).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _pdb import load_env, exec_sql, schema  # noqa: E402


def _read_arg(v: str) -> str:
    if v and v.startswith("@"):
        p = Path(v[1:])
        if not p.exists():
            raise FileNotFoundError(p)
        return p.read_text(encoding="utf-8")
    return v or "{}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--init", required=True)
    ap.add_argument("--snapshot-json", required=True,
                    help="Inline JSON or @path/to/snapshot.json")
    ap.add_argument("--corrections-json", default="{}",
                    help="Inline JSON or @path/to/corrections.json")
    args = ap.parse_args()

    snap = json.loads(_read_arg(args.snapshot_json))
    corr = json.loads(_read_arg(args.corrections_json))
    load_env()
    sch = schema()

    sql = f"""
        INSERT INTO {sch}.archive_profile_verifications
          (session_id, researcher_id, profile_snapshot, corrections, confirmed_at)
        VALUES (%s, %s, %s::jsonb, %s::jsonb, now()::text)
        ON CONFLICT (session_id) DO UPDATE SET
          profile_snapshot = EXCLUDED.profile_snapshot,
          corrections      = EXCLUDED.corrections,
          confirmed_at     = EXCLUDED.confirmed_at;
    """
    exec_sql(sql, (
        args.session, args.init,
        json.dumps(snap,  ensure_ascii=False),
        json.dumps(corr,  ensure_ascii=False),
    ))
    print(json.dumps({"ok": True, "session_id": args.session}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
