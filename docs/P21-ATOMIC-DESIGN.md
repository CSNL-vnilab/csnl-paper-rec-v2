# P21 — atomic design: dedup hardening + APA renderer + multi-lens synopsis

**Date:** 2026-05-27
**Status:** design draft → awaiting codex adversarial review → implement
**Origin:** user feedback (this session) — three concrete defects in the
researcher-facing paper introduction that block the interview from being
useful:

1. **Duplicate paper re-issued.** The same paper (different fuzz variant
   or preprint/published pair) gets surfaced after the researcher already
   answered it.
2. **APA mangled to "2025 OO외".** Citation gets summarised into Korean
   abbreviation instead of being rendered verbatim in APA 7. Researcher
   cannot identify the paper at a glance.
3. **Shallow single-model rationale.** "Behavioural modeling 와 겹칩니다"
   tier-template; even the P19f Block 2 anchored to one project × one
   model is too thin. A recommendation must engage multiple theoretical
   lenses (efficient coding / normative Bayesian / behavioural / neural)
   with their premise → prediction → observation → interpretation chains.

This document is **atomic** — each defect has its own self-contained
section with: root cause, fix, contract, failure modes, test matrix.
The three sections do not depend on each other in implementation order;
they can be shipped independently.

---

## §A — Duplicate paper hardening

### A.1 Empirical evidence (live state, 2026-05-27)

JOP queue ∩ JOP responses shows **19 already-answered canonical_ids still
present in the queue rows**. Pick_next's `NOT EXISTS` clause filters
those out at issue time, so they are not the failure. The failure is
**different canonical_ids for the same semantic paper**. Confirmed
escape cases:

| Answered (cid) | In queue (cid) | Cause |
| --- | --- | --- |
| "The geometry of efficient codes:" 2025 | "The geometry of efficient codes" 2025 | punctuation diff (colon) |
| "Model Sharing in the Human MTL" 2022 (preprint) | "Model sharing in the human medial temporal lobe 80a86f" 2021 (preprint) | year + case + trailing hash suffix |
| "Fast efficient coding…" 2025 (preprint) | "Fast efficient coding and sensory" 2026 (published) | preprint→published version |
| "Distinct neural representational geometries…" 2025 | "Distinct neural representational geometries…" 2025 (different cid) | hash suffix in title, same year |
| "Incorporation of a Cost of Deliberation Time…" 2025 (preprint) | "Incorporation of a cost of deliberation time… c5f60" 2024 | year + case + hash suffix |

### A.2 Root cause

`scripts/archive/merge_dedupe_filter.py` uses `title_norm` (lowercased
alnum-only) and a 0.88 RapidFuzz threshold. But:

- **Hash suffix leakage**: ingest_classics.py derives title from
  filename. Lab convention appends `_<6hex>` or ` <6hex>` to disambiguate
  files. `title_norm` strips non-alnum, so " c5f60" → "c5f60" which
  survives. The fuzz match now sees `"…decisionmakingc5f60"` vs
  `"…decisionmaking39473"` — different 5-char tails. RapidFuzz partial
  ratio drops below 0.88.
- **Year-window false negative**: fuzz collapse only considers candidates
  within ±1 year. A 2025 preprint → 2026 published is +1 OK; but a 2022
  preprint → 2024 published (NeurIPS 2 years out) misses.
- **DOI-prefix not used as a fast-collapse key**: archive_papers.doi is
  often present on the published version but null on the
  filename-derived preprint, so the two never share a DOI to collapse on.

### A.3 Fix — five additive collapses

1. **Stronger title_norm** (`scripts/archive/_common.py`):
   - Strip trailing token of length ≤ 8 consisting only of hex digits
     ("c5f60", "39473", "80a86f", "6630a7").
   - Strip leading/trailing punctuation classes including ASCII ":" "—" "–".
   - NFKC unicode normalize before stripping.
   - Strip "preprint" / "manuscript" / "draft" suffix tokens.

2. **Cross-year fuzz with widened window** in
   `merge_dedupe_filter.py`:
   - Window ±2 years (was ±1).
   - When years differ by exactly 1 AND one is_preprint=true, treat as
     same paper (preprint→published collision pattern).
   - Threshold stays at 0.88 partial_ratio, but only computed on the
     hash-stripped normalized title.

3. **Cluster-pick canonical**: when collapsing N≥2 rows, pick the row
   with (is_preprint=False, year>=other, longer authors_json) as
   canonical; merge the others' archive_paper_sources rows onto it; keep
   the loser canonical_ids alive as redirect entries
   (`archive_canonical_redirects` — new table).

