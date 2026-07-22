---
name: dedup-feedback-rule
description: Never re-recommend a paper. Dedup checks ledger paper_recommendations + paper_recommendations_read + reading-DB + exclusion_rules. Feedback loop: reply → classify → update exclusions → re-run. Carry-over rank 2–5 logic.
source: docs/DECISIONS-2026-05-18.md (in this repo) + v1 predecessor artifacts NOT in this repo (feedback_dm_feedback_loop.md, BUILD_SPEC.md §schema.sql, BUILD_SPEC.md §04_dedup.py)
---

## Never re-recommend

A paper is permanently excluded for a unit if its **normalized DOI** (or >90% fuzzy title
match) appears in any of the following ledger tables:

| Table | Covers |
|---|---|
| `paper_recommendations` | Any prior run, any member of the unit |
| `paper_recommendations_read` | Papers the researcher has marked as already read |
| `exclusion_rules` (unit_id match) | Topics/papers explicitly rejected via feedback |

Normalization: `pipeline/_util.py:doi_normalize` strips `https://doi.org/`, lowercases,
trims whitespace. Title fuzzy match threshold: ≥ 0.9 ratio (`fuzzy_title_eq`).

Source: `docs/DECISIONS-2026-05-18.md` — "Never re-recommend — dedup vs ledger
`paper_recommendations` + `paper_recommendations_read` + reading-DB + `exclusion_rules`."

## Reading-DB port

The legacy reading-DB (from the v1 `csnl-paper-scout` predecessor) exists in this repo
**only as a committed snapshot**: `config/reading_db_snapshot.json`, shape
`{"_provenance": ..., "entries": [{"doi", "title"}, ...]}`, 1,069 entries. There is no port
or refresh script here — v1's `scripts/port_assets.sh` was never carried over — so the
snapshot is frozen; refreshing it means re-deriving it from the predecessor repo by hand.

It is **not** loaded into `paper_recommendations_read`. `scripts/init_db.py` only applies
schema/DDL and touches no data rows; `paper_recommendations_read` is populated by
`scripts/migrate_legacy_ledger.py` (one-off predecessor-sqlite import).

So the reading-DB file dependency is **live, not retired**: `scripts/dedup_snapshot.py`
reads `config/reading_db_snapshot.json` on every run and merges it into
`state/runs/<RID>/_dedup_snapshot.json` under `reading_db`, alongside the three ledger
tables above. Scouts consume it through their brief's `dedup_terms`
(`.claude/agents/pg-interest-reader.md` step 4) and never query the database themselves.
(v1's `04_dedup.py`, which this section used to say "queries only the ledger", was never
written.)

## Feedback loop

When a researcher replies to a channel post with rejection feedback:

1. **Classify** — operator manually marks the `feedback_events.signal` field:
   - `thumbs_down` — explicit rejection
   - `already_read` — not new to the researcher
   - `thinking` / `thread_reply` — neutral / informational

2. **Update exclusions** — for `thumbs_down` or `already_read`:
   - Insert row into `exclusion_rules(unit_id, member_init, excluded_term, reason,
     declared_at, source)` where `excluded_term` is the rejected paper DOI or a
     rejected topic keyword extracted from the reply.
   - `source` = `"feedback"`, `declared_at` = ISO timestamp of the reply.

3. **Drop carry-over** — if any rank 2–5 carry-over paper matches a newly added
   `excluded_term`, drop it from the next run's carry-over pool.

4. **Re-run** — trigger a new discovery + scoring pass for the affected unit. Do not
   surface the rejected paper or any paper matching the rejected topic keyword.

Source: `feedback_dm_feedback_loop.md` — "update `member_uncertainty.json` to record what
topics the researcher rejects + re-run search with new keyword set, drop the rejected paper
from carry-over."

Note: automated intent classification is deferred for the validation phase. The operator
manually sets the `signal` field.

## Carry-over: rank 2–5

Papers ranked 2 through 5 in the scout's composite-sorted candidate list are eligible for
carry-over. In v2 that list is `state/runs/<RID>/scout_<unit>.json` → `candidates[]` (the
scout keeps ≥3 so the operator has a swap choice); the v1 `06_scored.json` this section used
to name is not produced by this harness.

- If the top paper (rank 1) was successfully delivered in the current run, ranks 2–5 carry
  forward as the first candidates for the next run's discovery set.
- Carry-over papers must re-pass the date filter (`rules/02_date_filters.md`) at the time
  of the next run. A paper that was within the strict window last run may fall outside it
  next run — it is then dropped.
- Carry-over papers that match any `exclusion_rules.excluded_term` for the unit are
  dropped immediately.
- Carry-over as **persisted state is spec-only**: no code in this repo writes
  `paper_recommendations(tier="carryover")` rows. What actually happens is that the prior
  run's `scout_<unit>.json` stays on disk and the orchestrator starts a follow-up run from
  it (`.claude/skills/paper-rec-orchestrator/SKILL.md`, Phase 0 — "new run off carryover").
  If ledger-persisted carry-over is ever needed, it has to be built.

Source: `feedback_paper_rec_tone.md` (template) — "차순위 후보(2~5위) 4편은 다음 주
후보로 자동 이월됩니다."
Source: BUILD_SPEC.md §06_scored.json — "{top:{...}, carryover:[2..5]}"

## `exclusion_rules` schema (reference)

```sql
CREATE TABLE exclusion_rules(
  unit_id TEXT, member_init TEXT, excluded_term TEXT, reason TEXT,
  declared_at TEXT, source TEXT, UNIQUE(unit_id, excluded_term));
```

`excluded_term` can be:
- A normalized DOI (`10.xxxx/...`)
- A topic keyword string (e.g., `"fMRI decoding"`) — matched against future keyword sets

Topic-keyword exclusions are applied in `pipeline/01_extract_topics.py` — keywords that
match an `excluded_term` for the unit are removed from the keyword bundle before discovery.

--- end of 04_dedup_feedback.md
