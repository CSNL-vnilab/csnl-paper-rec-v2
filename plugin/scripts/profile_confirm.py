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
    ap.add_argument("--dim-preferences-json", default=None,
                    help="(P14) Researcher-confirmed dim_preferences. "
                         "Shape: {focus:{code:weight,...}, method:..., "
                         "stim:..., subj:..., combo_bonus:[[a,b],...], "
                         "source:..., version:1}")
    ap.add_argument("--chunk-mix-json", default=None,
                    help='(P14) {"recent":N,"mid":N,"classic":N} '
                         "override per-chunk top-N for this researcher.")
    args = ap.parse_args()

    args.init = args.init.strip().upper()
    snap = json.loads(_read_arg(args.snapshot_json))
    corr = json.loads(_read_arg(args.corrections_json))
    dim_prefs = (json.loads(_read_arg(args.dim_preferences_json))
                 if args.dim_preferences_json else None)
    chunk_mix = (json.loads(_read_arg(args.chunk_mix_json))
                 if args.chunk_mix_json else None)
    load_env()
    sch = schema()

    sql = f"""
        INSERT INTO {sch}.archive_profile_verifications
          (session_id, researcher_id, profile_snapshot, corrections,
           dim_preferences, chunk_mix, confirmed_at)
        VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, now()::text)
        ON CONFLICT (session_id) DO UPDATE SET
          profile_snapshot = EXCLUDED.profile_snapshot,
          corrections      = EXCLUDED.corrections,
          dim_preferences  = COALESCE(EXCLUDED.dim_preferences,
                                      archive_profile_verifications.dim_preferences),
          chunk_mix        = COALESCE(EXCLUDED.chunk_mix,
                                      archive_profile_verifications.chunk_mix),
          confirmed_at     = EXCLUDED.confirmed_at;
    """
    exec_sql(sql, (
        args.session, args.init,
        json.dumps(snap,  ensure_ascii=False),
        json.dumps(corr,  ensure_ascii=False),
        json.dumps(dim_prefs, ensure_ascii=False) if dim_prefs is not None else None,
        json.dumps(chunk_mix, ensure_ascii=False) if chunk_mix is not None else None,
    ))
    print(json.dumps({
        "ok": True,
        "session_id": args.session,
        "dim_preferences_set": dim_prefs is not None,
        "chunk_mix_set": chunk_mix is not None,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
