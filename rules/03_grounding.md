---
name: grounding-rule
description: Every recommendation must cite a specific verifiable element of the unit's actual project data. Generic topic claims are insufficient. Weak vs strong examples.
source: feedback_interview_groundedness.md + feedback_past_focus_and_resilience.md + plugin/rules/02_grounded.md + BUILD_SPEC.md §01_extract_topics.py
---

## Core requirement

The `추천 근거:` field must reference at least one **specific, verifiable element** drawn
from the unit's `csnl_research.projects` row. This element must be observable in the
structured JSON fields produced by `pipeline/01_extract_topics.py`.

Acceptable grounding types:

1. **A manipulation variable** — a named independent or dependent variable from
   `manipulation_variables_jsonb.independent_vars` or `dependent_vars`.
   Example: `ANALYSIS_SPATIAL_SIGMA`, `anchor_alpha = {20, 90}`, `Refs=[-45,-20,-10,10,20,45]`

2. **A connected_graph paradigm** — a `shared_paradigm_with` entry or a named paradigm
   from `connected_graph_jsonb`.
   Example: "predictive coding paradigm shared with JYK project"

3. **A background prior_study DOI** — a DOI from `background_jsonb.prior_studies[].doi`.
   Example: `Lim 2023 doi 10.1038/s41598-023-45505-5`,
   `Gu et al. 2025 Neuron doi 10.1016/j.neuron.2025.xx`

4. **A research question or hypothesis phrase** — a direct extract from
   `purpose_jsonb.research_question` or `purpose_jsonb.hypothesis`.

5. **A modality** — a specific measurement modality from `modalities_jsonb` that the
   recommended paper also uses (e.g., "eyetracking + behavior paradigm matches the
   unit's `modalities: [eyetracking, behavior]`").

## Weak vs strong grounding

### Weak (insufficient — do not use)

```
추천 근거: 본 논문은 시각적 지각 연구에 관한 것으로, 연구원의 관심 분야와 일치합니다.
```

Problems: "시각적 지각" is too generic. No specific project field is cited. This reasoning
cannot be verified against any structured data.

### Strong (required standard)

```
추천 근거: 본 논문의 surround suppression 측정 paradigm 이 connected_graph 의
shared_paradigm_with 항목("Najemnik & Geisler 2005")과 동일한 사전(prior)을 채택합니다.
manipulation_variables 의 independent_var `ANALYSIS_SPATIAL_SIGMA` 범위와 본 논문의
자극 크기 조건이 직접 비교 가능합니다.
```

Why strong: cites a named `shared_paradigm_with` entry, a named `independent_var`, and
the reasoning is verifiable by reading those two fields in the project row.

### Another strong example (using background DOI)

```
추천 근거: 본 논문은 배경 문헌 `Gu et al. 2025 Neuron doi 10.1016/j.neuron.2025.xx`
의 후속 연구로, 해당 DOI 가 본 유닛의 background.prior_studies 에 등록되어 있습니다.
fMRI decoding 방법론의 적용 방식이 `modalities: [fMRI]` 조건과 일치합니다.
```

## Inferred-fit caveat

Topic fit inferred from structured data is **inferred**, not confirmed. The researcher has
not validated that the inferred keywords match their current priorities. The grounding
statement must reflect this:

- Allowed: `prior 가 mapping 됩니다`, `paradigm 이 일치합니다`, `조건이 비교 가능합니다`
- Forbidden: `직접 일치`, `핵심 직접`, `최적의 논문`, `완벽하게 부합`

Source: `docs/DECISIONS-2026-05-18.md` — "deprecated_stub excluded; topic facts are
*inferred-fit, not confirmed-fit* until researcher feedback (skepticism rule)."

## Grounding source in the pipeline

The Opus scoring agent (SKILL `pipeline/score/SKILL.md`) extracts the grounding element
from `02_topic_bundles.json` fields:

- `keywords` — from `purpose.research_question/hypothesis` + `manipulation_variables`
- `anchor_dois` — from `background.prior_studies[].doi`
- `connected_graph.shared_paradigm_with`

A score ≥ 7 (on any dimension) **requires** a direct quote from the paper abstract.
The grounding statement in `추천 근거:` must link that quote to a specific project field.

Source: BUILD_SPEC.md §score/SKILL.md — "score ≥7 needs a direct quote."

## Self-check before draft

Before finalizing `07_drafts.json`, the Opus agent must confirm:

- [ ] `추천 근거:` cites at least one of: manipulation variable / connected_graph entry /
      background DOI / research_question phrase
- [ ] The cited element is traceable to a specific JSON field in `02_topic_bundles.json`
- [ ] Language is measured, not superlative (see `rules/01_tone.md`)
- [ ] No internal system field names appear in researcher-facing text (no `manipulation_variables_jsonb`,
      no `connected_graph_jsonb` — only the human-readable content of those fields)

--- end of 03_grounding.md
