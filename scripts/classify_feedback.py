#!/usr/bin/env python3
"""
scripts/classify_feedback.py — local heuristic classifier (no side effects).

Reads state/runs/<RID>/_replies.json + 08_dm_drafts.json; emits
_feedback_proposals.json per the paper-rec-evolve skill. PROPOSES ONLY —
apply via scripts/apply_feedback.py (gated, operator-run).

Heuristic is conservative (rules/06 skepticism): only mark thumbs_up /
thumbs_down / already_read / saved / cited on clear cues; otherwise
thread_reply with confidence=low and no exclusion. The Opus feedback-analyst
agent can be invoked separately on the same _replies.json for richer
classification — but this script is safe to run unattended at the 21:00
prep pass.
"""
import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# Korean/English cue lexicon — conservative; ambiguous cases stay "thread_reply"
CUES = {
    "thumbs_up":   ["좋습니다", "좋아요", "감사", "읽어볼게요", "읽겠습니다",
                    "thanks", "great", "perfect", "will read"],
    "thumbs_down": ["관심 없", "관심없", "별로", "부적합", "맞지 않", "아닌",
                    "not relevant", "not interested", "not a fit", "no thanks"],
    "already_read":["이미 읽었", "이미 봤", "본 적 있", "읽은 적 있",
                    "already read", "already seen", "read this"],
    "saved":       ["저장했", "저장하겠", "북마크", "saved"],
    "cited":       ["인용", "cite", "cited"],
}
ALT_PAT = re.compile(r"(?:후보\s*)?#?\s*([1-9])\s*(?:번|호)?", re.U)


def classify_one(text: str) -> tuple[str, str]:
    t = (text or "").lower()
    # priority order: thumbs_down > already_read > saved > cited > thumbs_up > thread_reply
    for sig in ("thumbs_down", "already_read", "saved", "cited", "thumbs_up"):
        if any(c.lower() in t for c in CUES[sig]):
            return sig, "high"
    return "thread_reply", "low"


def pick_alt(text: str, alternates: list[str]) -> str | None:
    m = ALT_PAT.search(text or "")
    if not m:
        return None
    idx = int(m.group(1)) - 1
    return alternates[idx] if 0 <= idx < len(alternates) else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args()
    rd = _ROOT / "state" / "runs" / a.run_id

    replies_path = rd / "_replies.json"
    if not replies_path.exists():
        print(f"  WARN: {replies_path} not found — treating all as non-repliers.")
        replies = []
    else:
        replies = json.loads(replies_path.read_text()).get("replies", [])
    drafts = json.loads((rd / "08_dm_drafts.json").read_text()).get("drafts", [])
    by_member = {d["member_init"]: d for d in drafts}

    proposals = []
    for r in replies:
        m = r["member_init"]
        d = by_member.get(m, {})
        signal, conf = classify_one(r["text"])
        picked = pick_alt(r["text"], d.get("alternates", []))
        excl = None
        if signal in ("thumbs_down", "already_read") and conf == "high":
            excl = {"excluded_term": d.get("paper_doi", ""),
                    "reason": f"feedback:{signal}", "source": "feedback"}
        proposals.append({
            "member_init": m, "unit_id": d.get("unit_id", m),
            "signal": signal, "confidence": conf,
            "reply_text": r["text"][:500], "reply_ts": r["ts"],
            "feedback_event": {
                "recommendation_doi": d.get("paper_doi", ""),
                "payload_json": json.dumps({"reply_ts": r["ts"],
                                            "text": r["text"][:500]},
                                           ensure_ascii=False),
                "idem_key": f"{a.run_id}:{m}:{r['ts']}"},
            "exclusion": excl,
            "picked_alternate_doi": picked,
        })

    replied = {p["member_init"] for p in proposals}
    non_repliers = [d["member_init"] for d in drafts if d["member_init"] not in replied]

    out = {"run_id": a.run_id, "proposals": proposals,
           "non_repliers": non_repliers,
           "summary": {"replies": len(proposals), "non_repliers": len(non_repliers)}}
    dest = rd / "_feedback_proposals.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[classify_feedback] {dest}")
    print(f"  replies classified: {len(proposals)} ; non-repliers: {len(non_repliers)}")
    for p in proposals:
        ex = f" excl→{p['exclusion']['excluded_term']}" if p["exclusion"] else ""
        pa = f" pick→{p['picked_alternate_doi']}" if p["picked_alternate_doi"] else ""
        print(f"  {p['member_init']:5s} {p['signal']:13s} {p['confidence']:6s}{ex}{pa}")
    if non_repliers:
        print(f"  non-repliers: {non_repliers}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
