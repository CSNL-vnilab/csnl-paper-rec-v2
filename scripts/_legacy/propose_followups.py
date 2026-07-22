#!/usr/bin/env python3
"""
scripts/propose_followups.py — draft NEUTRAL follow-up DMs for non-repliers.

Reads state/runs/<RID>/_feedback_proposals.json (or _replies.json) +
08_dm_drafts.json; writes state/runs/<RID>/09_followups.json in the same
schema as 08, so:
    ! python scripts/deliver.py --run-id <RID> --mode dm \\
        --drafts state/runs/<RID>/09_followups.json --send --operator-approved

NO Paper Blitz / NO scheduling / NO signature (rules/00 + rules/01). Self
banned-term scan runs and refuses to emit on a hit.
"""
import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
DISPLAY = {"JOP": "박준오", "BYL": "이보연", "MSY": "여민수", "SMJ": "정새미",
           "JYK": "김정예", "SYJ": "조수영", "BHL": "이보현"}


def banned_scan(text: str) -> list[str]:
    tone = (_ROOT / "rules" / "01_tone.md").read_text()
    m = re.search(r"```BANNED_TERMS\s*\n(.*?)\n```", tone, re.S)
    if not m:
        return []
    low = text.lower()
    return [t.strip() for t in m.group(1).splitlines()
            if t.strip() and t.strip().lower() in low]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args()
    rd = _ROOT / "state" / "runs" / a.run_id

    drafts = json.loads((rd / "08_dm_drafts.json").read_text()).get("drafts", [])
    by_member = {d["member_init"]: d for d in drafts}

    fp = rd / "_feedback_proposals.json"
    if fp.exists():
        prop = json.loads(fp.read_text())
        non = list(prop.get("non_repliers", []))
    else:
        rp = rd / "_replies.json"
        replied = set()
        if rp.exists():
            replied = {r["member_init"] for r in json.loads(rp.read_text()).get("replies", [])}
        non = [m for m in by_member if m not in replied]

    followups = []
    for m in non:
        d = by_member[m]
        name = d.get("display_name") or DISPLAY.get(m, m)
        text = (
            f"{name} 연구원께,\n\n"
            f"지난 추천 논문(\"{d['paper_title']}\", "
            f"https://doi.org/{d['paper_doi']})을 확인하셨는지요. 추천작에 대한 "
            f"피드백, 또는 함께 보내드린 후보 목록 중 다른 논문을 읽어보고 "
            f"싶으시면 본 메시지에 회신해 주십시오. 응답이 없으셔도 무방합니다."
        )
        hits = banned_scan(text)
        if hits:
            print(f"  ERROR: {m} follow-up has banned terms {hits} — skipping")
            continue
        followups.append({
            "unit_id": d["unit_id"], "member_init": m, "display_name": name,
            "dm_channel": d["dm_channel"],
            "paper_doi": d["paper_doi"], "paper_title": d["paper_title"],
            "paper_authors": d.get("paper_authors", ""),
            "paper_venue": d.get("paper_venue", ""),
            "paper_date": d["paper_date"], "tier": d.get("tier", "strict"),
            "alternates": d.get("alternates", []),
            "dm_text": text,
        })

    out = {"run_id": a.run_id, "mode": "dm", "kind": "followup",
           "drafts": followups}
    dest = rd / "09_followups.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[propose_followups] {dest}  ({len(followups)} non-replier follow-ups)")
    for f in followups:
        nch = len(re.sub(r"\s", "", f["dm_text"]))
        print(f"  {f['member_init']:5s} → DM {f['dm_channel']}  chars≈{nch}  banned=[]")
    if not followups:
        print("  (no non-repliers — nothing to follow up)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
