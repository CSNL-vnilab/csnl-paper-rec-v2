#!/usr/bin/env python3
"""
scripts/weekly/seed_channels.py — seed archive_researcher_channels consent rows.

Design §8: a researcher receives automatic weekly recommendations only if they
have a row here, with an explicit consented_at. The hybrid security gate (§3c)
treats the established interview cohort (the existing 7 researchers) as
pre-approved for autonomy; a brand-new researcher needs an operator grant.

This seeds one notion-channel row per researcher (channel_target = init = the
Notion "Researcher" select value), Monday 09:00 KST default slot. Idempotent —
ON CONFLICT DO NOTHING never disturbs an existing row (so a researcher who later
opts out via enabled=false is not re-enabled by a re-run).

Operator-run; --apply gates the write.

Usage:
    python3 scripts/weekly/seed_channels.py                 # dry-run, all 7
    python3 scripts/weekly/seed_channels.py --apply         # write
    python3 scripts/weekly/seed_channels.py --only JOP --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
from _db import load_env, query_json, exec_many, ledger_schema  # noqa: E402


def _inits(only: str | None) -> list[str]:
    try:
        import yaml
        d = yaml.safe_load((_REPO_ROOT / "config" / "researchers.yaml").read_text("utf-8"))
        inits = sorted((d.get("researchers") or {}).keys())
    except Exception:
        inits = ["BHL", "BYL", "JOP", "JYK", "MSY", "SMJ", "SYJ"]
    if only:
        only = only.strip().upper()
        inits = [i for i in inits if i == only]
    return inits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default=None, help="Seed one researcher only.")
    ap.add_argument("--dow", type=int, default=1, help="Delivery day-of-week (0=Sun,1=Mon).")
    ap.add_argument("--hour", type=int, default=9, help="Delivery hour KST.")
    args = ap.parse_args()
    load_env()
    sch = ledger_schema()
    inits = _inits(args.only)
    if not inits:
        print("[seed] no researchers matched.")
        return 0

    existing = {r["researcher_id"] for r in query_json(
        f"SELECT researcher_id FROM {sch}.archive_researcher_channels "
        f"WHERE channel_type = 'notion'")}
    new = [i for i in inits if i not in existing]
    print(f"[seed] researchers={inits}  already_present={sorted(existing & set(inits))}  "
          f"to_insert={new}  slot=dow{args.dow}/h{args.hour}")

    if not args.apply:
        print("[seed] dry-run only. Re-run with --apply to INSERT consent rows.")
        return 0
    if not new:
        print("[seed] nothing to insert (all present).")
        return 0
    rows = [(i, "notion", i, args.dow, args.hour, True) for i in new]
    n = exec_many(
        f"INSERT INTO {sch}.archive_researcher_channels "
        f"(researcher_id, channel_type, channel_target, delivery_dow, "
        f"delivery_hour_kst, enabled, consented_at) "
        f"VALUES (%s,%s,%s,%s,%s,%s, now()) "
        f"ON CONFLICT (researcher_id, channel_type) DO NOTHING",
        rows)
    print(f"[seed] inserted up to {n} consent row(s) (ON CONFLICT DO NOTHING).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
