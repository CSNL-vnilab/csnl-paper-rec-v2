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

> **CRITICAL — FLAT schema only.** All ten content keys (`frameworks`,
> `core_question`, `key_assumptions`, `manipulations`, `key_findings`,
> `interpretations`, `limitations_noted`, `connecting_signals`,
> `out_of_scope_note`, plus the wrapper fields `canonical_id`,
> `synopsis_version`, `generator`, `generated_at`, `review_status`,
> `abstract_coverage`) MUST appear at the **top level** of the JSON
> object. Do NOT nest the content under a `"synopsis"` or `"content"`
> wrapper key — that produces an empty DB row when the importer reads
> top-level fields. (codex @10 milestone review finding — observed in
> 10/24 phase-2 batches.)

```json
{
  "canonical_id": "<cid>",
  "synopsis_version": "v1.2026-05-28",
  "generator": "opus-4-7@2026-05-28",
  "generated_at": "<ISO 8601 KST timestamp>",
  "review_status": "auto_unreviewed",
  "abstract_coverage": 0.85,
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

> ❌ Wrong (will be ignored by the DB importer):
> `{"canonical_id": "...", "synopsis": {"frameworks": [...], "core_question": "..."}}`
> ✅ Right (importer reads `frameworks` from the top level):
> `{"canonical_id": "...", "frameworks": [...], "core_question": "..."}`

### Field rules

- `frameworks`: 1–4 entries. **Role taxonomy is fixed; framework names are free.**
  - `primary_lens` — the framework the paper is built on.
  - `alternative_lens` — an alternative the paper also considers.
  - `compared_against` — a framework the paper argues against.
  - `extended` — a framework the paper extends with new mechanism.
  - `context` — a framework merely cited as motivation.

  **Valid framework names** are *theoretical lenses* the paper engages
  (e.g. `predictive coding`, `efficient coding`, `drift-diffusion`,
  `Bayesian observer`, `attractor dynamics`, `signal-detection theory`,
  `rate-distortion`, `divisive normalization`, `hierarchical Gaussian
  filter`, `binding-by-synchrony`). **Invalid** entries (do NOT list these
  as a framework):
  - Software pipelines or tooling baselines (e.g. `DeepPrep`, `fMRIPrep`,
    `PCANet`, `SpeechSplit`, `TensorFlow`) — those are method/tool names,
    not theoretical frameworks.
  - Generic domain labels (e.g. `neuroimaging`, `decision-making`,
    `vision science`, `cognitive neuroscience`) — those are the field,
    not a framework.
  - Statistical procedures with no theoretical commitment (e.g. `PCA`,
    `linear regression`, `t-test`, `likelihood ratio test`).
  - Implementation choices (`autoencoder bottleneck`, `transformer
    encoder`) absent a theoretical claim about brain function.

  If the paper truly does not engage any theoretical framework (pure
  methods paper, tool benchmark, tutorial, software release), set
  `frameworks: []` and populate `out_of_scope_note`.
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
- `out_of_scope_note`: normally `null`. Populate it (a short prose
  string) AND set all of `frameworks`, `key_assumptions`, `manipulations`,
  `key_findings`, `interpretations`, `limitations_noted`,
  `connecting_signals` to empty arrays `[]` AND set `core_question` to
  null when the paper is out-of-scope or thin.

  **Specifically OUT-OF-SCOPE categories (mark out_of_scope_note even
  if the abstract is rich):**
  - Clinical case reports / surgery / interventional radiology / clinical
    epidemiology with no neural-computation content.
  - Materials science, battery chemistry, particle physics, plasma
    physics, optics engineering.
  - Pure-ML papers (voice conversion, NLP fine-tuning, image
    classification benchmarks) without biological neural content.
  - **Neuroimaging/preprocessing tools and pipelines** (e.g. DeepPrep,
    fMRIPrep release papers, scanner benchmarks). Useful infrastructure,
    but the paper has no perceptual / decision-making / encoding /
    cognitive claim that connects to the lab's theoretical work.
  - Social science / economics / public-goods games / collective
    intelligence experiments without neural recording or computational
    model of the brain.
  - HCI / VR / assistive technology studies (e.g. VR memorization, UI
    design) without neural data or a theoretical model of cognition.
  - Comparative anatomy, evolutionary biology (e.g. primate thumb/brain
    correlations) without circuit-level mechanism.
  - Pedagogy, humanities, political/legal essays, philosophy of
    consciousness *book chapters* (the lab tracks empirical research,
    not philosophy synthesis pieces).
  - RETRACTED papers — even if topic is in-scope, the paper itself was
    withdrawn from the literature and should never surface as a
    recommendation. Set out_of_scope_note explaining the retraction.
  - Abstract is < 100 useful words after stripping boilerplate (journal
    banner, copyright notice, "OPINION article …" only).

  When in doubt between two competing scope calls: prefer
  `out_of_scope_note` over a half-grounded synopsis. A wrongly-included
  paper wastes a researcher's interview turn; a wrongly-excluded paper
  is recoverable when the operator re-runs the queue build.

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
2. Are all `frameworks[*].name` values theoretical lenses the paper
   actually engages (not pipelines, tools, domain labels, or
   implementation choices)? See "Valid framework names" above.
3. Is the total JSON ≤ 200 words?
4. Did you avoid equations and method recipes?
5. **Is the JSON FLAT?** Top-level keys must be exactly the wrapper +
   content fields listed in the schema above. NO `"synopsis": {...}` or
   `"content": {...}` nesting. Open your JSON: if you see `synopsis` or
   `content` as a key whose value is an object containing `frameworks`,
   STOP and rewrite as flat.
6. If `out_of_scope_note` is non-null, are `frameworks` empty,
   `core_question` null, and all bullet arrays empty? Conversely, if
   `out_of_scope_note` is null, do all content arrays have at least one
   grounded entry?

If any of (1)–(6) fails, FIX before returning. Do not return anything
other than the JSON object (no markdown fences, no commentary).
