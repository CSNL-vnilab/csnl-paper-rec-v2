#!/usr/bin/env python3
"""
scripts/build_packet.py — assemble the committed review packet + the
deliver.py input, deterministically, from scout + draft artifacts.

Inputs  (state/runs/<RID>/):
  _scout_briefs.json · scout_<unit>.json · draft_<unit>.json
Outputs:
  state/runs/<RID>/07_drafts.json          (deliver.py input; gitignored)
  drafts/<RID>/README.md                   (committed packet — summary)
  drafts/<RID>/<unit>.md                   (chosen draft + ≥3 candidates)
  drafts/<RID>/candidates.md               (flat candidate index)

Mirrors the predecessor's validated packet shape (drafts/20260519-1408/).
No DB, no network, no LLM — pure assembly + contract validation.
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_KST = timezone(timedelta(hours=9))
_REQ = ("unit_id", "channel_ids", "dm_inits", "channel_text",
        "dm_ping_text", "paper_doi", "paper_title", "paper_date", "tier")


def _fn(uid: str) -> str:
    return uid.replace("+", "_")


def main() -> int:
    rid = sys.argv[1] if len(sys.argv) > 1 else "20260519-1539"
    rd = _ROOT / "state" / "runs" / rid
    units = json.loads((rd / "_scout_briefs.json").read_text())["units"]
    pkt = _ROOT / "drafts" / rid
    pkt.mkdir(parents=True, exist_ok=True)

    drafts, rows, problems = [], [], []
    for u in units:
        uid = u["unit_id"]
        sc = json.loads((rd / f"scout_{_fn(uid)}.json").read_text())
        dpath = rd / f"draft_{_fn(uid)}.json"
        if not dpath.exists():
            problems.append(f"{uid}: draft missing")
            continue
        d = json.loads(dpath.read_text())
        for k in _REQ:
            if not d.get(k):
                problems.append(f"{uid}: draft missing field {k}")
        if "{permalink}" not in d.get("dm_ping_text", ""):
            problems.append(f"{uid}: dm_ping_text missing {{permalink}}")
        top = sc.get("top") or {}
        if d.get("paper_doi") and top.get("doi") and \
           d["paper_doi"].lower().lstrip("https://doi.org/") not in top["doi"].lower() \
           and top["doi"].lower() not in d["paper_doi"].lower():
            problems.append(f"{uid}: draft paper_doi != scout top.doi")
        rev = d.get("_review", {})
        clean = {k: d[k] for k in _REQ}
        drafts.append(clean)
        rows.append((uid, top, sc.get("candidates", []), d, rev))

    # 07_drafts.json (deliver.py input)
    (rd / "07_drafts.json").write_text(json.dumps(
        {"run_id": rid, "generated_at": datetime.now(_KST).isoformat(timespec="seconds"),
         "drafts": drafts}, ensure_ascii=False, indent=2), encoding="utf-8")

    # README.md (summary)
    L = [f"# Paper-rec run {rid} — review packet", "",
         "- Method: **harness v2 — Opus scout fan-out (Playwright full-text crawl)"
         " + producer–reviewer**, Postgres data plane, no API key/OpenRouter/Ollama.",
         "  각 unit: 도메인 질의 자체 수립 → 학술 검색(OpenAlex/EuropePMC/arXiv/S2)"
         " → 전문(full text) 정독 → D1–D5 → ≥3 후보 review loop → 생성-검증 초안.",
         "- 날짜창: journal ≥2025-05-19 / preprint ≥2026-02-19 (strict). "
         "dedup: Postgres ledger(8 rec/1 read/3 excl) + reading-DB 1069 + exclusions.",
         "- **발송 안 됨.** `scripts/deliver.py --run-id %s` dry-run + 톤린트. "
         "게이트 유지(`state/.APPROVED_%s` + `--send --operator-approved`)." % (rid, rid),
         "", "## 요약", "",
         "| unit | 채택 #1 | composite | best_dim | 후보수 | 추천근거자수 | iters |",
         "|---|---|---|---|---|---|---|"]
    for uid, top, cands, d, rev in rows:
        t = (top.get("title", "")[:52] + "…") if len(top.get("title", "")) > 52 else top.get("title", "")
        cc = rev.get("char_count_reason", "—")
        L.append(f"| {uid} | {t} | {top.get('composite','—')} | "
                 f"{top.get('best_dim','—')} | {len(cands)} | {cc} | "
                 f"{rev.get('iterations','—')} |")
    L += ["", "각 unit 상세(채택 draft + ≥3 후보 + grounding + 인용)는 `<unit>.md`. "
          "전체 후보 색인 `candidates.md`.", "",
          "## 운영자 결정", "- 각 unit에서 #1 발송 / 후보 #2·#3 교체 검토 후 지정.",
          "- 실제 발송: 검토 후 `touch state/.APPROVED_%s` + "
          "`python scripts/deliver.py --run-id %s --send --operator-approved` "
          "(first-external-action 게이트; 순차·unit당 ≥7s)." % (rid, rid),
          "- SYJ+BHL 병합 unit → 두 INIT_claude 채널 + 두 DM (운영자가 단일 채널로 변경 가능)."]
    (pkt / "README.md").write_text("\n".join(L), encoding="utf-8")

    # candidates.md (flat index)
    C = [f"# {rid} — 전체 후보 색인", ""]
    for uid, top, cands, d, rev in rows:
        C.append(f"## {uid}")
        for i, c in enumerate(cands, 1):
            star = " ★채택" if (top and c.get("doi") == top.get("doi")) else ""
            C.append(f"- **#{i}** comp {c.get('composite')} ({c.get('best_dim')}) "
                     f"[{c.get('tier')}/{c.get('fulltext_mode')}]{star} — {c.get('title')}  ")
            C.append(f"  {c.get('venue')} · {c.get('date')} · DOI {c.get('doi')}")
        C.append("")
    (pkt / "candidates.md").write_text("\n".join(C), encoding="utf-8")

    # per-unit md
    for uid, top, cands, d, rev in rows:
        m = [f"# {uid} — 추천 검토", "",
             f"채택 #1: **{top.get('title')}**  ",
             f"composite **{top.get('composite')}** (best_dim {top.get('best_dim')}) · "
             f"{top.get('venue')} · {top.get('date')} · tier={top.get('tier')} · "
             f"fulltext={top.get('fulltext_mode')}  ",
             f"DOI: {top.get('doi')}", "",
             f"grounding: {top.get('grounding')}  ",
             f"verbatim quote: \"{top.get('quote')}\"", "",
             f"review: verdict={rev.get('verdict')} iterations={rev.get('iterations')} "
             f"banned_hits={rev.get('banned_hits')} 추천근거자수={rev.get('char_count_reason')}",
             "", "## 채택 draft (검토용 — 미발송)", "",
             f"channels: {d.get('channel_ids')} · DM: {d.get('dm_inits')}", "",
             "### channel_text", "```", d.get("channel_text", ""), "```", "",
             "### dm_ping_text", "```", d.get("dm_ping_text", ""), "```", "",
             f"## 후보 {len(cands)}건 (operator 교체 선택용)", ""]
        for i, c in enumerate(cands, 1):
            g = (c.get("grounding", "") or "")[:200]
            m.append(f"- **#{i}** comp {c.get('composite')} ({c.get('best_dim')}) "
                     f"[{c.get('tier')}/{c.get('fulltext_mode')}] — {c.get('title')}  ")
            m.append(f"  {c.get('venue')} · {c.get('date')} · DOI {c.get('doi')}  ")
            m.append(f"  grounding: {g}")
        (pkt / f"{_fn(uid)}.md").write_text("\n".join(m), encoding="utf-8")

    print(f"[build_packet] drafts/{rid}/ : README.md, candidates.md, "
          f"{len(rows)} unit md; state/runs/{rid}/07_drafts.json ({len(drafts)} drafts)")
    if problems:
        print("  PROBLEMS:")
        for p in problems:
            print("   -", p)
        return 1
    print("  contract OK — all drafts have deliver.py fields + {permalink}; "
          "paper_doi matches scout top")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
