---
name: philosophy-rule
description: Grounded over abstraction. Past-focus over speculation. Scientific skepticism — topic fit is inferred, not confirmed. Resilience without surfacing system state. Sequential not batch.
source: project_subagent_orchestrator_philosophy.md + plugin/rules/06_philosophy.md + plugin/rules/07_scientific_skepticism.md + feedback_past_focus_and_resilience.md + feedback_interview_groundedness.md
---

## 1. Grounded over abstraction

Every recommendation rationale must be traceable to a specific field in the unit's
`csnl_research.projects` row. Generic domain claims ("this paper is about visual
perception, matching the researcher's interests") are insufficient and misleading — they
cannot be verified and they do not distinguish one unit's project from another's.

The same principle governs keyword extraction: keywords come from named variables,
named paradigms, and cited DOIs, not from the system's interpretation of what the
research is "about."

Source: `feedback_interview_groundedness.md` — "researcher 입장에서 '이 질문이 내
*어떤 파일* 의 *어떤 변수/날짜/조건* 을 묻는지' 가 한 줄로 파악되어야 답하기 쉽다."
Translated to the recommendation context: the researcher should be able to verify the
`추천 근거:` against something they recognize.

## 2. Past-focus over speculation

Keywords and anchor DOIs used for discovery are drawn from **existing project fields** —
research questions already stated, variables already coded, papers already cited in
the background. The pipeline does not speculate about where the researcher's interests
might be heading.

This means: if a unit's `manipulation_variables` contains `anchor_alpha = {20, 90}`, that
specific parameter is a valid keyword seed. "The researcher might want to explore
decision-making next" is not.

Source: `feedback_past_focus_and_resilience.md` — "Q는 *과거/현재 NAS 파일/코드/파라미터*
중심 (미래 계획 질문 자제)."

## 3. Scientific skepticism: inferred-fit is not confirmed-fit

The system infers that a paper fits a unit based on keyword and DOI overlap with
structured project data. This inference:

- Has not been validated by the researcher
- May be wrong (keywords are imperfect proxies for active interest)
- Is provisional until the researcher responds with feedback

Therefore:
- Language must be measured: `mapping 됩니다`, `비교 가능합니다`, not `완벽하게 부합`
- `deprecated_stub` projects (phase=deprecated_stub or confidence_avg=0) are excluded
  entirely; low-confidence data is not treated as confirmed project direction

Source: `docs/DECISIONS-2026-05-18.md` — "topic facts are *inferred-fit, not
confirmed-fit* until researcher feedback (skepticism rule)."
Source: plugin `rules/07_scientific_skepticism.md` — "researcher confidence ≠ epistemic
confidence."

The feedback loop (`rules/04_dedup_feedback.md`) is the mechanism by which inferred-fit
eventually becomes confirmed or rejected. Until a researcher explicitly rejects a topic,
it remains eligible for recommendation. Once rejected, it is permanently excluded for
that unit.

## 4. Resilience without surfacing system state

The recommendation system will encounter transient failures: Slack rate limits, Supabase
connection errors, API timeouts. These must not produce visible artifacts in researcher-
facing messages.

Principles:
- If delivery fails for a unit, that unit is skipped. No partial message is sent. The
  run summary records the failure for operator review.
- If discovery returns zero candidates for a unit (after both strict and relaxed tiers),
  the unit receives no recommendation that run. No message is sent explaining why.
- Carry-over (ranks 2–5) from the prior run serves as a buffer against empty discovery.
- Internal terms (`tier`, `unit_id`, `confidence_avg`, `exclusion_rules`) never appear
  in researcher-facing text.

Source: `feedback_past_focus_and_resilience.md` §2 — "NAS unmount + Slack 429 등
일시 정지에도 cron 자동 복구." Adapted for delivery context: silent resilience.

## 5. Sequential, not batch

One unit at a time. The ≥ 7 second gap between units is not a politeness convention — it
is a correctness invariant. Each unit's delivery must complete (Slack `ok`, permalink
retrieved, ledger written) before the next unit begins. Batch delivery is forbidden.

Source: `docs/DECISIONS-2026-05-18.md` — "Sequential pacing — never batch."
Source: `feedback_slack_pacing.md` — "한 번에 1 명만 DM 발사. 발사 직후 ledger row +
slack permalink 검증."

## 6. Operator as architect, in-session agent as executor

This system is in manual validation mode. No autonomous LLM inference runs outside an
operator-driven session. The in-session Opus agent reads structured pipeline outputs and
produces drafts; it does not trigger external sends. Only `deliver.py` sends, and only
after the operator has explicitly approved.

Source: `docs/DECISIONS-2026-05-18.md` decision #4 — "Rank/draft performed by the
in-session Opus agent team (operator-driven, not cron)."
Source: `project_subagent_orchestrator_philosophy.md` — "Orchestrator (현 Claude
session, Opus 1M context) 는 7 subagent 의 *위 책무* 가 *올바르게 evolve* 하도록 지도."

Adapted here: the operator is the architect who decides when to run, approves the dry-run
preview, and creates the approval token. The in-session agent scores and drafts but
does not execute delivery.

## 7. No recommendation is better than a bad recommendation

If no candidate passes the date filter + dedup + a score of ≥ 7 in any dimension, the
unit receives no recommendation for that run. An empty run is preferable to a stale,
duplicate, or poorly grounded recommendation.

This applies to all units independently. A run may successfully deliver to 4 units and
skip 3 without error.

--- end of 06_philosophy.md
