# P19 — Scientific-rigor recommendation algorithm

**Date:** 2026-05-26 (KST)
**Status:** Ship-with-cuts after 3-Opus design round + codex adversarial
review. **NOT a complete replacement** of the current `composite =
0.55·cos + 0.30·dim_score + 0.05·n_combos` pipeline — instead, an
additive per-researcher signal that complements the existing scoring.
**Three CRITICAL codex findings are deferred** with explicit markers;
re-evaluation requires non-circular ground truth.

## Process

Three parallel Opus agents (`A: fingerprint extraction` /
`B: ranking + Bayesian update` / `C: validation harness`) produced
designs. A codex adversarial review attacked the central claim
("does this achieve scientific literature discovery?") and returned a
no-ship verdict on the full proposal with a ship-with-cuts path. This
doc captures the synthesis + the cut decisions.

The conversation lives in this repo's git log under P19. The agent
transcripts are at `/private/tmp/claude-501/.../tasks/` (ephemeral).

## What we ship (P19a — this commit)

### 1. Per-researcher scientific fingerprint extraction (Voice A, partial)

A new operator-run script `scripts/archive/build_fingerprints.py` reads
each active researcher's `csnl_research.projects` rows and emits a
JSON fingerprint at `state/archive/fingerprints/<INIT>.json` carrying:

- `phrases[]` — multi-word noun phrases extracted from the researcher's
  OWN project text via a lexicon-anchored Pass-A only (the lexicon
  lives at `state/archive/known_phrases.txt`, operator-curated). Pass B
  (capitalized-NP regex) and Pass C (bilingual unigrams/bigrams) are
  **deferred** until Pass-A coverage is empirically demonstrated.
- `novel_terms[]` — researcher phrases NOT in the existing 52-tag
  taxonomy. Surfaced for operator's manual lexicon-growth review.
- `tag_priors{}` — soft prior weights for the existing 52-tag
  taxonomy, derived from researcher-phrase × taxonomy keyword overlap.
- `method_signature{}` — IV/DV names and paradigm compounds from
  `manipulation_variables_jsonb` (kept structured, used in §2).
- A backward-compatible `dim_preferences{}` projection.

The fingerprint is read by `build_researcher_queue.py` when present;
absence falls back to the existing `_derive_dim_prefs()` behavior.

Domain IDF: `scripts/archive/build_corpus_idf.py` runs once over
`merged_papers.jsonl` to identify "scientific" terms vs filler;
output at `state/archive/lexicon_idf.json`.

### 2. BM25 keyword overlap signal (Voice B, partial)

Add an `s_kw_bm25` term to the existing composite. When a researcher's
fingerprint is present, compute BM25 of the researcher's `phrases[]`
against `paper.title + paper.abstract` and add a weighted term:

```
composite = 0.50·cos + 0.20·dim_score + 0.20·s_kw_bm25 + 0.05·n_combos + s_floor_softening
```

where `s_floor_softening` replaces the hard `cos ≥ 0.18` gate with
`(1 - exp(-3·max(cos, s_kw_bm25))) · base_composite` — a paper with
strong keyword match and zero cosine can still survive.

