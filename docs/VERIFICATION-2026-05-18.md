# Integration verification — 2026-05-18

Deterministic core verified end-to-end against the **live** `csnl_research.projects`
(read-only). Network discovery + Opus-agent scoring/drafting + delivery are the
operator-gated dry-run (next step).

## Stage 00 — active-project selection (live DB)

10 projects matched `phase ∈ {data_collection, analysis, manuscript_draft} AND confidence_avg ≥ 0.7`:

| init | project_slug | phase | conf |
|---|---|---|---|
| BHL | bhl_paradigm_pilot | data_collection | 0.97 |
| BYL | biasvar | data_collection | 0.88 |
| JOP | grannmds | analysis | 0.91 |
| JOP | granrdt | analysis | 0.91 |
| JOP | ringrepsca | analysis | 0.97 |
| JOP | time2dist | analysis | 0.97 |
| JYK | dynamic_bias | analysis | 0.96 |
| MSY | cat_mag_main | analysis | 0.86 |
| MSY | face_cond_ver10 | data_collection | 0.75 |
| SMJ | concentricity | analysis | 0.90 |

Correctly **excluded**: `SYJ/syj_d2e_distractor_effect` (phase `lit_review_post_null`),
`SYJ/syj_jsl_sd_onboarding` (deprecated_stub), `BHL/bhl_sk_organization` (phase `mapping`),
`SMJ/visual_search` (phase null).

## Stage 01 — unit construction

6 units. **SYJ+BHL correctly merged** → one unit, channels `[C0B3FTNR00J, C0B39GVLKCK]`.

## ⚠ Operator flag — SYJ has zero "active" projects under the chosen criteria

SYJ's only substantive project (`syj_d2e_distractor_effect`) is `phase=lit_review_post_null`,
which the decision-3 phase set excludes. Consequence: the **SYJ+BHL unit's recommendation
is grounded solely in BHL/bhl_paradigm_pilot**.

Note the tension: a researcher in `lit_review_post_null` is *actively searching the
literature after a null result* — arguably the highest-value recipient of paper
recommendations. Options for the operator (decision-3 refinement, not changed unilaterally):
- (a) keep as-is — SYJ covered indirectly via the shared BHL project;
- (b) add `lit_review_post_null` (and/or `manuscript_draft` variants) to the active phase set;
- (c) special-case lit-review-phase projects as always-eligible.

## Wiring fix applied

`pipeline/00_select_projects.py` gained a `psql` fallback (psycopg2 absent in system
Python; `psql` + `~/.claude/csnl-archive/.env` is the lab's working pattern) and
`SUPABASE_DB_NAME` now defaults to `postgres`. psycopg2 remains the primary path.

## Not yet exercised (operator-gated dry-run)

Stages 02 discover / 03 verify / 04 dedup (external read-only APIs) and 06 score /
07 draft (in-session Opus agent team) — run via `scripts/run_manual.py`, then
`scripts/deliver.py --run <RID>` (dry-run default). Real send stays hard-blocked
behind `--send --operator-approved` + `state/.APPROVED_<RID>`.
