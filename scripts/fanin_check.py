#!/usr/bin/env python3
"""
scripts/fanin_check.py — Supervisor fan-in validation + final dedup
cross-check over the scout outputs (rules/03 anti-hallucination, rules/04
never-re-recommend). Local only, no DB. Run by the orchestrator after the
scout fan-out completes, before the producer-reviewer loop.

Checks per unit scout_<unit>.json:
  - valid JSON, contract fields present
  - candidates ≥ 3, composite-descending
  - composite == max(D1..D5); any dim ≥7 ⇒ non-empty verbatim quote
  - top non-null ⇒ composite ≥ 7 and has grounding + quote
  - FINAL DEDUP: top.doi (normalized) ∉ unit dedup dois/excluded_dois,
    and top.title not ≥0.9 vs unit dedup titles or reading_db
"""
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def ndoi(s):
    s = (s or "").strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    return re.sub(r"^doi:\s*", "", s, flags=re.I).strip().lower()


def feq(a, b):
    a = re.sub(r"\s+", " ", (a or "").lower().strip())
    b = re.sub(r"\s+", " ", (b or "").lower().strip())
    return bool(a) and bool(b) and SequenceMatcher(None, a, b).ratio() >= 0.90


def main() -> int:
    rid = sys.argv[1] if len(sys.argv) > 1 else "20260519-1539"
    rd = _ROOT / "state" / "runs" / rid
    snap = json.loads((rd / "_dedup_snapshot.json").read_text())
    reading_titles = [e["title"] for e in snap.get("reading_db", []) if e.get("title")]
    reading_dois = {ndoi(e["doi"]) for e in snap.get("reading_db", []) if e.get("doi")}
    idx = json.loads((rd / "_scout_briefs.json").read_text())["units"]

    ok = True
    print(f"{'unit':9s} {'cand':4s} {'top_comp':8s} {'tier':7s} verdict")
    for u in idx:
        uid = u["unit_id"]
        f = rd / f"scout_{uid.replace('+','_')}.json"
        problems = []
        if not f.exists():
            print(f"{uid:9s} —    —        —      MISSING scout file")
            ok = False
            continue
        s = json.loads(f.read_text())
        cands = s.get("candidates", [])
        if len(cands) < 3:
            problems.append(f"only {len(cands)} candidates (<3)")
        comps = [c.get("composite") for c in cands]
        if comps != sorted(comps, reverse=True):
            problems.append("not composite-descending")
        for c in cands:
            dims = [c.get(d, 0) for d in ("D1", "D2", "D3", "D4", "D5")]
            if c.get("composite") != max(dims):
                problems.append(f"{c.get('doi')}: composite≠max(D1..D5)")
            if max(dims) >= 7 and not (c.get("quote") or "").strip():
                problems.append(f"{c.get('doi')}: dim≥7 without verbatim quote")
        top = s.get("top")
        tier = (top or {}).get("tier", "—")
        if top:
            if top.get("composite", 0) < 7:
                problems.append("top composite <7 but non-null")
            if not (top.get("grounding") or "").strip():
                problems.append("top missing grounding")
            td = ndoi(top.get("doi"))
            ut = snap["units"].get(uid, {})
            if td in set(ut.get("dois", [])) | set(ut.get("excluded_dois", [])) | reading_dois:
                problems.append(f"DEDUP VIOLATION: top doi {td} in never-recommend set")
            for t in list(ut.get("titles", [])) + reading_titles:
                if feq(top.get("title"), t):
                    problems.append(f"DEDUP VIOLATION: top title ≥0.9 match: {t[:60]}")
                    break
        else:
            if s.get("reason") != "insufficient_grounded_candidates":
                problems.append("top null without insufficient_grounded_candidates reason")
        verdict = "OK" if not problems else "FAIL: " + "; ".join(problems)
        if problems:
            ok = False
        print(f"{uid:9s} {len(cands):<4d} {str((top or {}).get('composite','—')):8s} {tier:7s} {verdict}")
    print("\nFAN-IN", "PASS — all units validated, dedup clean" if ok else "FAIL — see above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