Initial weights are NOT calibrated against CWLL (see codex finding #1).
They're operator-tunable in code constants and the doc explicitly
flags them as "first-cut, not validated".

### 3. Explain-WHY skeleton (Voice B, partial)

`build_researcher_queue.py` now writes `dim_match.top_signals` (an
ordered list of 1-2 strongest signal contributors per paper) into
`archive_researcher_queues.dim_match` JSONB. `pick_next.py` surfaces
it; SKILL.md Stage 2 renders it as one Korean sentence under the MCQ.

### 4. Live MCQ-precision monitoring (Voice C, partial)

A new operator view + script:

- `state/schema_archive.sql` adds a CREATE OR REPLACE VIEW
  `vw_archive_mcq_quality` computing per-researcher 30-day MCQ
  precision = `save_later / (save_later + not_relevant)`. Activates once
  n ≥ 10. (Original formula included `tell_me_more`; that option was
  retired 2026-05-28 — see state/migrations/2026-05-28_drop_tell_me_more.sql.)
- `scripts/archive/validate_drift.py` reads the view + computes the
  stale-fingerprint flags codex finding #2 calls for.

CWLL-derived NDCG backtest is **CUT** as a primary success metric (see
codex finding #1).

## What we cut (codex no-ship items)

| Cut | Codex finding | Why |
|---|---|---|
| **CWLL backtest as primary success metric** | #1 ground-truth illegitimacy | CWLL log = one operator's reading history. Backtesting against it just measures how well we mimic operator taste. Defer until we have a non-circular signal. |
| **Real-time Thompson sampling claim** | #8 operator-side write coupling | Plugin allowlist + operator rebuild cadence means MCQ responses don't actually loop into the queue in real time. Keep posterior mean only; rename "in-session re-rank" honestly. |
| **Fixed κ=0.05 exploration knob** | #6 exploration knob + #7 explainability | Researchers in active project crunch want zero exploration; this needs to be a Stage-1 mode toggle, not a constant. Ship with κ=0 until UI supports the choice. |
| **Method signature from abstract** | #5 method signature mismatch | Psychophysics methods live in full text, not abstracts. Abstract-only `s_meth` will be near-zero for the right papers + spurious matches for the wrong ones. Skipping `s_meth` entirely until we have full-text extraction. |
| **Citation graph (full proposal)** | #4 citation graph coverage | OpenAlex `referenced_works` undercovers JoV/Vision Research/psychophysics venues; `s_cit` would be ~0 for papers that matter most. Ship without `s_cit`; revisit when we have multi-source citation expansion (Semantic Scholar + Crossref). |
| **Beta-Bernoulli posterior + Bayesian uncertainty** | #3 starvation | ~12 obs/tag/year per researcher is below the threshold where Beta-Bernoulli outperforms the existing +0.2/−0.3 heuristic. Keep the deterministic belief-updater rubric. |
| **Researcher-side feedback loop tightening** | #8 | Same root cause as Thompson cut. Real-time updates require allowlist expansion + new tables. Defer. |

## What we cannot ship until something else changes

**Non-circular success metric (codex finding #9 + #1, root cause).** No
current data source can ground a non-tautological "this recommendation
helped the researcher's science" judgment. The lab needs to:

1. Add a quarterly retrospective survey hook (3 questions per
   researcher, ≤ 5 min): "of papers recommended in the last 3 months,
   which did you (a) save, (b) read in full, (c) cite or plan to cite,
   (d) discuss with collaborators?"
2. Wire this into a new `archive_outcome_signals` table (operator-only
   writes; researchers complete the survey via the SAME plugin
   interview at a special slash command `/csnl-paper-archive-interview:
   retrospective`).
3. Six months of survey data becomes the non-circular ground truth for
   re-running the codex-blocked NDCG calibration.

This is P20+ work, not P19. Flag in operator-facing notes.

**Method signature with full-text grounding (codex #5).** Requires
either (a) the parent harness's `pipeline/crawl.mjs` reachable from
operator pipeline (true today) + a per-paper methods-section extractor
ALSO operator-run (new), OR (b) accepting a narrow local-only
SPECTER2/SciBERT exception per codex finding #10. Defer to P20.

**Multi-source citation expansion (codex #4).** Add Semantic Scholar
+ Crossref reference fetch alongside OpenAlex. Cache same way as
OpenAlex. P20 work.

## Hard constraints (still hold)

- No LLM API key in any unattended path. The fingerprint extraction is
  rule-based (lexicon + regex + IDF lookup).
- No new tables written by the plugin. Voice B's `dim_posterior`
  extension was scoped into the existing `dim_preferences` JSONB; we
  cut it anyway, so no schema change for the posterior.
- Operator-side scripts only; researchers never run fingerprint /
  IDF / validation tooling.
- Korean researcher-facing text. English scientific terms preserved
  per the P15 rule. Internal codes (`F-EFC`, `s_kw_bm25`, tier raw
  scores) never visible to the researcher.

## Open follow-ups (P20 candidate list)

1. Retrospective-survey ground truth (codex #9 blocker).
2. Full-text method-section extraction (codex #5).
3. Multi-source citation graph (codex #4).
4. Hierarchical Bayesian posterior — pooling tags across researchers
   to escape the starvation regime (codex #3 mitigation).
5. Per-researcher exploration mode (Stage-1 UI: `focus`/`balanced`/
   `explore`; codex #6).
6. Re-running CWLL NDCG with the codex-corrected tier setup once
   non-circular labels exist.

## Change log

| Date | Change |
| --- | --- |
| 2026-05-26 | P19a: fingerprint extraction (Pass-A lexicon only) + BM25 keyword signal + explain-WHY + MCQ-precision view. 7 cuts documented above. |
