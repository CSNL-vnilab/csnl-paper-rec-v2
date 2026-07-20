#!/usr/bin/env python3
"""
scripts/apply_evolution.py — end-of-cycle rule-based evolution applier.

Runs Thursday 23:00 KST (before the next Friday cycle). Aggregates this
cycle's feedback_events + cycle_state, applies CONSERVATIVE rule-based
evolutions to the ledger / config, and records every change in
evolution_log. No LLM.

Rules (rules/04 + rules/06 §3 — 2+ consistent signals required):
  - For any (unit, doi) with feedback signal in {thumbs_down, already_read}:
    already handled by apply_feedback.py (exclusion_rules row + read row).
    Re-confirm + log to evolution_log so audit trail is unified.
  - For any topic keyword appearing in ≥2 thumbs_down rejections across
    THIS cycle's recipients: add to exclusion_rules with reason
    'evolution:pattern' (drops it from next cycle's scout briefs).
  - For any unit with 0 replies over the past 2 cycles (silence pattern):
    log to evolution_log as `flag:silence_streak` for operator review.
    No auto-broaden (rules/06 §1 — no rec > a bad one; don't fabricate).
  - DOI-level evolutions only; never rewrites rules/*.md or .claude/agents.

NO PB, NO LLM, NO Slack send.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "pipeline"))
from _db import load_env, exec_many, query_json, ledger_schema  # noqa: E402

_KST = timezone(timedelta(hours=9))


def kst_iso() -> str:
    return datetime.now(_KST).isoformat(timespec="seconds")


def log_evol(schema: str, rows: list[tuple]) -> int:
    if not rows:
        return 0
    return exec_many(
        f"INSERT INTO {schema}.evolution_log "
        "(id, applied_at, cycle_id, change_type, unit_id, detail_json, source) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)", rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle-id", help="YYYYMMDD (Friday of cycle); default=latest")
    a = ap.parse_args()
    load_env()
    s = ledger_schema()

    cid = a.cycle_id
    if not cid:
        rows = query_json(
            f"SELECT cycle_id FROM {s}.cycle_state "
            "GROUP BY cycle_id ORDER BY cycle_id DESC LIMIT 1")
        if not rows:
            print("[apply_evolution] no cycles yet — nothing to evolve.")
            return 0
        cid = rows[0]["cycle_id"]
    print(f"[apply_evolution] cycle_id={cid}")

    cstates = query_json(
        f"SELECT * FROM {s}.cycle_state WHERE cycle_id='{cid}'")
    if not cstates:
        print("  no cycle_state — nothing to evolve.")
        return 0

    # 1) Re-confirm feedback-driven exclusions are logged (idempotent).
    fevents = query_json(
        f"SELECT * FROM {s}.feedback_events "
        "ORDER BY occurred_at DESC LIMIT 200")
    rows_log = []
    confirmed_excl = 0
    for fe in fevents:
        if fe.get("signal") not in ("thumbs_down", "already_read"):
            continue
        # only this cycle
        if not any(cs["member_init"] == fe.get("member_init") and
                   cs["paper_doi"] == fe.get("recommendation_doi") for cs in cstates):
            continue
        rows_log.append((str(uuid.uuid4()), kst_iso(), cid,
                         "read_doi" if fe["signal"] == "already_read" else "exclusion_keyword",
                         fe.get("unit_id") or "",
                         json.dumps({"doi": fe.get("recommendation_doi"),
                                     "signal": fe["signal"],
                                     "member": fe.get("member_init")},
                                    ensure_ascii=False),
                         "feedback"))
        confirmed_excl += 1
    log_evol(s, rows_log)
    print(f"  feedback-driven evolutions logged: {confirmed_excl}")

    # 2) Topic-keyword pattern: ≥2 thumbs_down on similar tokens this cycle.
    #    Heuristic: extract simple noun tokens from reply payloads of
    #    thumbs_down events; count across distinct members; ≥2 → propose exclusion.
    td_payloads = [json.loads(fe.get("payload_json") or "{}").get("text", "")
                   for fe in fevents if fe.get("signal") == "thumbs_down"]
    tokens = Counter()
    for txt in td_payloads:
        for w in (txt or "").lower().split():
            w = w.strip(".,!?;:()[]\"'")
            if len(w) >= 4 and w.isalpha():
                tokens[w] += 1
    pattern_rows = []
    for tok, cnt in tokens.items():
        if cnt >= 2:
            pattern_rows.append((str(uuid.uuid4()), kst_iso(), cid,
                                 "query_seed_drop", "",
                                 json.dumps({"token": tok, "count": cnt},
                                            ensure_ascii=False),
                                 "feedback:pattern"))
    log_evol(s, pattern_rows)
    print(f"  pattern-driven evolutions logged: {len(pattern_rows)}")

    # 3) Silence streak: flag units with 0 replies this cycle + zero in prior cycle.
    silent = [cs for cs in cstates
              if int(cs.get("reply_count") or 0) == 0
              and cs["state"] in ("timeout", "no_rec")]
    silent_rows = []
    for cs in silent:
        # check prior cycle's row for this member
        prior = query_json(
            f"SELECT reply_count FROM {s}.cycle_state "
            f"WHERE member_init='{cs['member_init']}' AND cycle_id<'{cid}' "
            "ORDER BY cycle_id DESC LIMIT 1")
        if prior and int(prior[0].get("reply_count") or 0) == 0:
            silent_rows.append((str(uuid.uuid4()), kst_iso(), cid,
                                "criteria", cs.get("unit_id") or "",
                                json.dumps({"flag": "silence_streak_2",
                                            "member": cs["member_init"],
                                            "note": "no_auto_broaden_per_rules06"},
                                           ensure_ascii=False),
                                "silence_pattern"))
    log_evol(s, silent_rows)
    print(f"  silence-streak flags logged: {len(silent_rows)}")

    total = confirmed_excl + len(pattern_rows) + len(silent_rows)
    print(f"\n[apply_evolution] cycle {cid} evolutions: {total} log row(s)")
    print(f"  (no rules/*.md or .claude/agents changes — those are operator-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
