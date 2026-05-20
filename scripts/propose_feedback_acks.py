#!/usr/bin/env python3
"""
scripts/propose_feedback_acks.py — per-replier acknowledgement DMs that
state HOW their feedback will be reflected.

Reads state/runs/<RID>/_feedback_proposals.json (from classify_feedback.py)
+ 08_dm_drafts.json. Emits state/runs/<RID>/10_feedback_acks.json in the
08-schema so:
    ! python scripts/deliver.py --run-id <RID> --mode dm \\
        --drafts state/runs/<RID>/10_feedback_acks.json \\
        --send --operator-approved

PROPOSES ONLY (write JSON). NO PB, NO signature, no banned terms, no
internal-ops vocab (ledger/dedup/cycle/run_id/...). Self banned-scan runs
and refuses to emit on a hit; tone-lint at delivery is the final backstop.
"""
import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
DISPLAY = {"JOP": "박준오", "BYL": "이보연", "MSY": "여민수", "SMJ": "정새미",
           "JYK": "김정예", "SYJ": "조수영", "BHL": "이보현"}

# Reflection sentence per signal (Korean 합쇼체; measured; no banned terms;
# no internal-ops vocabulary; states the action that will follow).
ACK = {
    "thumbs_up":
        "추천이 도움 되신 것으로 확인했습니다. 동일한 방향성을 향후 추천에 "
        "유지하도록 반영하겠습니다.",
    "thumbs_down":
        "해당 추천을 적합하지 않은 것으로 기록하고, 향후 동일 논문이 재추천 "
        "되지 않도록 본 분의 제외 목록에 반영하겠습니다.",
    "already_read":
        "이미 읽으신 논문으로 기록하여 향후 재추천 대상에서 제외되도록 "
        "반영하겠습니다.",
    "saved":
        "저장하셨다고 기록해 두었습니다. 후속 추천에서 관련 방향을 "
        "참고하겠습니다.",
    "cited":
        "인용 예정으로 기록하였습니다. 관련 후속 연구 방향을 다음 추천에 "
        "반영하겠습니다.",
    "thinking":
        "회신 확인했습니다. 의견을 별도로 기록해 두었으며 다음 추천에 "
        "참고하겠습니다.",
    "thread_reply":
        "회신 확인했습니다. 의견을 별도로 기록해 두었으며 다음 추천에 "
        "참고하겠습니다.",
}


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

    fp = rd / "_feedback_proposals.json"
    if not fp.exists():
        print(f"ERROR: {fp.relative_to(_ROOT)} not found "
              "(run scripts/classify_feedback.py first).")
        return 1
    props = json.loads(fp.read_text()).get("proposals", [])
    if not props:
        print("[propose_feedback_acks] no replies — nothing to acknowledge.")
        out = {"run_id": a.run_id, "mode": "dm", "kind": "feedback_ack", "drafts": []}
        (rd / "10_feedback_acks.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    drafts08 = json.loads((rd / "08_dm_drafts.json").read_text()).get("drafts", [])
    by_member = {d["member_init"]: d for d in drafts08}

    acks = []
    for p in props:
        m = p["member_init"]
        d = by_member.get(m, {})
        name = DISPLAY.get(m, m)
        sig = p.get("signal", "thread_reply")
        action = ACK.get(sig, ACK["thread_reply"])
        # picked alternate: name the DOI as the next-cycle target
        picked = p.get("picked_alternate_doi")
        extra = ""
        if picked:
            extra += (f" 회신하신 선택({picked})에 따라 해당 논문을 다음 "
                      "추천으로 갱신하여 별도 전달드리겠습니다.")
        excl = p.get("exclusion")
        if excl and excl.get("excluded_term"):
            extra += f" 제외 대상으로 기록될 항목: {excl['excluded_term']}."

        text = (
            f"{name} 연구원께,\n\n"
            f"지난 추천(\"{d.get('paper_title','-')}\")에 대한 회신을 "
            f"확인했습니다. {action}{extra}\n\n"
            "추가 의견이 있으시면 언제든 본 메시지에 회신해 주십시오."
        )
        hits = banned_scan(text)
        if hits:
            print(f"  ERROR: {m} ack hit banned terms {hits} — skipping")
            continue
        acks.append({
            "unit_id": d.get("unit_id", m), "member_init": m, "display_name": name,
            "dm_channel": d.get("dm_channel", ""),
            "paper_doi": d.get("paper_doi", ""), "paper_title": d.get("paper_title", ""),
            "paper_authors": d.get("paper_authors", ""),
            "paper_venue": d.get("paper_venue", ""),
            "paper_date": d.get("paper_date", ""), "tier": d.get("tier", "strict"),
            "alternates": d.get("alternates", []),
            "signal": sig, "exclusion": excl, "picked_alternate_doi": picked,
            "dm_text": text,
        })

    out = {"run_id": a.run_id, "mode": "dm", "kind": "feedback_ack", "drafts": acks}
    dest = rd / "10_feedback_acks.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[propose_feedback_acks] {dest}  ({len(acks)} replier ack(s))")
    for ack in acks:
        nch = len(re.sub(r"\s", "", ack["dm_text"]))
        print(f"  {ack['member_init']:5s} → {ack['dm_channel']}  "
              f"sig={ack['signal']:13s} chars≈{nch}  banned=[]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
