---
name: paper-rec-post
description: >
  Stage 7 of csnl-paper-rec: draft Korean academic recommendation messages
  per unit. Executed by the in-session Opus agent team (operator-driven).
  Input 06_scored.json → output 07_drafts.json. Tone governed by
  rules/01_tone.md + rules/03_grounding.md. No signature. Ground the
  추천 근거 in a specific project field or DOI from the unit's active
  projects. Also produce a ≤2-line DM-ping text per unit.
  Triggers on: 'draft messages', 'draft recommendations', 'paper rec post',
  '메시지 초안', '추천 초안'. Use after 06_scored.json exists.
---

# csnl-paper-rec — Stage 7: Drafting

> **Execution mode: in-session Opus agent team, operator-driven.**
> Same session as Stage 6. No API key. No Ollama. The operator's Opus agents
> draft each unit's message, run the internal tone check, and write
> 07_drafts.json. The operator reviews the full dry-run preview via
> `scripts/deliver.py --dry-run` before any real send.

## Input

`state/runs/<RUN_ID>/06_scored.json` — output of Stage 6 (score/SKILL.md).
`state/runs/<RUN_ID>/01_active_projects.json` — for grounding (project
fields and prior-study DOIs).
`config/researchers.yaml` — channel IDs, display names.
`rules/01_tone.md` — mandatory tone rules (read fully before drafting).
`rules/03_grounding.md` — mandatory grounding rules.

Draft only for units where `top` is non-null.

## Tone Constraints (from `rules/01_tone.md`)

Read `rules/01_tone.md` in full before writing any draft. The rules file
contains a `BANNED_TERMS` fenced block; every term in that block is forbidden
in any channel message or DM-ping text. Key constraints from current lab
rules:

- **Korean academic register**: 격식체 (합쇼체), not casual.
- **No model name, no AI self-reference** (`Claude`, `AI`, `모델`, `Sonnet`,
  `Opus`, `에이전트`, `서브에이전트`, `오케스트레이터`, `safe_memory` 등 금지).
- **No signature**: `— Claude`, `— AI`, `AI드림` 등 일체 금지.
- **No superlatives, marketing language, or abstract affect**: `혁신적`,
  `획기적`, `매우 중요한`, `흥미롭게도` 등 금지.
- **`패러다임` / `프레임워크`**: 메시지 전체에서 각 1회까지만 허용.
- **No emoji**.
- **No system/harness vocabulary** in the channel message body.

## Grounding Constraints (from `rules/03_grounding.md`)

The `추천 근거` section must be grounded in at least one of:
1. A real NAS file path or code variable name from the unit's active
   project(s) — if available from `01_active_projects.json`.
2. A DOI from `background.prior_studies[].doi` of the unit's projects.
3. A specific field value from `manipulation_variables` or `purpose` of
   the unit's projects (e.g., a named dependent variable, a paradigm name
   with a confirmed project slug reference).

**Abstract inference prohibition**: 논문 abstract에서 추론한 내용만으로
추천 근거를 채우지 않는다. 근거는 반드시 unit의 실제 프로젝트 데이터와
연결되어야 한다.

## Channel Message Template

Write in Korean. Do NOT follow this template word-for-word — use it as a
structural guide. Vary phrasing across units.

```
[논문 제목]
[저자(s) — 학술지/서버, 연도]
DOI: https://doi.org/<doi>

추천 근거:
[1–2 문장. 구체적으로: 어떤 프로젝트(슬러그)의 어떤 변수/가설/DOI와
연결되는지. abstract quote 1건 포함 (score/SKILL.md의 abstract_quote 활용).]

[선택: 방법론적 메모 또는 경쟁 신호 1문장, 필요한 경우만]
```