4. **`archive_canonical_redirects` table** (new):
   ```sql
   archive_canonical_redirects(
     from_canonical_id  TEXT PRIMARY KEY,
     to_canonical_id    TEXT NOT NULL,
     reason             TEXT,    -- 'fuzz_collapse','preprint_published',…
     collapsed_at       TEXT NOT NULL
   )
   ```
   Loser cids redirect to the winner. archive_responses.canonical_id
   is migrated; archive_researcher_queues.canonical_id is migrated.

5. **`pick_next.py` Python-side safety net**: after the SQL `NOT EXISTS`
   filter, additionally compute a hash-stripped normalized title for the
   candidate and reject if any of the researcher's `archive_responses`
   rows share that normalized title (regardless of canonical_id).
   This catches the case where merge_dedupe missed but the candidate is
   semantically a duplicate.

### A.4 Contract

- After A.3.1+A.3.2+A.3.3 ship, re-running `merge_dedupe_filter.py
  --apply` produces ≤ 1 duplicate cluster larger than 1 (false negative
  rate ≤ 0.01 % of corpus). Verified by a sweep query at end of run.
- After A.3.4 ships, `archive_responses.canonical_id` and
  `archive_researcher_queues.canonical_id` never reference a
  `from_canonical_id` (verified by a sweep + idempotent migrate script).
- A.3.5 is belt-and-suspenders, always on, regardless of A.3.1–A.3.4
  status.

### A.5 Failure modes / what could go wrong

| Failure | Mitigation |
| --- | --- |
| Hash-suffix strip eats legitimate ≤ 8 hex content (e.g. SARS-CoV-2 strain name "BA.5") | Restrict the strip to a strict regex `[a-f0-9]{4,8}$` AFTER a non-letter boundary; safelist common 4-hex words ("face", "deed") if needed |
| Cross-year fuzz collapses two DIFFERENT papers that share 90 %+ title | Add publisher/venue penalty — if venues are both known and disagree by token-set distance > 0.6, refuse the collapse |
| Redirect cascade (A→B→C) | Resolve transitively in pick_next + record_choice; or compact to flat A→C in a post-merge step |
| Existing rows in archive_responses pointing to a loser cid | A one-time migration script (`scripts/archive/migrate_redirects.py`) UPDATEs them. Idempotent. |

### A.6 Tests

- T-A1: synthetic title pairs covering each of the 5 escape patterns →
  the new fuzz logic returns same cluster.
- T-A2: re-run merge against current archive_papers → expected ≥ 3
  collapses for JOP's confirmed near-duplicates above.
- T-A3: pick_next.py for JOP returns 0 candidates whose normalized
  title matches a previously-answered paper (regression test).
- T-A4: migrate_redirects.py is idempotent (running twice produces
  identical state).

---

## §B — APA citation pre-rendering

### B.1 Empirical evidence

The plugin's Stage 2 Block 1 instructs the assistant to render APA 7
from `authors_json + year + title + venue + doi`. In practice, the
assistant degrades to Korean abbreviation ("2025 Lee 외." / "2024 OO 외.")
because:

- `authors_json` is sometimes a list of bare strings ("Heeseung Lee"),
  sometimes `{"name":"...","position":1}`, sometimes `{"family":...,
  "given":...}`. The skill cannot infer initials reliably from a single
  field — needs a centralized normalizer.
- Volume / issue / pages are often missing → the model's "APA-completion"
  instinct fires a Korean shortform instead of leaving the gaps blank.
- The skill prose is procedural (multi-sentence), so the LLM reads it
  more as a guideline than a contract.

### B.2 Fix — pre-render in pick_next.py

Add a Python helper `pipeline/_apa.py` with one function:

```python
def format_apa(paper: dict) -> str:
    """Return APA 7 reference string, verbatim, no Korean tokens."""
```

It normalizes `authors_json` (string list / dict with `name` /
{family,given}), generates `Family, F. M.` form, joins per APA 7 rules
(≤ 20 authors → list all with `&` before last; > 20 → first 19, "…", last),
emits `(year)` (or `(year [Preprint])` when is_preprint=True), title,
italics-marked `*venue*`, and DOI URL when present. Missing volume /
issue / pages are silently omitted (no Korean substitute). Returns ONE
plain string with newlines preserved.

