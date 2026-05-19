---
name: paper-rec-scout
description: >
  Per-unit discovery + full-text review procedure for unit-scout agents.
  Formulate domain queries from real project fields, search keyless APIs via
  pipeline/crawl.mjs, fetch and READ full text, score D1–D5 grounded in
  quoted full text, loop until ≥3 in-window non-duplicate relevant
  candidates. Use whenever scouting/reviewing papers for a researcher unit;
  also on re-scout ("broaden", "다시 탐색", "후보 더 찾아").
---

# paper-rec-scout — discovery + full-text review (one unit)

Keyless keyword-API discovery alone is a **known failure mode** (1/6 units).
The validated method is an Opus scout that reads full text (6/6). Read
`pipeline/score/SKILL.md` (D1–D5 rubric + anti-hallucination) and
`pipeline/scan/SKILL.md` before scoring. Date law: `rules/02_date_filters.md`.
Dedup law: `rules/04_dedup_feedback.md`. Grounding law: `rules/03_grounding.md`.

## Inputs

Your unit brief (in the prompt): `unit_id, members, project_slugs,
keywords, anchor_dois, gist, projects:[full csnl_research rows],
dedup_terms:[normalized DOIs + titles]`. Reference dates: `today` (KST);
strict `since_journal = today−365d`, `since_preprint = today−90d`.

## Step 1 — formulate domain queries (not generic words)

Build 4–8 queries from the unit's *actual* fields:
- `purpose.research_question` / `hypothesis` phrases (the phenomenon).
- named `manipulation_variables.independent_vars` / `dependent_vars`
  (e.g. an exact paradigm parameter, stimulus manipulation).
- `connected_graph.shared_paradigm_with` entries.
- author surnames + topic from `background.prior_studies[].doi` (resolve the
  DOI's lineage; chase the *method*, not just the words).
Each query targets a specific mechanism/method, e.g.
`"serial dependence" orientation estimation stimulus duration repulsive`
— not `"visual perception"`.

## Step 2 — search (crawl.mjs)

```
node pipeline/crawl.mjs search --query "<q>" \
  --since-journal <YYYY-MM-DD> --since-preprint <YYYY-MM-DD> --limit 30
```
Round-robin merge across OpenAlex (title+abstract relevance) / Europe PMC /
arXiv / Semantic Scholar; window-filtered. Treat ≥3 distinct strict queries
with zero topical+in-window hit as the escalation precondition (see Step 6).

## Step 3 — fetch + READ full text (mandatory)

For each promising hit:
```
node pipeline/crawl.mjs fulltext --doi <doi> [--url <landing>] [--pdf <pdf>]
```
Modes: `epmc_xml` (Europe PMC OA) → Playwright `html`/`pdf` (pdfjs). **Read
the returned text** — methods, manipulations, key results. Abstract-only is
NOT a review. If `unavailable`, try the alternate URL/PDF; if still nothing,
the paper may be scored only from an OA abstract with a concrete quote, with
`fulltext_mode:"abstract"` and composite capped at 6.

## Step 4 — score D1–D5 (max composite; grounded; anti-hallucination)

Per candidate × each unit member, score D1 Direct Advance, D2 Hypothesis
Tension, D3 Methodological Import, D4 Competitive Signal, D5 Reframing Power
(0–10). `member_score = max(D1..D5)`; `composite = max over members`. Rules
(from `pipeline/score/SKILL.md`):
- Score only what the text states; no "probably discusses…".
- **Any dimension ≥7 requires a verbatim quote** from the fetched full text.
- Irrelevant dimension = 0 (no 3–4 padding); no double-counting across dims.
- D2: name which member, which hypothesis, support vs challenge.
Record `grounding` = the *named* project element it maps to (slug + field +
value) and `quote` = the verbatim full-text sentence.

## Step 5 — dedup

Drop any candidate whose `doi_normalize` OR ≥0.9 fuzzy title matches the
unit's `dedup_terms` (ledger recommendations + read + reading-DB +
exclusion_rules). Never re-recommend. Also drop exclusion-keyword topic hits.

## Step 6 — loop / escalation

Goal: **≥3 in-window, non-duplicate, genuinely-relevant candidates**,
composite-ranked, top ≥7. If short:
1. Reformulate (synonyms, method terms, anchor-DOI co-authors, adjacent
   paradigm) and re-crawl — still strict window.
2. Only after **≥3 distinct strict queries each returned zero
   topical+in-window hit**: relax to journal ≤730 d / preprint ≤180 d, tag
   those `tier:"relaxed"`. Beyond relaxed = reject regardless of fit.
3. Cap ~6–8 rounds. If still <3: return what you have with
   `reason:"insufficient_grounded_candidates"`. No padding, no laundering a
   weak fit into a 7 — no recommendation beats a bad one.

## Output

`state/runs/<RID>/scout_<unit_id>.json`:
```json
{ "unit_id": "...", "rounds": N, "queries_tried": ["..."],
  "candidates": [ { "doi","title","authors","venue","date",
    "is_preprint","tier","source","fulltext_mode","fulltext_chars",
    "D1":..,"D2":..,"D3":..,"D4":..,"D5":..,"composite":..,"best_dim":"D1",
    "best_member":"BYL","grounding":"<slug>.<field>=<value> …",
    "quote":"<verbatim full-text sentence>" } ],
  "top": { ...best candidate object... },
  "reason": null }
```
Rank `candidates` by composite desc; `top` = rank 1 (null if max<7). Keep
≥3 for the operator's swap choice. Return a short summary; keep all
internal labels (D-scores, tier, composite) inside the JSON only.
