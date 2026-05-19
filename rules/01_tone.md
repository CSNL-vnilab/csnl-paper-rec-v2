# 01 — Tone & message form

Researcher-facing recommendation messages. Korean, academic, measured.
Sources: `feedback_paper_rec_tone.md`, `feedback_channel_routing_and_strict_tone.md`,
`feedback_channel_tone_cleanup.md`, plugin `rules/01_tone.md`, `agents/archiver.md`,
adjudicated by `docs/DECISIONS-2026-05-18.md`.

Two layers enforce this:
1. **Primary — the drafting Opus agent** (`pipeline/post/SKILL.md`) writes to the
   full rules below (style, structure, ≤1 caps, neutrality). This is where tone
   is actually achieved.
2. **Backstop — `deliver.py` mechanical lint** parses the `BANNED_TERMS` fenced
   block at the end of this file and aborts a unit's send on any case-insensitive
   substring hit. It is deliberately a *conservative hard-unsafe set* (leaked
   AI/model self-reference, internal-ops tokens, blatant superlatives) — NOT the
   full style list — so it never false-positives on a legitimate paper title or
   author (e.g. an author named "Claude", a paper about "GPT").

## Form

- Greeting: `<연구자 이름> 연구원께,` — no `안녕하세요`, no `!`.
- Bare section labels, each on its own line: `논문:`, `저자:`, `발행:`, `DOI:`,
  `추천 근거:`, `활용:`.
- `추천 근거:` — 2–3 sentences, measured, grounded in a specific element of the
  unit's actual project (see `03_grounding.md`). State fit as *inferred*, not
  asserted as certain.
- `활용:` — one concrete sentence.
- Close with the channel-reply line (see `05_delivery.md`). **No signature.**

## Hard rules

- **No signature.** `— Claude`, `- Claude`, `(opus…)`, any model name, any
  "AI/assistant" self-reference — forbidden. Slack already shows the bot sender.
  (2026-05-13 + plugin rules supersede the 2026-05-08 template that still showed it.)
- **No emoji. No exclamation marks. No affective/superlative language**
  (`훌륭`, `최고`, `매우 적합`, `강력히 추천`, `놀라운`, `감사합니다`).
- **No AI jargon**: delve, leverage, robust, comprehensive, holistic, synergy,
  tapestry, meticulous, "navigate the complexities".
- **No internal-ops vocabulary**: subagent, orchestrator, harness, ledger,
  safe_memory, member_uncertainty, nas_inventory, memev, fire/발사, 라운드,
  사이클, axis, gap, confidence, `kind=`, `≥0.85`, run_id.
- `paradigm` and `framework`: each ≤1 occurrence per message.
- Neutral phrasing: `paradigm 이 일치합니다` — not `핵심적으로 부합` / `매우 적합`.

## Example

GOOD:
```
이보연 연구원께,

다음 주 후보 논문 한 편을 전달드립니다.

논문: <title>
저자: <authors>
발행: <venue>, <YYYY-MM>
DOI: https://doi.org/<doi>

추천 근거: biasvar 의 orientation estimation 과제에서 사용하시는 stimulus-specific
loss 가정과 본 논문의 추정 편향 분석이 연결됩니다. 방법론적 대응 관계가 있는 것으로
보입니다.

활용: 본 논문의 편향-분산 분해 절차를 biasvar 분석 파이프라인의 비교 기준으로
검토하실 수 있습니다.

해당 추천이 부적합하면 본 채널로 회신해 주십시오.
```

BAD: `안녕하세요! 매우 적합한 훌륭한 논문을 추천드립니다 😊 … — Claude (opus)`
(greeting+`!`, superlatives, emoji, signature, model name — all forbidden).

## Mechanical lint set

`deliver.py` reads exactly the block below (one term per line, case-insensitive
substring). Curated to avoid false positives on legitimate paper metadata.

```BANNED_TERMS
— claude
- claude
—claude
(claude)
claude opus
claude sonnet
claude haiku
claude code
anthropic
chatgpt
openai
gpt-4
gpt-5
as an ai
ai assistant
ai 어시스턴트
언어모델로서
대규모 언어 모델로서
훌륭
최고의
매우 적합
강력히 추천
놀라운
감사합니다
delve
leverage
robust
comprehensive
holistic
synergy
tapestry
meticulous
navigate the complexities
subagent
orchestrator
safe_memory
member_uncertainty
nas_inventory
fire_lock
q_hash
memev
harness_runner
exploration_plan
```