`pick_next.py` emits `paper.apa_citation` (the formatted string) in its
JSON output. The SKILL.md Stage 2 Block 1 is reduced to ONE rule:

> Render `paper.apa_citation` verbatim. Do not edit. Do not abbreviate.
> Do not add Korean particles, glosses, or wrapping prose.

The assistant can no longer hallucinate a Korean shortform because the
APA string is now a data field, not a construction task.

### B.3 Contract

- `pick_next.py` returns `apa_citation: <string>` for every paper.
- The string contains zero Hangul characters (`re.search(r'[가-힣]',
  apa)` returns None).
- The string contains at least one `(YYYY)` token.
- When `doi` is present, the string contains `https://doi.org/<doi>`.

### B.4 Failure modes

| Failure | Mitigation |
| --- | --- |
| authors_json is corrupt / missing | Render `Author Unknown` (English) + (year). Researcher still sees title + year. Never substitute Korean. |
| Title contains italics-conflicting characters (asterisks already) | Escape literal asterisks in title before wrapping venue in italics |
| Multi-script titles (Korean + English) | Render verbatim; the title field is the source. The APA rule is "no Korean ADDED" — not "no Korean preserved." |
| DOI contains URL fragment / version (e.g. `…v1`) | Render verbatim |

### B.5 Tests

- T-B1: 8 synthetic author_json shapes → each produces a valid `Family,
  F. M.` rendering.
- T-B2: corpus sweep on archive_papers → 100 % of rows produce a
  Hangul-free APA string.
- T-B3: edit SKILL.md, ensure plugin commands surface `apa_citation`.

---

## §C — Multi-lens scientific synopsis

### §C is the largest atomic concern. The user's directive:

> 논문은 항상 한 가지 방법론 한 가지 모델만을 고수하지 않는다. 다면적인
> 렌즈로, 어떤 전제에 기반해서, 어떤 가설을 지지하여, 어떤 조건에서 어떤
> 현상이 발견될 것인지 예측하고, 그 예측이 어떤 측면에서 확인되었는지를
> 중점으로 파악해야 한다.

### C.1 What "multi-lens synopsis" means concretely

For each paper, we want a structured record like:

```
{
  "canonical_id": "8e4c00d…",
  "lenses": [
    {
      "name": "efficient coding",
      "premise": "neural resources are allocated to match natural stimulus statistics",
      "prediction": "response variability scales with d/dθ of the cumulative prior — under skewed numerosity priors, larger magnitudes should be MORE variable",
      "regime": "numerosity estimation, prior-skew manipulation across two preregistered experiments",
      "finding": "behavioural responses show anti-Weber pattern when large magnitudes are made more frequent — directly contradicting classic Weber",
      "interpretation": "the increasing variability typically attributed to Weber may primarily reflect natural-prior skewness, not encoding noise alone"
    },
    {
      "name": "normative Bayesian",
      "premise": "observer combines a learned prior with a noisy likelihood by Bayes' rule",
      "prediction": "Bayesian ideal observer with the manipulated prior exhibits anti-Weber when large numerosities are more frequent or more rewarding",
      "regime": "model-fit to behavioural responses, Fechner-logarithmic encoding kernel",
      "finding": "Bayesian observer reproduces the human anti-Weber pattern; logarithmic encoding survives even under anti-Weber behaviour",
      "interpretation": "Fechner encoding (commonly credited with explaining Weber) coexists with anti-Weber behaviour — encoding and variability are dissociable axes"
    },
    {
      "name": "behavioural",
      "premise": "human numerosity estimates are well-approximated by a Bayesian observer with logarithmic encoding",
      "prediction": "manipulating prior or reward should shift mean response and variability together",
      "regime": "two preregistered numerosity-estimation experiments with prior + reward manipulations",
      "finding": "subjects' responses are best reproduced by logarithmic encoding + anti-Weber when large numerosities are more frequent OR more rewarding",
      "interpretation": "behavioural Weber/anti-Weber pattern is task-set-dependent, not a fixed psychophysical law"
    },
    {
      "name": "neural",
      "premise": "(this paper) — out of scope; not measured",
      "prediction": null,
      "regime": null,
      "finding": null,
      "interpretation": null
    }
  ],
  "framework_connections": [
    {"from":"efficient coding","to":"normative Bayesian","kind":"complementary","note":"efficient-coding prior matches the Bayesian prior; the two converge on the same predicted variability pattern"},
    {"from":"normative Bayesian","to":"behavioural","kind":"prediction→observation","note":"model derives anti-Weber; behaviour shows anti-Weber"},
    {"from":"efficient coding","to":"behavioural","kind":"contradiction with classic Weber","note":"if encoding alone produced Weber, behaviour would NEVER show anti-Weber — it does"}
  ],
  "methodological_notes": "preregistered, 2 manipulations (prior + reward), Fechner-logarithmic kernel as competing encoding model, no fMRI/EEG",
  "limitations": "behaviour only; no neural data; numerosity domain (does not directly test duration / orientation / face)",
  "synopsis_version": "v1.2026-05-27",
  "generator": "opus-paper-synopsis-agent",
  "review_status": "auto_unreviewed | human_approved | needs_rework"
}
```

