---
name: paper-rec-scan
description: >
  Phase 3 of csnl-paper-rec pipeline: multi-source discovery driven by
  per-unit topic bundles from 02_topic_bundles.json. Query six keyless REST
  APIs using unit keywords + anchor DOIs; apply strict date window; escalate
  to relaxed only after ≥3 strict-query failures. Output 03_candidates.json.
  Triggers on: 'scan papers', 'discover candidates', 'paper rec scan',
  '논문 검색', '후보 검색', 'find candidates'. Use after 02_topic_bundles.json
  is in state/runs/<RUN_ID>/.
---

# csnl-paper-rec — Stage 3: Multi-Source Discovery

Find recent papers inside each unit's topic perimeter by querying six
keyless public APIs. Unlike the RAG-anchored approach of csnl-paper-scout,
topic queries here are driven entirely by `02_topic_bundles.json` — the
per-unit keyword lists + anchor DOIs extracted from `csnl_research.projects`.
No Zotero, no embedding gate, no OpenRouter key required.

## Input

`state/runs/<RUN_ID>/02_topic_bundles.json` — output of `pipeline/01_extract_topics.py`.

Shape per unit:
```json
{
  "unit_id": "JOP",
  "members": ["JOP"],
  "display_names": ["박준오"],
  "channel_ids": ["C0B3FTHAVR8"],
  "dm_inits": ["JOP"],
  "project_slugs": ["RingRepSca", "Time"],
  "keywords": ["serial dependence", "estimation bias", "working memory", ...],
  "anchor_dois": ["10.1016/j.neuron.2025.01.xxx", ...],
  "gist": "≤60-word extractive synthesis of unit projects"
}
```

## Output

`state/runs/<RUN_ID>/03_candidates.json`

```json
{
  "run_id": "20260518-1400",
  "generated_at": "2026-05-18T14:00:00+09:00",
  "units": [
    {
      "unit_id": "JOP",
      "candidates": [
        {
          "doi": "10.xxxx/xxxxx",
          "title": "...",
          "authors": ["Last F", "Last2 F2"],
          "venue": "Nature Neuroscience",
          "date": "2026-04-10",
          "abstract": "...",
          "source": "semantic_scholar",
          "is_preprint": false,
          "tier": "strict"
        }
      ]
    }
  ]
}
```

Cap: **40 candidates per unit** before dedup. Each candidate must carry `tier`
(`"strict"` or `"relaxed"`). Never mix tiers silently — a unit that escalated
must have `tier:"relaxed"` on every relaxed-window result.

## Date Windows (from `rules/02_date_filters.md`)

| Tier | Journal | Preprint |
|------|---------|----------|
| strict | ≤ 365 days | ≤ 90 days |
| relaxed | ≤ 730 days | ≤ 180 days |

