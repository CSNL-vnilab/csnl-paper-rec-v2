---
name: paper-rec-score
description: >
  Stage 6 of csnl-paper-rec: value-dimension scoring of deduped candidates.
  Executed by the in-session Opus agent team (operator-driven). NOT a script,
  NOT Ollama, NOT an API key. Input 05_deduped.json → output 06_scored.json.
  Triggers on: 'score candidates', 'rank papers', 'paper rec score',
  '논문 평가', '가치 평가', 'D1-D5 scoring'. Use after 05_deduped.json exists.
---

# csnl-paper-rec — Stage 6: Value-Dimension Scoring

> **Execution mode: in-session Opus agent team, operator-driven.**
> This stage is NOT automated. No Anthropic API key is used anywhere in
> csnl-paper-rec's unattended paths. The operator launches the Opus agent
> team interactively; agents score candidates against D1–D5, write
> 06_scored.json, and hand off to Stage 7 (post/SKILL.md).

## Why Value Dimensions, Not Weighted Averages

A researcher decides to read a paper because of *one strong reason*, not an
average of weak ones. Five orthogonal value dimensions capture that structure;
the composite is `max`, not `mean`. This directly ports the csnl-paper-scout
scoring philosophy to the project-DB-driven csnl-paper-rec pipeline.

## Input

`state/runs/<RUN_ID>/05_deduped.json` — output of `pipeline/04_dedup.py`.

Shape per unit:
```json
{
  "unit_id": "JOP",
  "members": ["JOP"],
  "candidates": [
    {
      "doi": "10.xxxx/xxxxx",
      "title": "...",
      "authors": ["Last F"],
      "venue": "Nature Neuroscience",
      "date": "2026-04-10",
      "abstract": "...",
      "source": "semantic_scholar",
      "is_preprint": false,
      "tier": "strict"
    }
  ]
}
```

Project context for each unit's members is available in
`state/runs/<RUN_ID>/01_active_projects.json` (fields: `purpose`,
`background`, `manipulation_variables`, `modalities`). Load both files
before scoring.

## The 5 Value Dimensions

Score each candidate × each unit member on all five dimensions (0–10).

| Dim | Name | Core Question |
|-----|------|---------------|
| D1 | **Direct Advance** | 이 논문의 결과/방법이 해당 member의 실험 또는 모델에 바로 활용 가능한가? |
| D2 | **Hypothesis Tension** | 해당 member의 가설을 지지하거나 도전하는 실증적 증거인가? |
| D3 | **Methodological Import** | 빌려올 수 있는 새 분석 기법 또는 패러다임이 있는가? |
| D4 | **Competitive Signal** | 같은 질문을 다른 그룹이 독립적으로 추격하고 있다는 신호인가? |
| D5 | **Reframing Power** | 해당 member의 문제를 새로운 개념 틀로 보게 해주는가? |

### Dimension Rubric

| Score | Meaning |
|-------|---------|
| 0–2 | 해당 차원에서 가치 없음 |
| 3–4 | 약한 연결 — 같은 분야이나 직접적이지 않음 |
| 5–6 | 의미 있는 연결 — 방법 또는 현상 하나가 겹침 |
| 7–8 | 강한 연결 — 해당 차원에서 직접적 가치 |
| 9–10 | 즉각적 행동 유발 — 읽은 후 실험/분석/해석을 바꿔야 함 |

## Composite Score: Max, Not Average

```
member_score(paper, member) = max(D1, D2, D3, D4, D5)
```

**Tie-breaking within a member**: `mean(D1..D5)` — 동일 max를 가진 논문
중 더 다양한 차원에서 가치 있는 것이 우선.

**Paper-level score**: 해당 unit의 모든 members에 대한 `member_score` 중
최대값.

```
paper_score(paper, unit) = max over members of member_score(paper, member)
```

## Anti-Hallucination Rules (mandatory)

1. **Abstract-only rule**: 스코어링은 abstract에 명시된 내용만으로 한다.
   "아마 본문에서 다룰 것 같다"는 근거로 사용 금지.

2. **Specificity gate for ≥7**: 어떤 dimension에서든 7점 이상을 부여하려면
   abstract에서 직접 인용구(direct quote)나 구체적 수치/주장을 반드시 명시
   해야 한다. 인용구 없이 7+ 부여 불가.

3. **Negative scoring is mandatory**: 관련 없는 dimension은 0점. "약간 관련
   있을 수도 있음"은 0점이다. 3–4점 채우기 금지.