This is much richer than a dim-tag set. It is what enables the
researcher to read ONE sentence and decide "this matters to my project."

### C.2 Where this comes from — offline Opus team

Generating these per paper at interview time would be:
- Slow (Opus pass ≈ 8-30s per paper),
- Expensive (per-researcher × per-paper),
- Inconsistent (same paper rendered differently across researchers).

Therefore: **the synopsis is precomputed offline, once per paper, by an
Opus agent team and persisted into the DB.** At interview time the
plugin reads the synopsis row. This is the "Opus agent team" the user
referred to — it has a real job now.

Architecture (operator-side, not plugin-side):

```
state/archive/synopses/<canonical_id>.json  (cache file)
csnl_paper_rec.archive_paper_synopses        (DB mirror)

scripts/archive/build_synopses.py
  ├─ load archive_papers + archive_filter_decisions (lab_relevant only)
  ├─ skip rows that already have a synopsis at synopsis_version >= current
  ├─ for each paper:
  │   ├─ spawn the `paper-synopsis` Opus agent (one per paper, parallel up to N=4)
  │   ├─ agent reads: title, authors, year, venue, abstract, dim_tags,
  │   │              lab_scope_tags, the lab's framework primer
  │   │              (state/archive/framework_primer.md — lab vocab,
  │   │              standard lenses, what a "premise" means in this lab)
  │   ├─ agent writes the multi-lens JSON structure above
  │   ├─ a `paper-synopsis-reviewer` Opus agent validates schema +
  │   │   refuses obviously hallucinated content (e.g. lens="neural"
  │   │   when paper has no neural data — the reviewer marks that lens
  │   │   "out of scope" not "fabricated finding")
  │   └─ persist to disk + DB
  └─ output a coverage report (papers done / failed / out-of-scope)
```

### C.3 Database schema

```sql
CREATE TABLE __SCHEMA__.archive_paper_synopses(
  canonical_id      TEXT PRIMARY KEY,
  synopsis_version  TEXT NOT NULL,                 -- 'v1.2026-05-27'
  lenses_json       JSONB NOT NULL,                -- array of {name,premise,prediction,regime,finding,interpretation}
  connections_json  JSONB,                         -- array of {from,to,kind,note}
  methodological_notes TEXT,
  limitations       TEXT,
  generator         TEXT NOT NULL,                 -- 'opus-paper-synopsis-agent'
  review_status     TEXT NOT NULL DEFAULT 'auto_unreviewed'
    CHECK (review_status IN ('auto_unreviewed','human_approved','needs_rework')),
  generated_at      TEXT NOT NULL,
  reviewed_at       TEXT,
  reviewer_init     TEXT
);
CREATE INDEX IF NOT EXISTS ix_archive_synopses_review
  ON __SCHEMA__.archive_paper_synopses(review_status);
```

### C.4 Lab framework primer — `state/archive/framework_primer.md`

A hand-curated 1-page document the synopsis agent must read every run:

- The lab's standard lenses (the four C.1 used, plus optionally:
  *probabilistic population coding*, *attractor dynamics*, *drift
  diffusion*, *predictive coding*, *active inference*).
- For each lens: 2-line description, key variables, 3 classic papers
  cited in the lab.
- Lab vocabulary norms (we say "sensory adaptation" not "neural
  adaptation"; "history effect" specifically means trial-to-trial
  carry-over, not lifetime priors; …).
- What counts as a "premise" vs a "prediction" vs an "observation."

Without this primer, the Opus agent will hallucinate lens names or
misuse "efficient coding" interchangeably with "predictive coding."

### C.5 Interview-time rendering

The plugin's Stage 2 Block 2 (the "personalized recommendation
rationale") is rewritten to:

