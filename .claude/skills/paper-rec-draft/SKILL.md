---
name: paper-rec-draft
description: >
  Korean academic recommendation drafting for draft-writer. Compose one
  unit's channel message + DM-ping from the scout's chosen paper, grounded
  in a named project element plus a verbatim full-text point, no signature,
  추천 근거 150–280자. Use to draft or revise a unit message; also on
  "리뷰 반영", "초안 수정", "다시 작성".
---

# paper-rec-draft — Korean academic message (one unit)

Tone law `rules/01_tone.md`, grounding law `rules/03_grounding.md`,
philosophy `rules/06_philosophy.md`. Also see `pipeline/post/SKILL.md`. Write
to the rules and their intent; the delivery lint is only a backstop.

## Inputs
Scout `top` (doi/title/authors/venue/date/tier/quote/grounding), the unit's
project rows, `display_names`/`channel_ids`/`dm_inits`; on revision, the
`_review_<unit>.json` findings.

## channel_text structure (Korean, 합쇼체)

```
<이름> 연구원께,

논문: <title>
저자: <Last F, …> — <venue>, <YYYY-MM>
DOI: https://doi.org/<doi>

추천 근거: <2–3 measured sentences, 150–280 Korean chars: name a specific
project element (slug + variable / connected_graph paradigm / a
background.prior_studies DOI / a research_question·hypothesis phrase) AND a
concrete full-text point — embed the scout's verbatim quote in "…". State
the correspondence as inferred.>

활용: <one concrete sentence: how this unit could use it in their pipeline.>

해당 추천이 부적합하면 본 채널로 회신해 주십시오.
```
For multi-member units (SYJ+BHL) greet both: `조수영 · 이보현 연구원께,`.

## Hard constraints
- **No signature** — no `— Claude`, model name, AI/assistant self-reference.
- No emoji, no `!`, no superlative/affect (`훌륭`/`최고`/`매우 적합`/
  `강력히 추천`/`놀라운`/`감사합니다`), no AI jargon, no internal-ops
  vocabulary (subagent/orchestrator/harness/ledger/tier/run_id/D1–D5/axis/
  confidence/round/…).
- `paradigm` and `framework`: ≤1 occurrence **each**, message-wide.
- Inferred-fit only: `mapping 됩니다`/`paradigm 이 일치합니다`/`비교
  가능합니다`; never `직접 일치`/`완벽하게 부합`/`최적의 논문`/`핵심 직접`.
- `추천 근거` = 150–280 Korean chars (spaces incl.). If long, tighten
  wording without dropping the named anchor or the load-bearing quote.
- DOI always full `https://doi.org/…`.
- If `tier=="relaxed"`: add one sentence — strict 1-year window had no
  candidate so the search was widened (no internal words like "tier").

## dm_ping_text (≤2 lines, literal placeholder)
```
INIT_claude 채널에 이번 주 추천 논문을 게시했습니다.
{permalink}
```
No greeting, no title/DOI, no signature. `{permalink}` stays literal —
deliver.py substitutes it post-permalink.

## Self-check before writing output
- [ ] greeting form + bare labels + close line, no signature
- [ ] 추천 근거 names a traceable project element AND a full-text point
- [ ] verbatim quote present and actually in the scout's fetched text
- [ ] 150–280 chars; paradigm/framework ≤1 each; no banned/internal terms
- [ ] inferred phrasing; relaxed disclaimer iff tier==relaxed
- [ ] dm_ping_text ≤2 lines with literal {permalink}

## Output
Upsert this unit into `state/runs/<RID>/07_drafts.json` `drafts[]`:
`{unit_id, channel_ids, dm_inits, channel_text, dm_ping_text, paper_doi,
paper_title, paper_date, tier}` (paper_* and tier copied from scout `top`).
Return the rendered message + char count + a self banned-term scan. Revision
mode: apply only the reviewer's findings; keep clean spans and the grounding.
