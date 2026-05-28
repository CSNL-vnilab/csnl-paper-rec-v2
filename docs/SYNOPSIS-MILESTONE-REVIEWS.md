# Synopsis Milestone Reviews

## @20 milestone (2026-05-28 phase 2 batch 1+2+3)

Attack vector: framework selection diversity -- are agents over-fitting to efficient-coding / Bayesian-observer language, or do they pick the framework the paper actually uses?

### Corpus Availability

- Requested prior source: `docs/SYNOPSIS-MILESTONE-REVIEWS.md` @10 section. Status: the file was absent at review start, and git history contained no prior version, so the 16 prior CIDs were not recoverable from that source.
- I did not infer or substitute prior CIDs. The concrete per-synopsis checks below cover the eight supplied batch-3 CIDs only. Therefore, any "cumulative 24" conclusion is blocked by missing review state.
- Abstract grounding source for batch-3 checks: `state/archive/merged_papers.jsonl` field `abstract`; synopsis fields checked from `state/archive/synopses/<cid>.json` fields `frameworks`, `out_of_scope_note`, `key_findings`, and `interpretations`.

### Per-Synopsis Findings

| cid | in/out-scope recheck | framework names | unsupported insertions | role-taxonomy verdict |
|---|---|---|---|---|
| `8690d4466d23791c803508f8621d16d6` | Correct out-of-scope. `out_of_scope_note` says ESL teaching methodology / education, and paper `abstract` is about teaching English, patience, games, projects, and language-learning engagement. | `[]` per `frameworks`. | None; no frameworks emitted. | N/A; out-of-scope shape correctly used empty `frameworks`, `key_findings`, and `interpretations`. |
| `a9286feb73ebad796e4feb68f4405fdc` | Correct insufficient-content exclusion. `out_of_scope_note` says the abstract is only a journal banner string and topic may be in-scope but data is insufficient; paper `abstract` is only the Frontiers opinion-article banner and DOI line. | `[]` per `frameworks`. | None; no frameworks emitted. | N/A; out-of-scope shape correctly used empty `frameworks`, `key_findings`, and `interpretations`. |
| `d4e5107a63691614f4287baf80b03b01` | Correct out-of-scope. `out_of_scope_note` says philology / linguistic-historical methodology; paper `abstract` says it provides information about philology history, teaching methodology, and differences from other fields. | `[]` per `frameworks`. | None; no frameworks emitted. | N/A; out-of-scope shape correctly used empty `frameworks`, `key_findings`, and `interpretations`. |
| `dc037911eb714b9bbd1ece63265ebd65` | Correct out-of-scope. `out_of_scope_note` says computer-vision ML / sparse 2D deep subspace learning without biological neural content; paper `abstract` is PCANet / sparse 2D2PCANet object-recognition classification. | `[]` per `frameworks`. | None; no frameworks emitted. | N/A; out-of-scope shape correctly used empty `frameworks`, `key_findings`, and `interpretations`. |
| `0db5ff7d959be0349158a2ef9863c216` | Correct out-of-scope. `out_of_scope_note` says COVID-19 epidemiology / demographic distribution; paper `abstract` is about COVID-19 transmission risk, mask use, closed-environment duration, ventilation, and fuzzy logic. | `[]` per `frameworks`. | None; no frameworks emitted. | N/A; out-of-scope shape correctly used empty `frameworks`, `key_findings`, and `interpretations`. |
| `0fc7415598bb66c4404cd5bd0fc9998d` | Correct in-scope. `out_of_scope_note=null`; paper `abstract` concerns simulated brain mechanisms for semantic processing, perceptual inputs, working memory, semantic memory, and brain circuits. | `binding-by-synchrony`; `phase comparison mechanism` from `frameworks[*].name`. | `binding-by-synchrony` is agent-inserted under the strict verbatim rule: paper `abstract` says "phase synchronous firing binds features" but does not contain the name `binding-by-synchrony`. `phase comparison mechanism` is abstract-grounded verbatim. | Good role diversity: `frameworks[*].role` uses `primary_lens` and `extended`, not all `primary_lens`. The distinction is plausible, though the first name should use abstract wording. |
| `105b78a9070e7b04b27351ab0895484f` | Correct in-scope. `out_of_scope_note=null`; paper `abstract` explicitly discusses artificial neural networks, biological neuronal-network models, predictive coding, backpropagation, supervised learning, and models of biological learning. | `predictive coding`; `backpropagation`; `artificial neural networks` from `frameworks[*].name`. | None. All three names are abstract-grounded verbatim in paper `abstract`. | Good role diversity: `frameworks[*].role` uses `primary_lens`, `compared_against`, and `context`, matching how the abstract frames predictive coding, backpropagation, and artificial neural networks. |
| `1ea590445b49877319dc145af28d0755` | Correct conservative out-of-scope. `out_of_scope_note` flags title/abstract metadata corruption and molecular addiction biology; paper `abstract` concerns adolescent cannabis exposure, NAc glutamate, astrocytes, p38alpha MAPK, THC, and synaptic plasticity rather than computational/systems modeling. | `[]` per `frameworks`. | None; no frameworks emitted. | N/A; out-of-scope shape correctly used empty `frameworks`, `key_findings`, and `interpretations`. |