1. Read `paper.synopses` from pick_next output (joined from DB).
2. Pick the 2–3 lenses MOST RELEVANT to the researcher's projects
   (compare `paper.synopses.lenses[*].name` against the dim_preferences
   focus codes, e.g. F-EFC → efficient coding lens; F-BAY → normative
   Bayesian lens).
3. Render one paragraph per chosen lens in Korean:

   > **Efficient coding 관점**: <premise>. 본 paper 는 <regime> 에서
   > <prediction> 을 예측하고, <finding> 으로 확인했습니다. <init> 연구원님의
   > <project title> 에서 <specific overlap or contrast>.

4. End with the connection summary (1 sentence): "본인의 <project> 에서
   <specific question> 을 <specific way> 로 다루시는 부분에 가장 직접적인
   feed: <which lens of this paper, which finding>."

5. Show the MCQ.

### C.6 Contract

- Every lab_relevant paper in archive_papers has an
  archive_paper_synopses row with `review_status != 'needs_rework'`
  before it can be issued by pick_next. (Pick_next gracefully skips
  rows without a synopsis, until coverage = 100 %.)
- The synopsis must contain ≥ 1 lens that is either marked "out of
  scope" with all sub-fields null, OR has all four sub-fields
  (premise, prediction, finding, interpretation) non-null and non-empty.
- The schema CHECK forces enumerated `review_status`.
- The agent's prompt MUST instruct: "every lens not directly supported
  by the abstract/title text is marked out_of_scope; do NOT
  extrapolate."

### C.7 Failure modes

| Failure | Mitigation |
| --- | --- |
| Opus hallucinates findings the abstract does not state | Reviewer agent runs a verbatim-coverage check: each `finding` field must paraphrase a sentence in the abstract; if no abstract sentence supports it, the reviewer demotes the lens to out_of_scope |
| Opus mislabels lenses (calls predictive coding "efficient coding") | Primer enumerates lens names with their canonical refs; reviewer agent has the same primer and rejects mismatches |
| Cost explosion (8,680 papers × 1 Opus call ≈ several thousand $ in input tokens) | Cache by canonical_id; only regenerate when (a) synopsis_version bumps or (b) the abstract changes. Batch with parallel N=4. Skip already-reviewed rows |
| Synopsis rendered staler than the researcher's project state | At interview time, the plugin computes a *fresh* mapping from synopsis lenses to researcher projects — the synopsis itself is researcher-agnostic; only the rendering is personalized |
| Researcher disagrees with a synopsis | Add a 5th MCQ ("synopsis is wrong") that records to archive_responses.choice_detail and queues the paper for `review_status = 'needs_rework'` |

### C.8 Tests

- T-C1: 10 hand-curated abstracts + ground-truth lens annotations →
  agent + reviewer recovers ≥ 80 % of correct lenses; never hallucinates
  more than 1 % spurious findings.
- T-C2: schema CHECK rejects illegal review_status.
- T-C3: missing synopsis on a queue row → pick_next skips it
  gracefully (does not crash; does not surface the paper).
- T-C4: 5th MCQ "synopsis 가 잘못됐어요" flips review_status correctly.
- T-C5: cost monitor — synopsis pass on a 100-paper sample stays under
  the operator-set per-month budget.

---

## Open design questions (for codex review)

1. **Should §C.5 lens selection use researcher's dim_preferences or the
   queue-builder's dim_match.matched / combos?** Both encode "which lenses
   matter to this researcher" but at different granularities.

2. **For §A.3.5 (Python-side dedup safety net), should pick_next.py
   fail-loudly when it catches a duplicate that slipped through SQL, or
   silently pick the next candidate?** Loud may surface bugs; silent
   protects the researcher's experience.

3. **§C.4 framework_primer.md governance**: who can edit it? Should
   changes bump synopsis_version (triggering full regeneration) or just
   the affected lens?

4. **§C reviewer agent**: is one Opus reviewer per paper enough, or do
   we want 2-of-3 majority voting on lens correctness? Cost vs reliability.

5. **§B vs §C ordering**: §B is cheap and unblocks the visible APA bug
   now; §C is months of work. Should we ship §A + §B first, then design
   §C in a follow-up cycle?

6. **What benchmark do we use to know §C is working?** Without ground
   truth labels on "what lenses does paper X engage," we cannot validate
   the synopsis agent. Should we have 2 lab members (operator + JOP)
   hand-annotate 20 papers as a labelled set?

7. **§C cost discipline**: 8,680 papers × Opus input — what's the
   ceiling? Should we tier (Opus for tier S/A papers, Sonnet for B/C)?
