---
name: date-filter-rule
description: Hard publication-date gates for paper candidates. Strict tier first; relaxed tier only after ≥3 strict queries return zero topical match. Beyond relaxed = reject. Output must declare tier.
source: docs/DECISIONS-2026-05-18.md (in this repo) + v1 predecessor artifacts NOT in this repo (feedback_paper_rec_date_rules.md, BUILD_SPEC.md) — the "Source:" quotes below cite those predecessor files
---

## Reference date

`today` = the KST (Asia/Seoul, UTC+9) calendar date on which the run's discovery step
executes, in ISO form.

**What actually computes it (live path):**

| Site | Role |
|---|---|
| `pipeline/_util.py` → `kst_now()` | the clock. `run_id_now()` is `kst_now().strftime("%Y%m%d-%H%M")`, so `today` is also the date prefix of the run's `<RID>`. |
| `pipeline/_util.py` → `within_window(date_iso, is_preprint, tier)` | the reference implementation of the tier tables below (its `_WINDOWS` dict *is* those tables). |
| `.claude/skills/paper-rec-scout/SKILL.md` §Inputs | where the derivation actually happens now: the `unit-scout` computes `since_journal = today − 365 d` and `since_preprint = today − 90 d` when it builds its crawl calls. |
| `pipeline/crawl.mjs` → `withinWindow()`, driven by `search --since-journal / --since-preprint` | enforces the window at crawl time: merged results are filtered before emit. |

Three facts about that code, stated so nobody has to rediscover them:

- `within_window()` compares against `datetime.utcnow()`, not KST. The ≤9 h skew is
  immaterial at 90/180/365/730-day granularity, but the **KST** date is the one of record —
  it is what `<RID>` and the ledger row carry.
- **Nothing stamps a window into the unit briefs.** `_scout_briefs.json` /
  `brief_<unit>.json` are emitted by `pg-interest-reader`
  (`.claude/agents/pg-interest-reader.md`), whose brief schema has no `window` field. A
  deterministic helper used to add one — with hard-coded `2026-05-19` literals rather than
  `kst_now()` — and it has since been retired to `scripts/_legacy/build_scout_briefs.py`.
  If a brief you are handed does carry a `window`, treat it as stale unless `window.today`
  equals the current run date. This file is authoritative either way.
- The windows live in two languages: `_WINDOWS` in `pipeline/_util.py` and `withinWindow()`
  in `pipeline/crawl.mjs`. Change one, change both (see §Code hook).

> Historical: v1 anchored this rule to "the date `pipeline/02_discover.py` executes". No
> such script was ever written in this repo. v2 realises discovery as the `unit-scout`
> fan-out — see `.claude/skills/paper-rec-scout/SKILL.md` and `docs/HARNESS-DESIGN-v2.md`.

## Tier definitions

### Strict (default — always try first)

| Source type | Window |
|---|---|
| Peer-reviewed journal | `today − 365 days` ≤ `publication_date` ≤ `today` |
| Preprint (bioRxiv, arXiv, OSF, etc.) | `today − 90 days` ≤ `posted_date` ≤ `today` |

### Relaxed (fallback — conditional, see below)

| Source type | Window |
|---|---|
| Peer-reviewed journal | `today − 730 days` ≤ `publication_date` ≤ `today` |
| Preprint | `today − 180 days` ≤ `posted_date` ≤ `today` |

### Beyond relaxed = reject

Any paper outside the relaxed window is **dropped regardless of topical fit**. Better no
recommendation than a stale one.

Source: `feedback_paper_rec_date_rules.md` — "Anything beyond relaxed (>2y journal, >6m
preprint) is rejected outright — better to send no recommendation than a stale one."

## Escalation rule

Relaxed tier is allowed **only** when:

1. At least **3 distinct strict-tier queries** have been executed for this unit in the
   current run, **and**
2. All 3 returned **zero candidates that pass both topic AND date** (strict window).