**Rules:**
- Total length: 150–280자 (Korean character count, spaces included).
- No blank-line padding beyond the structure above.
- DOI URL must be full `https://doi.org/` form.
- If `tier == "relaxed"`: append one sentence noting the paper is from an
  extended window (e.g., "엄격한 최신 기준(1년)을 적용했을 때 후보가 없어
  탐색 범위를 2년으로 확대하였습니다.").
- No `D1`/`D2`/`D3` labels in the message — these are internal scoring
  artifacts, not researcher-facing language.

## DM-Ping Text Template

The DM ping is a short pointer from the researcher's DM to the channel post.
It is sent AFTER the channel post is published (deliver.py fetches the
permalink first, then sends DM with the permalink embedded).

```
INIT_claude 채널에 이번 주 추천 논문을 게시했습니다.
https://[slack_permalink]
```

**Rules:**
- ≤ 2 lines.
- No greeting, no signature, no elaboration.
- Include the `{permalink}` placeholder; `deliver.py` substitutes the real
  Slack permalink at send time.
- If unit has multiple members (SYJ+BHL), each member receives an identical
  DM ping (to their respective DM channels) — draft once, deliver.py fans out.

## Drafting Workflow

For each unit with `top != null`:

### Step 1: Gather context

- Load `top` from `06_scored.json`: doi, title, authors, venue, date, abstract,
  tier, paper_score, best_dim, abstract_quote.
- Load the unit's projects from `01_active_projects.json`.
- Identify the grounding anchor: pick the most specific match between the
  paper's content and the unit's project fields (see Grounding Constraints).

### Step 2: Draft channel_text

Write the channel message following the template. Check character count
(150–280자). Run internal banned-term scan against the `BANNED_TERMS` block
from `rules/01_tone.md` before finalizing.

### Step 3: Draft dm_ping_text

Write the DM-ping using the template. Insert `{permalink}` as a literal
placeholder — deliver.py replaces it.

### Step 4: Self-review checklist

Before accepting the draft:
- [ ] No banned terms (checked against rules/01_tone.md BANNED_TERMS block)
- [ ] No signature
- [ ] No AI/model/system vocabulary
- [ ] abstract_quote appears verbatim (or paraphrased within quotes — prefer verbatim)
- [ ] Grounding anchor cites a real project slug, variable, or DOI
- [ ] Character count in 150–280 range
- [ ] DOI URL is full https://doi.org/ form
- [ ] tier=="relaxed" disclaimer present if applicable
- [ ] dm_ping_text is ≤ 2 lines and contains `{permalink}`

### Step 5: Record in output

Emit the draft into `07_drafts.json` (one entry per unit with a recommendation).

## Output

`state/runs/<RUN_ID>/07_drafts.json`

```json
{
  "run_id": "20260518-1400",
  "generated_at": "2026-05-18T16:00:00+09:00",
  "drafts": [
    {
      "unit_id": "JOP",
      "channel_ids": ["C0B3FTHAVR8"],
      "dm_inits": ["JOP"],
      "channel_text": "...(Korean channel message, 150-280자)...",
      "dm_ping_text": "INIT_claude 채널에 이번 주 추천 논문을 게시했습니다.\n{permalink}",
      "paper_doi": "10.xxxx/xxxxx",
      "paper_title": "...",
      "paper_date": "2026-04-10",
      "tier": "strict"
    },
    {
      "unit_id": "SYJ+BHL",
      "channel_ids": ["C0B3FTNR00J", "C0B39GVLKCK"],
      "dm_inits": ["SYJ", "BHL"],
      "channel_text": "...",
      "dm_ping_text": "INIT_claude 채널에 이번 주 추천 논문을 게시했습니다.\n{permalink}",
      "paper_doi": "10.zzzz/zzzzz",
      "paper_title": "...",
      "paper_date": "2026-03-15",
      "tier": "strict"
    }
  ]
}
```

**Shape rules (fixed by BUILD_SPEC contract):**
- `channel_ids`: list of Slack channel IDs (from `config/researchers.yaml`).
  SYJ+BHL = two channel IDs. All others = one.
- `dm_inits`: list of member inits who receive a DM ping. Must match
  `unit.members` from `config/researchers.yaml`.
- `channel_text`: the full Korean message, plain text (no Slack markdown
  formatting — deliver.py sends as `text:` field). Newlines as `\n`.
- `dm_ping_text`: ≤ 2 lines, contains literal `{permalink}`. deliver.py
  will `.format(permalink=actual_url)` before sending.
- `paper_doi`, `paper_title`, `paper_date`, `tier`: copied from `top` in
  `06_scored.json`.

## Handoff

After writing `07_drafts.json`, the operator runs:
```
python scripts/deliver.py --run-id <RUN_ID>
```
(defaults to `--dry-run`) to review the full preview — channel targets, DM
targets, exact message bodies, and ledger rows that WOULD be written. No
message is sent until `scripts/deliver.py --send --operator-approved` is
invoked AND `state/.APPROVED_<RUN_ID>` exists.

Print summary before handing off:
```
Stage 7 complete — 07_drafts.json written
  JOP     : channel C0B3FTHAVR8, DM JOP → ready
  MSY     : channel C0B4A6WAGNL, DM MSY → ready
  SMJ     : channel C0B39GQK067, DM SMJ → ready
  SYJ+BHL : channels C0B3FTNR00J + C0B39GVLKCK, DMs SYJ+BHL → ready
→ Run: python scripts/deliver.py --run-id <RUN_ID>
```
