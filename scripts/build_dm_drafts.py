#!/usr/bin/env python3
"""
scripts/build_dm_drafts.py — assemble per-RESEARCHER DM drafts (operator
override run 2026-05-19): full recommendation + reviewed alternate list +
an explicit "pick another & tell why" ask, delivered as a DM to every
researcher. No signature. Deterministic assembly; bespoke 추천근거/활용
prose is configured here (BYL/MSY/SMJ/JYK reuse the validated drafts;
JOP/SYJ/BHL freshly grounded per the operator's instruction).

Operator changes encoded:
  - JOP: focus = time2dist (others demoted); rec swapped to candidate
    10.1111/bjop.70070; prior rec 10.7554/elife.101277 marked read
    (scripts/mark_read.py) and excluded from JOP's alternate list.
  - SYJ & BHL: split into two people, SHARED 7-candidate pool, DIFFERENT
    recommended paper each (SYJ←Park HB 2025, BHL←Fischer eLife 2025),
    both grounded in the shared bhl_paradigm_pilot.
  - Delivery = DM (full content) to all 7 researchers.

→ state/runs/<RID>/08_dm_drafts.json
"""
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
RID = sys.argv[1] if len(sys.argv) > 1 else "20260519-1539"
rd = _ROOT / "state" / "runs" / RID

# member_init → (display, dm_channel, scout-unit-file, recommended doi)
DM = {
    "JOP": ("박준오", "D0AMRACTLBH", "JOP", "10.1111/bjop.70070"),
    "BYL": ("이보연", "D0AN6PMLWCS", "BYL", "10.3758/s13423-025-02714-5"),
    "MSY": ("여민수", "D0AP128V9DE", "MSY", "10.1113/jp288070"),
    "SMJ": ("정새미", "D0AN0CHTJP5", "SMJ", "10.1167/jov.26.4.1"),
    "JYK": ("김정예", "D0AN3B8K0CD", "JYK", "10.1038/s41586-025-09528-4"),
    "SYJ": ("조수영", "D0AN4N0278E", "SYJ_BHL", "10.3758/s13423-025-02714-5"),
    "BHL": ("이보현", "D0AN6PXAESE", "SYJ_BHL", "10.7554/elife.99478"),
}
UNIT = {"JOP": "JOP", "BYL": "BYL", "MSY": "MSY", "SMJ": "SMJ", "JYK": "JYK",
        "SYJ": "SYJ+BHL", "BHL": "SYJ+BHL"}
# JOP's prior rec is now READ → never list it as an alternate
EXCLUDE_EXTRA = {"JOP": {"10.7554/elife.101277"}}

# Bespoke prose. BYL/MSY/SMJ/JYK = the committed, reviewer-passed drafts.
PROSE = {
 "JOP": {
  "gr": "time2dist 의 duration(0.6–1.6초) 재현 추정(reproduction_estimate_s)과 "
        "absolute→relative posterior 전이 질문에 mapping 되는 것으로 보입니다. 본 "
        "논문 초록은 시간 판단에서 repulsive 지각 성분과 attractive 결정 이월을 "
        "분리하고 두 효과가 과제 간에는 사라져 \"response-mode consistency, not "
        "shared memory alone, drives sequential biases\" 라고 보고합니다. 재현(운동) "
        "응답 일관성이 전이를 좌우할 가능성에 대한 추론적 대응입니다.",
  "us": "재현 추정 분석에서 응답 모드 일관성을 absolute→relative 전이 해석의 "
        "통제 축으로 검토하실 수 있습니다."},
 "BYL": {
  "gr": "biasvar 의 ~0.05s 대 0.5s repulsive→attractive 역전 봉착 가설과 "
        "stimulus_duration 변수(0.034·0.167·0.5초)에 mapping 되는 결과로 "
        "보입니다. 본 논문은 \"a shift from repulsive biases in immediate "
        "perceptual reports to moderate and stronger attraction\" 를 분리해, "
        "즉시 지각에서 WM 응고·인출로 이어지는 같은 방향의 단계별 역전을 "
        "제시합니다.",
  "us": "본 논문의 즉시 지각 → WM 응고 → 인출 분리 절차를 stimulus_duration "
        "조건별 역전 분석의 비교 기준으로 검토하실 수 있습니다."},
 "MSY": {
  "gr": "cat_mag_main 의 magnitude task arm(독립변수 stim)·stim_type 자질 "
        "조작 축이 본 논문 설계와 mapping 됩니다. 본문은 \"previous stimulus "
        "magnitude produced an attractive effect\" 가 자질 불일치 시 강해진다고 "
        "보고하며 PFC 자극 표상에 위치시킵니다. background.prior_studies 의 "
        "Bernardi & Salzman 2020 계열로 이어지고, 가설 대응은 추론된 것입니다.",
  "us": "cat_mag_main 의 scaled_gauss(mu, sigma) history-effect 적합에서 자질 "
        "일치/불일치별 끌림 크기를 비교하는 분석 축으로 검토하실 수 있습니다."},
 "SMJ": {
  "gr": "saccade target 선택이 local concentricity index(LCI) 같은 structural "
        "evidence 와 spatial prior 의 Bayesian 결합이라는 concentricity 의 "
        "hypothesis 에 본 논문이 mapping 됩니다. 본문은 object 내 착지 위치가 "
        "분절 단서와 \"a default bias toward the COA\" 의 결합으로 결정된다고 "
        "보고하여, 동심성을 prior 로 보는 연구 질문과 비교 가능합니다. 대응은 "
        "추론입니다.",
  "us": "본 논문의 COA 정규화 착지 위치 분석 절차를 concentricity 의 LCI 기반 "
        "saccade 착지 모델 검증을 위한 비교 기준으로 검토하실 수 있습니다."},
 "JYK": {
  "gr": "dynamic_bias 의 task-optimized RNN(Gu et al. 2025 Neuron 직접 fork)"
        "에서, 본 논문의 흐름장 분해가 경합 attractor 가설을 구분합니다. 본문은 "
        "학습 RNN line-attractor 가설을 \"evidence inputs that are not aligned "
        "with the line attractor\" 로 평가하며, dependent_var 인 anchor "
        "attractor 위치·깊이와 mapping 됩니다. 추론된 대응입니다.",
  "us": "본 논문의 자율·입력 동역학 흐름장 분해 절차를 dynamic_bias 의 anchor "
        "attractor 깊이 modulate 분석을 검증하는 대조 기준으로 검토하실 수 있습니다."},
 "SYJ": {
  "gr": "bhl_paradigm_pilot 의 DoG 적합(scipy.optimize.curve_fit, x = "
        "reference(2번째)−target(1번째) 방위, y = centered estimation error)에 "
        "본 논문 분석이 mapping 됩니다. 본문은 상대 자극차에 대한 평균오차를 "
        "DoG 로 적합하며 \"the amplitude α determines the direction and "
        "magnitude of serial bias\" 라고 기술해, reference→target 거리 함수의 "
        "부호·크기 추정 틀과 비교 가능합니다. 대응은 추론입니다.",
  "us": "본 논문의 지각 대 작업기억 처리 단계 분리 절차를 D2E reference 처리의 "
        "추정 편향 발생 단계 판별 기준으로 검토하실 수 있습니다."},
 "BHL": {
  "gr": "bhl_paradigm_pilot 의 DoG 적합(scipy.optimize.curve_fit, x = "
        "reference−target 방위, y = centered estimation error)과 본 논문 방법이 "
        "mapping 됩니다. 본문은 상대 방위 편향 함수에 명시적 DoG 를 적합해 "
        "\"an amplitude parameter of 3.51°\" 등 진폭·너비 모수를 보고하며, 순차 "
        "두 자극·retro-cue·CW/CCW 표적상대 코딩과 구조적으로 비교 가능합니다. "
        "신경 부분 제외, 행동 DoG 절차가 전이 요소이며 대응은 추론입니다.",
  "us": "본 논문의 DoG 진폭·너비 모수화 절차를 D2E 추정오차의 reference−target "
        "편향 적합 비교 기준으로 검토하실 수 있습니다."},
}