4. **D2 directionality**: D2에서 높은 점수를 줄 때는 어떤 member의 어떤
   가설에 대해 어떤 방향(support / challenge)의 tension인지 명시.

5. **No score laundering**: 같은 사실을 두 dimension에서 중복 계산 금지.

## Scoring Workflow

For each unit in `05_deduped.json`:

### Step 1: Load project context

Read `01_active_projects.json`, find all projects belonging to the unit's
members. Extract per project: `purpose.research_question`, `purpose.hypothesis`,
`manipulation_variables.independent_vars`, `manipulation_variables.dependent_vars`,
`background.prior_studies[].doi`, `modalities`.

### Step 2: Score each candidate × each member

For every (candidate, member) pair:
1. Read candidate abstract in full.
2. Check each dimension against the member's project context.
3. Write a brief reasoning per dimension (1–2 sentences).
4. If any dimension ≥ 7: include a direct quote from the abstract.
5. Record `{D1, D2, D3, D4, D5, member_score, best_dim, reasoning}`.

### Step 3: Derive paper-level score and top selection

```
paper_score = max(member_scores across all members of unit)
```

Rank all candidates in the unit by `paper_score` descending.

**Selection criteria:**
- **Top 1** = highest `paper_score` → becomes `top` for the unit.
- **Carryover** = next 2–5 by rank → becomes `carryover` list.
- **Minimum threshold**: `paper_score < 7` → unit receives no recommendation
  this run. The carryover list is still emitted (for future dedup reference)
  but `top` is `null`.

## Output

`state/runs/<RUN_ID>/06_scored.json`

```json
{
  "run_id": "20260518-1400",
  "generated_at": "2026-05-18T15:30:00+09:00",
  "units": [
    {
      "unit_id": "JOP",
      "top": {
        "doi": "10.xxxx/xxxxx",
        "title": "...",
        "authors": ["Last F"],
        "venue": "Nature Neuroscience",
        "date": "2026-04-10",
        "abstract": "...",
        "source": "semantic_scholar",
        "is_preprint": false,
        "tier": "strict",
        "paper_score": 9,
        "best_member": "JOP",
        "best_dim": "D4",
        "member_scores": {
          "JOP": {
            "D1": 3, "D2": 8, "D3": 0, "D4": 9, "D5": 2,
            "member_score": 9,
            "reasoning": {
              "D2": "\"serial dependence deteriorates decision accuracy\" — JOP H1의 adaptive Bayesian 해석에 직접 도전",
              "D4": "[PI name]이 estimation-only paradigm에서 독립 실험을 수행; 경쟁 신호"
            }
          }
        },
        "abstract_quote": "\"serial dependence deteriorates rather than improves perceptual decision-making\""
      },
      "carryover": [
        {
          "doi": "10.yyyy/yyyyy",
          "paper_score": 7,
          "best_member": "JOP",
          "best_dim": "D1",
          "member_scores": { "JOP": {"D1": 7, "D2": 2, "D3": 3, "D4": 0, "D5": 1, "member_score": 7} },
          "abstract_quote": "\"orientation estimation bias scales linearly with memory delay\""
        }
      ],
      "no_rec_reason": null
    },
    {
      "unit_id": "BYL",
      "top": null,
      "carryover": [],
      "no_rec_reason": "max_paper_score_below_7"
    }
  ]
}
```

**Shape rules (fixed by BUILD_SPEC contract):**
- `top` is either a full scored paper object or `null`.
- `carryover` is a list of 0–5 scored paper objects (may be empty).
- `no_rec_reason` is `null` when `top` is present; one of
  `"max_paper_score_below_7"` | `"no_candidates_after_dedup"` when `top` is
  `null`.
- `abstract_quote` is required on `top` when `paper_score >= 7` (it always
  is, by the threshold rule). Quote must be verbatim from the abstract.
- `member_scores` keys are member inits; `reasoning` only needs entries for
  dimensions that scored ≥ 5.

## Handoff

After writing `06_scored.json`, proceed to `pipeline/post/SKILL.md` to draft
Korean channel messages. The operator's Opus agent team continues in the same
session — no new API call, no script.

Print a summary before handing off:
```
Stage 6 complete — 06_scored.json written
  JOP     : top score 9 (D4, JOP) — rec queued
  BYL     : no rec (max=5, below threshold)
  MSY     : top score 8 (D1, MSY) — rec queued
  SMJ     : top score 7 (D3, SMJ) — rec queued
  JYK     : no rec (0 candidates after dedup)
  SYJ+BHL : top score 8 (D2, SYJ) — rec queued
→ 4 units with recommendations; proceed to post/SKILL.md
```
