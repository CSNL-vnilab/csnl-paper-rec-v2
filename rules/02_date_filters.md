---
name: date-filter-rule
description: Hard publication-date gates for paper candidates. Strict tier first; relaxed tier only after ≥3 strict queries return zero topical match. Beyond relaxed = reject. Output must declare tier.
source: feedback_paper_rec_date_rules.md + docs/DECISIONS-2026-05-18.md + BUILD_SPEC.md
---

## Reference date

`today` = the date `pipeline/02_discover.py` executes (ISO, KST).

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

Every candidate in `03_candidates.json` **must** carry:

```json
{
  "date": "2025-11-20",
  "is_preprint": false,
  "tier": "strict"
}
```

`tier` must be `"strict"` or `"relaxed"`. Candidates without a `tier` field are treated as
invalid and dropped by `pipeline/04_dedup.py`.

The `tier` field propagates forward unchanged through `04_verified.json`, `05_deduped.json`,
`06_scored.json`, and `07_drafts.json`. The channel post does not expose the tier to the
researcher; it is for operator review only (visible in the ledger row).

## Verification step

`pipeline/03_verify.py` re-checks the date after DOI resolution via Crossref. If the
Crossref-resolved date is outside the tier window, the candidate is dropped even if the
discovery source reported a passing date. Crossref date is authoritative.

## Code hook

`pipeline/_util.py` exports:

```python
def within_window(date_iso: str, is_preprint: bool, tier: str) -> bool:
    """Return True if date_iso falls within the window for the given tier."""
```

All pipeline stages call this function rather than reimplementing the logic.

--- end of 02_date_filters.md