Escalation rule: attempt ≥ 3 independent strict queries per unit (vary
keywords/API). Escalate to relaxed ONLY if all three return zero topical
hits (not just zero results — a hit = a result with abstract overlap with
the unit's keywords). Tag escalated results `"tier": "relaxed"`. Beyond
relaxed = reject entirely (never include).

## APIs (keyless, public endpoints)

All six are hit per unit. Use `requests` with a 20 s timeout. Accept
`User-Agent: csnl-paper-rec/1.0 (research use)`.

### 1 — OpenAlex (`https://api.openalex.org`)

Keyword search on unit keywords (pick top-5 most discriminating):
```
GET https://api.openalex.org/works
  ?search=<keyword1> <keyword2> <keyword3>
  &filter=from_publication_date:<YYYY-MM-DD>,type:article
  &sort=publication_date:desc
  &per-page=20
  &mailto=csnl@snu.ac.kr
```
Use `filter=from_publication_date:<window_start>` for strict; expand to
relaxed start if escalating. Extract: `doi`, `title`, `authorships[].author.display_name`,
`primary_location.source.display_name`, `publication_date`, `abstract_inverted_index`
(reconstruct abstract from inverted index — see helper note below).

**Inverted-index reconstruction**: OpenAlex stores abstracts as
`{word: [positions]}`. Reconstruct with:
```python
words = abstract_inverted_index  # dict[str, list[int]]
flat = sorted(((pos, w) for w, ps in words.items() for pos in ps))
abstract = " ".join(w for _, w in flat)
```

### 2 — Crossref (`https://api.crossref.org`)

Use `/works` with `query.bibliographic` for title-style queries:
```
GET https://api.crossref.org/works
  ?query.bibliographic=<gist text, first 10 words>
  &filter=from-pub-date:<YYYY-MM-DD>
  &rows=20
  &mailto=csnl@snu.ac.kr
```
Extract: `DOI`, `title[0]`, `author[].given+family`, `container-title[0]`,
`published.date-parts[0]`, `abstract` (strip JATS XML tags if present).

### 3 — Semantic Scholar (`https://api.semanticscholar.org`)

Relevance search:
```
GET https://api.semanticscholar.org/graph/v1/paper/search
  ?query=<keywords joined with spaces>
  &fields=paperId,externalIds,title,authors,venue,publicationDate,abstract,isOpenAccess
  &limit=20
```
Filter `publicationDate >= window_start` client-side (API doesn't accept
date filter in keyless mode). Extract DOI from `externalIds.DOI`.
`is_preprint`: venue contains "arXiv" or "bioRxiv" or `isOpenAccess` and
venue is blank.

### 4 — PubMed E-utilities (`https://eutils.ncbi.nlm.nih.gov`)

Two-step: ESearch → EFetch.

```
# Step 1: search IDs
GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi
  ?db=pubmed
  &term=<keyword1>[tiab]+AND+<keyword2>[tiab]
  &datetype=pdat
  &mindate=<YYYY/MM/DD>
  &maxdate=<YYYY/MM/DD>
  &retmax=20
  &retmode=json

# Step 2: fetch details
GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi
  ?db=pubmed
  &id=<comma-separated PMIDs>
  &retmode=xml
  &rettype=abstract
```

Parse XML: `ArticleTitle`, `AbstractText`, `AuthorList`, `Journal/Title`,
`PubDate`, `ArticleId[IdType=doi]`.

### 5 — arXiv (`https://export.arxiv.org`)

```
GET https://export.arxiv.org/api/query
  ?search_query=ti:<keyword1>+AND+abs:<keyword2>
  &start=0
  &max_results=20
  &sortBy=submittedDate&sortOrder=descending
```
Parse Atom XML: `<entry>` elements → `<title>`, `<summary>` (abstract),
`<author><name>`, `<published>`, `<link href rel="related">` (DOI if present),
`id` (arXiv URL; use as pseudo-DOI `arxiv:<id>`).
`is_preprint=True` always for arXiv. Apply strict preprint window (90 d).

### 6 — bioRxiv/medRxiv (`https://api.biorxiv.org`)

```
GET https://api.biorxiv.org/details/biorxiv/<YYYY-MM-DD>/<YYYY-MM-DD>/0/json
```
Filter client-side on `category` and abstract keyword overlap with unit
keywords. Cap at 20 per call. `is_preprint=True` always. Apply strict 90 d
preprint window. DOI from `doi` field (already normalized).

## Execution Walkthrough

For each unit in `02_topic_bundles.json`:

### Step 0: Derive date windows

```python
from pipeline._util import kst_now, within_window
today = kst_now().date()
strict_journal_start  = today - timedelta(days=365)
strict_preprint_start = today - timedelta(days=90)
relaxed_journal_start  = today - timedelta(days=730)
relaxed_preprint_start = today - timedelta(days=180)
```

### Step 1: Strict discovery (first pass — all six APIs)

Issue 3 distinct keyword combinations per API (rotate unit.keywords, pick
top-5 most specific per query). Total = 18 strict queries per unit. Track
whether any query returns a result whose abstract has >0 token overlap with
unit.keywords. Count "topical hits" across all 18 queries.

### Step 2: Escalate if needed

If topical_hits == 0 after all 18 strict queries → repeat Steps 1 with
relaxed windows, tag all results `"tier": "relaxed"`. If relaxed also yields
zero topical hits → log `"no_candidates_reason": "zero_topical_hits_relaxed"`
for the unit and emit an empty list.

### Step 3: Anchor-DOI lookup

For each `doi` in `unit.anchor_dois`, fetch citing works via Semantic Scholar:
```
GET https://api.semanticscholar.org/graph/v1/paper/DOI:<doi>/citations
  ?fields=paperId,externalIds,title,authors,venue,publicationDate,abstract
  &limit=20
```
Apply window filter client-side. Add any new DOIs not already in the
candidate pool.

### Step 4: Deduplicate within the batch

Normalize all DOIs via `pipeline._util.doi_normalize`. If two results share
the same normalized DOI or `fuzzy_title_eq` → keep the one with the longer
abstract; discard the other.

### Step 5: Cap and emit

Sort remaining candidates by date descending; cap at 40. Emit into
`03_candidates.json` per the output shape above.

## Sanity Checks Before Handing Off

1. Every candidate has `doi` (non-empty), `title`, `abstract`, `date`, `tier`.
2. No candidate's `date` falls outside the tier's allowed window.
3. Total candidates across all units printed to console for operator review.
4. Candidates per unit printed: unit_id, count, tier breakdown.

Example console summary:
```
Stage 3 complete — 03_candidates.json written
  JOP      : 23 candidates (strict: 23, relaxed: 0)
  BYL      : 14 candidates (strict: 14, relaxed: 0)
  MSY      : 31 candidates (strict: 18, relaxed: 13)  ← escalated
  SMJ      : 8 candidates  (strict: 8, relaxed: 0)
  JYK      : 19 candidates (strict: 19, relaxed: 0)
  SYJ+BHL  : 27 candidates (strict: 27, relaxed: 0)
Total: 122 candidates → pipeline/04_dedup.py next
```

## Handoff

Pass `state/runs/<RUN_ID>/03_candidates.json` to `pipeline/03_verify.py`
(Stage 4 — Crossref title/DOI ground-truth verification) via `scripts/run_manual.py`.