### Framework Frequency

Denominator: two in-scope batch-3 synopses available for review (`0fc7415598bb66c4404cd5bd0fc9998d`, `105b78a9070e7b04b27351ab0895484f`). Prior @10 CIDs were unavailable, so this is not a cumulative-24 frequency table.

| framework name | synopsis count | share of in-scope reviewed | grounding verdict | over-fit signal |
|---|---:|---:|---|---|
| `artificial neural networks` | 1 | 50% | abstract-grounded via `105b78a9070e7b04b27351ab0895484f` paper `abstract` and `frameworks` | No; threshold is >50%. |
| `backpropagation` | 1 | 50% | abstract-grounded via `105b78a9070e7b04b27351ab0895484f` paper `abstract` and `frameworks` | No; threshold is >50%. |
| `binding-by-synchrony` | 1 | 50% | agent-inserted label via `0fc7415598bb66c4404cd5bd0fc9998d` `frameworks`; concept is described but name is not verbatim in paper `abstract` | No frequency over-fit, but yes wording drift. |
| `phase comparison mechanism` | 1 | 50% | abstract-grounded via `0fc7415598bb66c4404cd5bd0fc9998d` paper `abstract` and `frameworks` | No; threshold is >50%. |
| `predictive coding` | 1 | 50% | abstract-grounded via `105b78a9070e7b04b27351ab0895484f` paper `abstract` and `frameworks` | No; threshold is >50%. |

No efficient-coding or Bayesian-observer framework appeared in the available in-scope batch-3 synopses. Hypothesis: the current prompt is mostly suppressing the reflexive efficient-coding / Bayesian-observer bias, but the missing prior-16 list prevents a defensible cumulative-24 diversity claim.

### @10 Scope Recheck Carry-Forward

- Batch-3 scope accuracy: 8/8 defensible against `out_of_scope_note` plus paper `abstract`.
- Prior 16 scope accuracy: not rechecked because the @10 CID list was unavailable from the requested document path.
- Cumulative 24 scope accuracy: blocked; do not treat this section as a complete @20 cumulative audit.

### Verdict

TIGHTEN-PROMPT-AND-CONTINUE

Rationale: batch-3 does not show efficient-coding / Bayesian-observer over-fit and role taxonomy is not blob-marked as all `primary_lens`. The only concrete framework-selection defect is a canonicalized label (`binding-by-synchrony`) where the abstract supports the mechanism but not the exact framework name. The process defect is missing milestone state for the prior 16 CIDs.

Concrete patch proposals for `scripts/archive/synopsis_prompt.md`:

1. Add under `frameworks` field rules:

```md
- `frameworks[*].name` must be an exact case-insensitive phrase from the title or abstract, unless the abstract explicitly names the framework in a synonymous spelling. If the abstract only describes a mechanism, use the abstract's mechanism wording instead of adding a canonical framework label. Example: use `phase synchronous firing` if the abstract says that; do not rewrite it as `binding-by-synchrony` unless that name appears.
```