def kchars(s: str) -> int:
    return len(re.sub(r"\s", "", s))


def cand_index(unit_file: str):
    return json.loads((rd / f"scout_{unit_file}.json").read_text())["candidates"]


def main() -> int:
    out = {"run_id": RID, "mode": "dm", "drafts": []}
    print(f"{'who':5s} {'rec doi':34s} {'근거자':5s} alts banned")
    for init, (name, dm_ch, ufile, rec_doi) in DM.items():
        cands = cand_index(ufile)
        rec = next(c for c in cands if c["doi"] == rec_doi)
        excl = {rec_doi} | EXCLUDE_EXTRA.get(init, set())
        alts = [c for c in sorted(cands, key=lambda x: -x.get("composite", 0))
                if c["doi"] not in excl and c.get("composite", 0) >= 5][:6]
        gr, us = PROSE[init]["gr"], PROSE[init]["us"]
        au = ", ".join(rec.get("authors") or []) or "—"
        ym = (rec.get("date") or "")[:7]
        lines = [
            f"{name} 연구원께,", "",
            f"논문: {rec['title']}",
            f"저자: {au} — {rec['venue']}, {ym}",
            f"DOI: https://doi.org/{rec['doi']}", "",
            f"추천 근거: {gr}", "",
            f"활용: {us}", "",
            "다른 후보 논문 (정독·평가 완료, 위 추천작 외):"]
        for i, c in enumerate(alts, 1):
            lines.append(f"{i}. {c['title']} — {c['venue']}, "
                         f"{(c.get('date') or '')[:7]} · https://doi.org/{c['doi']}")
        lines += ["",
                  "위 목록 중 추천작 외에 읽어보고 싶은 논문이 있으시면 본 "
                  "메시지에 회신으로 알려주시고, 선택하신 이유도 함께 적어 "
                  "주십시오.", "",
                  "해당 추천이 부적합하면 본 메시지로 회신해 주십시오."]
        dm_text = "\n".join(lines)
        out["drafts"].append({
            "unit_id": UNIT[init], "member_init": init, "display_name": name,
            "dm_channel": dm_ch, "paper_doi": rec["doi"],
            "paper_title": rec["title"], "paper_authors": au,
            "paper_venue": rec["venue"], "paper_date": rec["date"],
            "tier": rec.get("tier", "strict"),
            "alternates": [c["doi"] for c in alts], "dm_text": dm_text})
        # self banned-scan (mirror rules/01 fenced block)
        tone = (_ROOT / "rules" / "01_tone.md").read_text()
        blk = re.search(r"```BANNED_TERMS\s*\n(.*?)\n```", tone, re.S).group(1)
        banned = [t.strip().lower() for t in blk.splitlines() if t.strip()]
        hits = [t for t in banned if t in dm_text.lower()]
        print(f"{init:5s} {rec_doi:34s} {kchars(gr):<5d} {len(alts):<4d} {hits}")
    (rd / "08_dm_drafts.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[build_dm_drafts] state/runs/{RID}/08_dm_drafts.json "
          f"({len(out['drafts'])} recipients)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
