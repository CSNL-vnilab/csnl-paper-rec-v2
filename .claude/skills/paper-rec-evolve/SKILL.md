---
name: paper-rec-evolve
description: >
  Phase-7 evolution procedure for feedback-analyst. After a paper-rec run,
  classify researcher DM replies into ledger signals, propose status &
  exclusion updates, draft neutral (NO Paper Blitz) follow-ups for
  non-repliers, and propose keyword/logic evolution diffs. Use when
  triggered: "replies catch", "피드백 정리", "follow-up", "재질문",
  "evolution propose", "리뷰 반영해 추천 로직 업데이트".
---

# paper-rec-evolve — feedback → status + follow-up + harness evolution

> **Proposes only.** No Slack send, no DB write. A human runs the gated
> `!` executors (`apply_feedback.py`, `deliver.py --mode dm --drafts
> 09_followups.json`). Manual-only is binding (DECISIONS #4) — this skill
> does not auto-apply harness changes.

Hard scope: **Paper Blitz / CWLL is OUT** (rules/00; SMJ's domain).
Follow-ups never mention PB/CWLL/scheduling. Tone laws still apply
(rules/01); BANNED_TERMS substring lint mirrors the delivery backstop.

## Inputs
`state/runs/<RID>/_replies.json` (Slack pull; may be empty when prep runs
before any replies), `08_dm_drafts.json` (sent recipients/papers/alts),
`_dedup_snapshot.json`, the unit briefs, `rules/00,01,03,04,06`.

## Step 1 — classify each reply

For each reply: map text → one signal ∈ `feedback_events.signal` CHECK set:
- `thumbs_up` — explicit acceptance / "읽어볼게요" / will read this one.
- `thumbs_down` — explicit rejection of the recommended paper or topic.
- `already_read` — they've read it before.
- `thinking` / `thread_reply` — ambiguous / informational.
- `saved` / `cited` — only if explicit ("저장했습니다", "인용 예정").

Extract:
- `picked_alternate_doi` — if they chose a different paper from the listed
  alternates ("후보 3번 읽고 싶어요" → resolve to the 3rd alt's DOI from
  `08_dm_drafts.json[i].alternates`).
- `rejected_topic_keywords` — explicit topic complaints (e.g., "RT 쪽은
  관심 없음" → `RT`); only obvious tokens, no inference.

Skepticism (rules/06 §3): a single reply is weak evidence. Set
`confidence ∈ {low, medium, high}`; low ⇒ no exclusion proposal.

## Step 2 — propose status & exclusion updates (no DB write)

Emit `_feedback_proposals.json`:
```json
{ "run_id":"<RID>",
  "proposals":[ { "member_init":"JOP","unit_id":"JOP",
     "signal":"thumbs_down","confidence":"high",
     "reply_text":"<verbatim>","reply_ts":"…",
     "feedback_event":{"recommendation_doi":"…","payload_json":"…","idem_key":"<RID>:JOP"},
     "exclusion":{"excluded_term":"<doi or keyword>","reason":"feedback","source":"feedback"},
     "picked_alternate_doi": null } ] }
```
Idempotency key: `<RID>:<member_init>:<reply_ts>`.

## Step 3 — draft neutral follow-ups for non-repliers (NO PB)

For every recipient in `08_dm_drafts.json` without a reply in
`_replies.json`, emit one entry in `09_followups.json` (same schema as 08
so `deliver.py --mode dm --drafts state/runs/<RID>/09_followups.json` can
send it). Template (Korean 합쇼체, no signature, no banned terms,
**no Paper Blitz / no schedule / no AI self-reference**):

```
<이름> 연구원께,

지난 추천 논문(<title>, https://doi.org/<doi>)을 확인하셨는지요. 추천작에
대한 피드백, 또는 함께 보내드린 후보 목록 중 다른 논문을 읽어보고 싶으시면
본 메시지에 회신해 주십시오. 응답이 없으셔도 무방합니다.
```
≤ 200 Korean chars; reuse 08-format fields (paper_doi/title/date/tier/
dm_channel/member_init). Self banned-term scan must be clean.

## Step 4 — propose keyword/logic evolution diffs (manual-apply only)

Aggregate `thumbs_down`/`already_read` proposals across this run + any
prior run with similar signals (read from the predecessor packets / past
runs). For a topic-keyword that appears in ≥2 unit-member rejections,
propose a diff to that unit's scout query seeds / exclusion list. Emit
`drafts/<RID>/EVOLUTION.md` (human-review):

```
# Evolution proposals — <RID>

## <unit_id> — proposed changes
- ADD exclusion_keyword: "<term>"   (evidence: 2 replies — quote_a; quote_b)
- DROP scout-query-seed: "<phrase>" (evidence: …)
- NO CHANGE elsewhere.

Apply (operator, gated):
  ! python scripts/apply_feedback.py --run-id <RID>     # ledger updates
  # re-run scouting next cycle with updated keywords
```

Never modify `rules/*` or `.claude/agents/*` automatically — those are
binding contracts; evolution shifts are config/keyword-level diffs the
operator applies (manual-only).

## Outputs
- `state/runs/<RID>/_feedback_proposals.json`
- `state/runs/<RID>/09_followups.json`         (08-schema; deliver-ready)
- `drafts/<RID>/EVOLUTION.md`                  (committed human-review)

## Hand-off
Print a summary table (member · signal · confidence · proposed-exclusion
or picked-alt · followup-needed?) and the exact two operator `!` commands
to apply: `apply_feedback.py` then `deliver.py --mode dm --drafts
09_followups.json --send --operator-approved` (gate intact).