2. Replace self-check item 2 with:

```md
2. For each `frameworks[*].name`, can you point to the exact phrase in the supplied title or abstract? If not, rewrite it to the exact abstract wording or drop it.
```

3. Add to `What NOT to do`:

```md
- Do NOT canonicalize a described mechanism into a famous framework name unless that framework name appears in the title or abstract.
```

## @40 milestone (2026-05-28 phase 2 batches 1-5)

### OOS Rate

CID set assumption: `phase2_progress.json` stores aggregate counts but no CID list, so this audit used the 24 phase-2 synopsis files touched after `started_at=2026-05-28T19:06:48+09:00` plus the first 16 rows implied by `next_index=16` in `state/archive/synopsis_phase2_queue.jsonl`; the next eight `_current_batch` files written after the checkpoint were excluded.

- OOS-marked by `out_of_scope_note`: 30/40 = 75.0%.
- In-scope-marked by `out_of_scope_note=null`: 10/40 = 25.0%.
- Audit-adjusted minimum OOS rate: 32/40 = 80.0% if the two recall misses below are corrected.

### Precision Audit

Sampled 10 of 30 OOS-marked synopses; false positives found: 0/10.

- RETRACTED: In vivo direct imaging of neuronal activity at high temporospatial resolution, `212b48bd4fb8041d74d848c1c8e4f101`: correct OOS because the prompt explicitly excludes retracted papers even when the topic is neuroscientific.
- SpeechSplit2.0: Unsupervised Speech Disentanglement for Voice Conversion without Tuning Autoencoder Bottlenecks, `618e493f49d0568e56e91b95f16996ca`: correct OOS because this is a pure speech-ML voice-conversion paper with no biological neural content.
- Adversarially Robust Continual Learning, `7238221889ca2054966962d1c2c032c3`: correct OOS because this is a pure ML robustness benchmark without biological neural content.
- The Effect of Stimulus Concurrence on Memorizing Constellations in VR, `95ded4ed96ad4dae860c63edeaf8f92c`: correct OOS because the tightened prompt excludes HCI/VR memorization studies without neural data or a theoretical cognitive model.
- Application of the endoscopic camera integrated on the common lamp blade for intubation, `3ec9ae72be4c4ee751a632aa03c248d0`: correct OOS because this is a clinical anesthesiology device paper.
- Evaluation of basal hormone levels and androgen receptor gene mutations in individuals with recurrent abortion, `4228423f7feb3e490e22f2f6c4dd51ce`: correct OOS because this is reproductive endocrinology / clinical genetics with no neural-computation content.
- Human dexterity and brains evolved hand in hand, `afea8bedfa5192c96c8cc3b51e448a16`: correct OOS because the prompt excludes comparative anatomy/evolutionary biology without circuit-level mechanism.
- Identification Approach of Arriving Wave Model Based on Likelihood Ratio Test With Different Sensor Noise Levels, `5a4ad354a118496f5cc211c1ad1db05a`: correct OOS because this is space plasma / sensor-noise physics.
- Ranking-space: magnitude makes sense through spatially scaffolded ranking, `a9286feb73ebad796e4feb68f4405fdc`: correct OOS because the available abstract is only a journal-banner string and is too thin to ground a synopsis.
- What is philology and its difference from other areas, `d4e5107a63691614f4287baf80b03b01`: correct OOS because this is a philology / humanities methodology essay.

### Recall Audit

Risk-weighted sample of 5 in-scope-marked synopses; false negatives found: 2/5.

- DeepPrep: an accelerated, scalable and robust pipeline for neuroimaging preprocessing empowered by deep learning, `ccb2c08818a88951c9e162b7e67a31db`: false negative because the prompt explicitly excludes neuroimaging/preprocessing tools and the emitted frameworks are tooling names, not theoretical lenses.
- Incentivizing free riders improves collective intelligence in social dilemmas, `460c0e911f32cba1c1f3ec30b99a5238`: false negative because the prompt explicitly excludes public-goods / collective-intelligence social games without neural recording or a computational model of the brain.
- Decoding the brain: From neural representations to mechanistic models, `33e1f04768e0d05318e8890f95a267bb`: correct in-scope because it directly addresses neural encoding/decoding and mechanistic readout models.
- Reference induces biases in late visual processing, `1a1628bc5f557dd180054ba07dedcac1`: correct in-scope because it studies visual reference repulsion and sensory/decision-stage encoding-decoding.
- Previous fixations do not facilitate search when a distractor becomes a target, `075277774d8deca8c1ab40f40e973012`: correct in-scope because it studies visual search, prior fixation, and task-relevant attentional priority.

