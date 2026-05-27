# Per-paper synopsis extraction prompt (P21 v1.2026-05-27)

You are extracting a **skeletal scientific synopsis** from a single
paper for a computational-neuroscience lab. Your output will be used to
match this paper to active researcher projects months from now. The
synopsis must be:

- **Framework-agnostic.** Do NOT assume the paper is about efficient
  coding or normative Bayesian observation. Extract whichever theoretical
  framework(s) the paper itself engages — there are dozens in
  computational neuroscience (predictive coding, drift-diffusion,
  attractor dynamics, reinforcement learning, free-energy / active
  inference, rate-distortion, normalization, divisive gain control,
  signal-detection, generalized linear models, deep RNN / connectionist,
  Bayesian causal inference, sparse coding, mixture-of-experts,
  hierarchical Gaussian filter, …). Name what is THERE, not what you
  expect to be there.

- **Skeletal.** No equations. No method recipes ("subjects ran 8AFC for
  12 sessions"). No paragraph-length prose. Each field is ≤ 1 short
  bullet or ≤ 1 short sentence. Total payload < 200 words.

- **Grounded.** Every claim in `key_findings` and `interpretations`
  must be paraphrasable from at least one sentence in the supplied
  abstract. If the abstract is too thin or the paper is clearly
  out-of-scope (textbook chapter, editorial, conference programme,
  retraction notice), return the out-of-scope shape (see below).

- **No invention.** If the abstract does not name the framework, do not
  guess one. If the abstract does not state a finding, do not infer
  one. Leave the field as an empty array `[]` or `null`.

## Input you will receive

A JSON object with these fields (some may be empty):

```
{
  "canonical_id": "8e4c00…",
  "title": "...",
  "year": 2025,
  "venue": "...",
  "authors": ["...","..."],
  "doi": "10.xxxx/...",
  "is_preprint": true|false,
  "abstract": "..."
}
```

## Required output — return EXACTLY this JSON shape

```json
{
  "frameworks": [
    {"name": "<framework name as the field calls it>",
     "role": "primary_lens|alternative_lens|compared_against|extended|context",
     "one_line": "<≤ 14-word description of what this framework means in THIS paper>"}
  ],
  "core_question": "<one sentence — what does the paper ask?>",
  "key_assumptions": ["<short bullet>", "..."],
  "manipulations":  ["<short bullet — what variable was varied?>", "..."],
  "key_findings":   ["<short bullet — what did they observe?>", "..."],
  "interpretations":["<short bullet — what do the authors claim it MEANS?>", "..."],
  "limitations_noted": ["<bullet>", "..."],
  "connecting_signals": ["<short noun phrase>", "..."],
  "out_of_scope_note": null
}
```

### Field rules

- `frameworks`: 1–4 entries. **Role taxonomy is fixed; framework names are free.**
  - `primary_lens` — the framework the paper is built on.
  - `alternative_lens` — an alternative the paper also considers.
  - `compared_against` — a framework the paper argues against.
  - `extended` — a framework the paper extends with new mechanism.
  - `context` — a framework merely cited as motivation.
- `core_question`: a single research question. Not a finding, not a method.
- `key_assumptions`: ≤ 4 items. What did the paper take as given? (e.g.
  "observer combines prior with likelihood via Bayes rule", "encoding
  capacity is fixed across magnitudes").
- `manipulations`: ≤ 4 items. What did they VARY? (e.g. "prior skewness
  (α=+3.3 vs −3.3)", "reward magnitude across blocks"). NO method
  recipe details (no sample sizes, no rig brand).
- `key_findings`: ≤ 4 items. What did they SHOW empirically?
- `interpretations`: ≤ 2 items. What do the authors CONCLUDE?
- `limitations_noted`: ≤ 2 items. Only what the authors themselves
  state as limitations. Do not invent.
- `connecting_signals`: 3–8 short noun phrases that someone else might
  search for to find this paper. Use the paper's own vocabulary. These
  feed the downstream matching against researcher projects.
- `out_of_scope_note`: normally `null`. Populate it (and return empty
  arrays elsewhere) ONLY when the paper is not a research paper, OR the
  abstract is < 100 useful words after stripping boilerplate.

### What NOT to do

- Do NOT inject `"efficient coding"` or `"Bayesian observer"` because
  the lab studies those — they are JOP's scope, not every paper's.
- Do NOT write equations.
- Do NOT write multi-sentence bullets.
- Do NOT include author names, citation years, or numerical statistics.
- Do NOT translate the abstract into Korean. Output is English JSON.
- Do NOT add prose around the JSON. The reply must be parseable JSON.

### abstract_coverage MUST be a decimal in [0.0, 1.0]

Write `0.85`, NOT `85` and NOT `8`. It is the FRACTION of `key_findings` +
`interpretations` bullets that paraphrase a specific sentence in the
supplied abstract. Examples: `1.0` (all grounded), `0.75` (3 of 4
grounded), `0.5` (half grounded). NEVER write a value greater than 1.

## Self-check before returning

1. Is every `key_findings` and `interpretations` bullet paraphrasable
   from a specific sentence in the abstract?
2. Are all `frameworks[*].name` values framework names that the
   abstract or title actually uses (or that the field clearly cites)?
3. Is the total JSON ≤ 200 words?
4. Did you avoid equations and method recipes?

If any of (1)–(4) fails, FIX before returning. Do not return anything
other than the JSON object (no markdown fences, no commentary).