If these conditions are not met, strict must be used. No escalation on the first query.

Source: `docs/DECISIONS-2026-05-18.md` — "Relaxed (journal ≤ 2 y, preprint ≤ 6 m) ONLY
after ≥3 strict queries returned zero topical match."

A specific failure mode that prompted this rule (`feedback_paper_rec_date_rules.md`):
Pourmohammadi et al. 2025-12-13 bioRxiv was posted 5 months before the run date and was
incorrectly emitted. The date filter must be applied at the candidate-filter step, not only
at query-construction time.

## How to classify preprint vs journal

- OpenAlex `primary_location.source.type == "repository"` → preprint
- DOI prefix `10.1101` (bioRxiv) or `10.48550` (arXiv) → preprint
- OSF DOI or no venue → preprint
- Everything else → journal (apply sanity check: venue name expected)

If classification is ambiguous, treat as preprint (more conservative window).

## Output requirement

Every candidate a scout emits — in v2 that is each object in `candidates[]` plus `top` of
`state/runs/<RID>/scout_<unit>.json` — **must** carry:

```json
{
  "date": "2025-11-20",
  "is_preprint": false,
  "tier": "strict"
}
```

`tier` must be `"strict"` or `"relaxed"`. A candidate carrying no `tier` is invalid and the
**scout must drop it before writing its output file** — nothing downstream will catch it.

> Historical: this line used to say such candidates are "dropped by `pipeline/04_dedup.py`".
> That script was never written. The v2 dedup step is the orchestrator's Phase-4 check
> against the unit's `dedup_terms` (from `scripts/dedup_snapshot.py`), which matches on
> normalized DOI / fuzzy title and never inspects `tier`.

Live propagation of `tier`: scout `top.tier` → `07_drafts.json.tier`
(`.claude/skills/paper-rec-draft/SKILL.md`) → `scripts/deliver.py` →
`paper_recommendations.tier` (`state/schema.sql`) and the `recommendation_messages` meta
JSON. `scripts/deliver.py` requires the field and fails the run without it. The channel post
does not expose the tier to the researcher; it is for operator review only (visible in the
ledger row). The v1 intermediates `04_verified.json` / `05_deduped.json` / `06_scored.json`
are **not** produced by the v2 harness.

## Verification step

**Historical — no live equivalent.** v1 specified a `pipeline/03_verify.py` that re-resolved
each DOI through Crossref and dropped the candidate if the Crossref-resolved date fell
outside the tier window. That script was never written, and v2 has no separate verification
stage. Crossref is not called anywhere on this path: `pipeline/crawl.mjs` merges OpenAlex,
Europe PMC, arXiv and Semantic Scholar only, and the sole Crossref caller in the repo is a
*discovery* adapter in `scripts/archive/fetch_new_papers.py` (P33, different pipeline).

What stands in for it today: a candidate's date is the one its discovery source reported
through `pipeline/crawl.mjs` (OpenAlex `from_publication_date`, Europe PMC `FIRST_PDATE`,
arXiv / bioRxiv posted date), re-filtered client-side by `withinWindow()` before emit; and
the scout must have fetched and read the full text (`crawl.mjs fulltext`) and recorded a
verbatim quote, which is what establishes that the paper is the paper. **Do not assume a
date is Crossref-authoritative.** If a run needs that guarantee, the resolution step has to
be built first.

## Code hook

`pipeline/_util.py` exports the reference implementation:

```python
def within_window(date_iso: str, is_preprint: bool, tier: str) -> bool:
    """Return True if date_iso falls within the window for the given tier."""
```

Any Python stage that filters by date must call this rather than reimplementing it. Note
that the crawl path is JavaScript and carries a **parallel** implementation —
`withinWindow()` in `pipeline/crawl.mjs` — so a change to the windows above must be applied
to both `_WINDOWS` in `pipeline/_util.py` and `pipeline/crawl.mjs`, or the two will drift.

--- end of 02_date_filters.md