Worst examples: DeepPrep (`ccb2c08818a88951c9e162b7e67a31db`) is the clearest prompt-criteria miss because it exactly matches the new preprocessing-tool OOS category; Incentivizing free riders (`460c0e911f32cba1c1f3ec30b99a5238`) is the clearest social-game miss because the title and abstract both match the expanded public-goods exclusion.

### Efficiency Projection

The observed OOS-marked rate is 75.0%, and the audit-adjusted minimum is 80.0%. A conservative title+venue Python gate would have hard-filtered about 24/40 papers in this milestone set, including the 22 obvious OOS regex hits, the public-goods false negative, and DeepPrep-style preprocessing titles. That leaves about 16/40 papers for agent fanout, so the projected agent-call/token reduction is `1 - 16/40 = 60%`; an aggressive but still reviewable gate catching 26/40 would reduce agent spend by about 65%.

### Framework Diversity

For the 10 in-scope-marked synopses, `frameworks` contains 28 mentions and 28 unique names. The top three framework names by count are `neural decoding`, `mechanistic readout models`, and `encoding-decoding framework`, each with one mention, so top-3 concentration is 3/28 = 10.7%. After removing the two false-negative OOS synopses, the valid in-scope set has 22 framework mentions and top-3 concentration is 3/22 = 13.6%; diversity is not concentrated, but OOS leakage inflated it with invalid tool/social entries such as `deep learning preprocessing pipeline`, `workflow manager orchestration`, and `public goods game`.

### Patches

Hybrid classifier proposal:

- Emit flat OOS JSON directly for hard drops, with `out_of_scope_note="Python prefilter: <category>..."`, empty arrays, `core_question=null`, and no agent call.
- Hard-drop title regex: `^RETRACTED[:\s-]`, `(DeepPrep|fMRIPrep|preprocessing pipeline|scanner benchmark|workflow manager).*(MRI|fMRI|neuroimaging)`, and `(SpeechSplit|voice conversion|PCANet|GANs|LLM|QLoRA)`.
- Hard-drop social/HCI regex: `(public goods|free riders|collective intelligence|social dilemmas|VR|virtual reality|memorizing constellations|interface|assistive technology)`.
- Hard-drop clinical/humanities/engineering regex: `(intubation|recurrent abortion|COVID-19|foreign language|teaching English|philology|speech etiquette|human rights|HL-LHC|quadrupole|plasma wave|biometrics|traffic control)`.
- Venue denylist unless the title has a strong neuro/cognitive keyword: `ICASSP|IJCNN|ACL|EMNLP|CVPR|Radio Science|Future Engineering Journal|Computers in Education`.
- Agent-route positive whitelist: venue/title matches `Journal of Vision|Neural Computation|Frontiers in Neuroscience|Brain Stimulation|Cell|brain|neural|cortex|hippocamp|retina|perception|decision|predictive coding|tDCS`.
- Route ambiguous neuro-adjacent titles with OOS keywords, especially `brain`, `memory`, and `neuroimaging`, to a small operator-review queue rather than silently dropping.
- Persist `processed_cids` in `phase2_progress.json` so future milestone reviews do not need mtime reconstruction.

### DECISION

SWITCH-TO-HYBRID-CLASSIFIER

The synopsis loop is making mostly defensible OOS calls, but at 75-80% OOS the all-agent batch loop is spending most of its budget on papers a deterministic gate can reject, and two obvious tightened-criteria misses still leaked into the in-scope pool. The single most important patch is to insert the title+venue OOS prefilter before agent fanout, with explicit hard rules for neuroimaging preprocessing tools and public-goods/social-game papers.
