# P29 — Harness & Memory Hardening Plan (v2, post-adversarial-review)

> Status: **EVOLVED after 3 Opus adversarial reviews** (2026-06-09). v1 was a
> "rebuild the spine" plan; all three skeptics returned **OVER-SCOPED /
> NEEDS-REWORK** with the same core message: the *diagnosis* is sound and
> valuable, but v1 (a) understated a **live** researcher-send footgun, (b)
> promoted an unvalidated, behaviour-blind recommender to primary, (c) proposed
> a unifying memory VIEW that is a category error, (d) demoted the system's only
> closed-loop learning, and (e) packaged it as the big rewrite this operator has
> declined six times. v2 reframes to **"disarm + commit-the-already-built +
> harden", with a *gated* post-survey rebuild** — additive, reversible, and
> matched to the pre-survey budget. The full finding→resolution map is the
> appendix.
>
> One-line reframe: **we are not rebuilding the memory; we are committing and
> hardening the P28 work that's already built, de-fanging a live cron, and
> sequencing the real rebuild for when the survey answers land.**

---

## 0. Boundaries (unchanged, inviolable)
`csnl_research` READ-ONLY; prod-DB writes = operator `!`/grant; agents SELECT-only;
**no researcher-facing sends**; live Notion surveys FROZEN
(`[[p28-survey-memory-pending-live-ingest]]`); `archive_responses` + filled
surveys = read-only truth; adversarial review via Opus skeptic (codex blocked).

---

## 1. ⚠ IMMEDIATE — the live Slack-cron footgun (do FIRST, before any plan work)

**This is the only true emergency, and v1 under-rated it.** Verified live state (2026-06-09):
- `launchctl list` → `com.csnl.paper-rec.{weekly,tick,evolution}` are **loaded** (status 126 = firing+erroring, not unloaded).
- `state/.CRON_ENABLED` **exists** (0-byte session debris) — the gate `run_weekly_cron.sh:23` checks.
- `scripts/run_weekly_cron.sh:82-84` **self-approves**: `touch "state/.APPROVED_$RID"` then `deliver.py … --send --operator-approved` ("real Slack DMs, sequential ≥7s"). The "operator-approved" gate is **theater** — the script creates its own token.
- The **only** real barrier is the off-ramp (`:54-76`: missing `08_dm_drafts.json` → notify+skip).

**Blast radius:** next Friday the weekly cron fires, self-approves, and the only thing between it and live researcher DMs is whether a `state/runs/<thisFriday-RID>/08_dm_drafts.json` exists. If anyone runs `/paper-rec-orchestrator` for this week (which the v1 plan itself wanted to keep exercising), that file materializes → **unattended Slack send to researchers**, violating the inviolable no-send boundary.

**Action (operator, ~10 min, fully reversible):**
```
launchctl unload ~/Library/LaunchAgents/com.csnl.paper-rec.{weekly,tick,evolution}.plist
rm -f state/.CRON_ENABLED '#' enable soft the token       # disarm gate + session debris
```
Then a one-line code fix: **delete the self-approving `touch "state/.APPROVED_$RID"`** in `run_weekly_cron.sh` so the v3 path can never send without a real operator token. (`launchctl unload` is the front-line fix; the flag/self-approve edits are defense-in-depth.) Keep the plists in-tree as break-glass; `launchctl load` re-arms intentionally.

---

## 2. Current-state diagnosis (the sound part — KEPT, with the GC list CORRECTED)

The fragmentation map all three reviewers endorsed:
- **Four overlapping researcher-profile memories**: `csnl_research.projects` (L1, operator, live) · `archive_survey_*` (L2, researcher, ⏳) · `archive_profile_verifications.dim_preferences` (L3, **the live belief-update loop**) · `fingerprints/*.json` (L4, vocab).
- **Two recommenders racing one table** (`archive_researcher_queues`): legacy `build_researcher_queue.py` (L1+L4) vs `recommend.py` (L2). Both prune by `build_token` with **no builder discriminator** → last `--apply` silently obliterates the other's rows (verified: `build_researcher_queue.py:1124-1127` ≡ `recommend.py:730`).
- **Two delivery crons** with separate flags: dormant v3 Slack (`.CRON_ENABLED`, §1) vs canonical P23 Notion (`.P23_ENABLED`, Wed 14:00).
- **The behaviour-blind gap (new, from review):** `recommend.py` reads **neither** `archive_responses` (568 rows of real save/not-relevant verdicts) **nor** `dim_preferences` (the belief-update loop `pick_next.py:189` re-ranks on after every 10 MCQs). Promoting it as-is = a recommender that has forgotten everything the lab learned in interviews.

**Dead-table list — CORRECTED by grep (v1 had false positives):**
- **Truly dead** (0 non-schema refs): `archive_queue_feedback`, `archive_outcome_signals` only.
- **NOT dead** (v1 was wrong): `archive_weekly_belief_due` (written by `capture_responses.py`, the canonical P23 path), `archive_meta_reviews` (live plugin: `meta_review.py`/`preflight.py`/`_pdb.py`), `archive_paper_sources` (3 ingesters + merge), and the v3 tables (`cycle_state`/`exclusion_rules`/etc. — live readers in the Slack scripts the plan keeps as break-glass).
- **GC is also un-reversible-as-imagined**: a `DROP` is silently undone by the next `init_db.py`/`schema_archive.sql` `CREATE TABLE IF NOT EXISTS` → data gone, empty table resurrected. Any real drop = a **3-file coordinated edit** (table + `init_db._EXPECTED` + schema DDL). → Pre-survey GC = **COMMENT-deprecate the two truly-dead tables only**; nothing dropped.

---

## 3. Minimum high-value PRE-SURVEY plan (5 additive items — the tightened set)

All reversible, no live-survey dependency. Ranked by value÷effort.

1. **Disarm the cron footgun** (§1). *(Emergency; trivial.)*
2. **Commit + record the already-built P28.** `recommend.py`, `ingest_survey.py`, `state/migrations/2026-06-08_p28_survey_memory.sql`, and the CLAUDE.md P28 row are **uncommitted** (git shows the "P28" commit was only the prompt doc). They were built **and adversarially reviewed THIS session** (Opus gate demo 16/18 cut + two Opus skeptic reviews → C1/C2 over-admission/over-veto + the JYK axis-collapse all fixed). Commit so git reflects the reviewed state. *(High value, low effort — it's the foundation everything else assumes.)*
3. **Kill the two-builder race + fix the tier signal** (one idempotent migration):
   - Add `builder TEXT` to `archive_researcher_queues`; make **each** builder prune only `AND builder = <own>`; make readers (`build_digest.py`, `pick_next.py`) select the **owning** builder per researcher. (A bare column does NOT help — the prune predicate and the readers must both consult it; verified.) Additive: both builders coexist explicitly, the operator's grain.
   - **Fix `recommend.py`'s tier**: it writes a literal `tier='B'` for every row (it's connection-based, no S/A/B/C), which silently degrades `build_digest`'s `TIER_TARGETS={S:1,A:2,B:2}` strict solver to `tier_relaxed`. Either derive a real tier from connection strength (specB-strong→S, specB→A, gated→B) **or** make `build_digest` tier-agnostic (composite slice) when `builder='p28'`. Decide before any P28 row reaches delivery.
4. **One-line correctness fix**: `pick_next.py` filters `archive_relevance_decisions` to a single `gate_engine` (stop mixing P26 scout + P28 verdicts).
5. **Harden the ingester against messy real edits** (v1's biggest omission, per review — this de-risks the post-survey ingest most): author 2–3 adversarial edited-survey fixtures (free-prose B-요약 cell · exclude row with no contrast template · half-deleted table · a researcher who pasted a paragraph into a confirm field) and confirm `ingest_survey.py` degrades gracefully to `needs_review=true` rather than fabricating structure. Reuse the existing `--source fixture` + `--emit-needs-review` paths.

**Genuine improvement, optional now (closes the behaviour-blind gap):** make `recommend.py` read `archive_responses` for **behavioural veto-confirmation** (a survey negative is downgraded veto→deprioritize if any `save_later`/`already_read` row matches it) and for boost/suppress (candidates similar to saved papers ↑, to not-relevant ↓). This is the single highest-leverage *quality* fix and it's pre-survey-safe (uses the existing 568 responses).

---

## 4. Memory-model corrections (decide now; apply ⏳ post-survey)

These replace v1 §2.1/§3.1/§3.3, which the review showed were unsound.

- **Precedence = (layer, confidence, recency), NOT blind layer.** v1's "survey always wins" is wrong: `csnl_research.projects` is operator-maintained and may be *newer* than a deadline-filled survey, and the project's own history records juniors filling surveys badly (P24: BHL/JYK precision 42/38%). Rule: a **high-confidence** survey field wins; a **medium/low**-confidence survey field does NOT override a high-confidence operator project — fall to L1 or mark `conflict`. The ingester already records `[자동]/【확인필요】/(직접작성) → high/medium/low`; use it at the tie-break. An **L1 active project absent from L2** surfaces as a candidate aim at *runtime* (not just in a report).
- **Veto gating on confidence + behaviour.** Only **high-confidence** survey negatives hard-veto; medium = deprioritize; low = flag-only. AND behavioural-confirmation (§3 optional item): never hard-veto a phenomenon the researcher has *saved* a paper in.
- **Keep L3/L4 first-class — do NOT demote to "tints."** `dim_preferences` (L3) is the live closed-loop learning (`pick_next.py:141,189` re-ranks on the *latest* belief update); the survey (static, one-shot) is **not** a superset of it. Either `recommend.py` reads L3 + reranks with it, or the belief loop writes back into survey memory — but "demote to optional tint" discards the per-session adaptation P17/P19d were built for.
- **CUT the unified per-(researcher,field) COALESCE view.** Category error: the four stores have different grains (project-JSONB / aim-rows / phrase-lists / dim-maps) with no shared key, and the only consumer (`recommend.py`) already reads the survey tables directly — a SQL view re-encoding the precedence would *re-fragment*, not unify. If a second consumer ever needs it, build a thin **researcher-grain `memory_state` view** (`surveyed|projects-only|cold` + confidence rollup) — not a field COALESCE. Defer.

---

## 5. Post-survey REBUILD sequence (the real critical path — ⏳ operator-gated)

1. `ingest_survey.py --source notion --apply` (P28). Then spot-check `needs_review`.
2. `recommend.py` — now reading L2 survey **+ `archive_responses` + `dim_preferences`** (§3 optional / §4) — builds to a **SHADOW** (`builder='p28'` partition or `archive_researcher_queues_p28`), **NOT** the live delivery queue.
3. **Eval gate (before any promotion):** extend `validate_drift.py` (don't duplicate it) to diff P28-shadow vs legacy precision/recall on held-out `archive_responses`, **per researcher**. Treat this as a **guardrail** (did we regress on known verdicts), not the sole ship gate — it's circular (responses were collected through the queue the recommender built); keep `archive_outcome_signals` for the non-circular outcome signal + a novelty/coverage axis so "high precision via near-duplicates" is penalized.
4. **Promote P28 to delivery-read only if it ≥ legacy** on the guardrail AND the tier issue (§3) is resolved. Flip the builder-owner; legacy becomes the fallback for `projects-only`/`cold` researchers (MSY).
5. **Connection gate, with an alarm:** run the existing P26 inline pattern (`recommend.py --gate-mode emit` → Opus same-job fan-out → `--gate-mode cached`). **Add a cache-miss alarm**: `cached` mode silently *rejects* every uncached borderline (`recommend.py:600`), so freshly live-ingested papers (the cross-species/mechanism matches that justify P28) vanish until an operator runs the gate. The cron must log+notify on uncached-borderline volume (mirror the v3 off-ramp's skip+alarm), or recall decays silently.
6. **Reconcile (operator-gated, flag-not-drop, polysemy-safe):** never auto-drop a fingerprint phrase on negative overlap — JYK's `attractor` is *both* wanted (single-item WM) and rejected (rodent decision). Require a specific multiword negative match AND no positive aim/keyword AND no `save_later` response claiming the phrase, else **flag for the operator** (the P24 follow-up ① stays recorded-only for this exact reason).

---

## 6. CUT / DEFERRED (with the review's reasons)

| v1 proposal | Disposition | Reason |
|---|---|---|
| Unified per-field COALESCE memory view + `memory_provenance` | **CUT** | Category error across grains; only consumer reads tables directly; re-fragments; provenance already in survey `raw_jsonb`/confidence |
| `run_gate.py` orchestration script (pre-survey) | **DEFER** | `recommend.py` already has all 3 gate modes; the fan-out is the P26 inline pattern; script it only if the first real pass is painful |
| `eval_recommender.py` (new, pre-survey) | **DEFER → extend `validate_drift.py`** | Can't eval queues that don't exist until ingest; largely duplicates `validate_drift`'s precision; circular if used as the sole gate |
| `reconcile_memory.py` (pre-survey) | **DEFER** | Every diff needs live survey content as one operand; pre-fill-vs-projects is near-tautological |
| GC Phase-B `DROP` / `_legacy` schema move | **CUT (pre-survey)** | `init_db` resurrects dropped tables; `_legacy` breaks single-schema `ledger_schema()`; only 2 tables truly dead → COMMENT-deprecate those |
| Deprecate `/paper-rec-orchestrator` + scout/draft/review stack | **CUT** | LIVE: CLAUDE.md:9-11 routes every paper-rec request to it; it's the manual high-assurance path. Only the **Slack SEND transport** + `paper-explainer.md`/`classify_feedback.py`/`propose_followups.py` are dead |
| Promote `recommend.py` to primary now | **DEFER → behind the eval gate** | Unvalidated, behaviour-blind, emits no tier; shadow-then-flip per §5 |

---

## 7. Risks · rollback · open questions

**Rollback:** every pre-survey item is a `launchctl`/file op (reversible), a commit, an idempotent additive migration, or a fixture test. Post-survey is shadow-then-gated-flip; queues rebuild from either builder; `archive_responses`/surveys never mutated.

**Open questions for the operator (the review gate):**
- Q1. v3 Slack transport: retire entirely, or keep as break-glass **with the self-approve removed** (real token required)?
- Q2. When P28 is promoted: give `recommend.py` a real connection-derived tier (S/A/B/C), or make `build_digest` tier-agnostic for P28 rows?
- Q3. Build the behavioural-confirmation (`recommend.py` reads `archive_responses` + `dim_preferences`) NOW (pre-survey, high value) or fold into the post-survey rebuild?
- Q4. Eval: extend `validate_drift.py` (lean) vs a fuller harness — and revive `archive_outcome_signals` for the non-circular signal, or drop it?

---

## Appendix — adversarial-review finding → resolution

| # | Reviewer | Finding (verified) | v2 resolution |
|---|---|---|---|
| A-C1 | mem | GC mislabels live tables; DROP undone by `init_db` (data loss + resurrection) | §2 corrected list (only 2 dead); §6 GC=COMMENT-only; drop = 3-file edit, deferred |
| A-C2 | mem | Unified COALESCE view is a category error (heterogeneous grains) | §4 CUT the view; optional thin `memory_state` view only |
| A-H1 | mem | "survey always wins" ignores confidence/recency; lazy-veto risk | §4 precedence=(layer,confidence,recency); veto gated on high-confidence |
| A-H2/H3 | mem | demoting L3 + behaviour-blind `recommend.py` discards closed-loop learning & ground truth | §3 optional + §4: keep L3/L4 first-class; `recommend.py` reads `archive_responses`/`dim_preferences` |
| A-M1 | mem | reconcile phrase-drop nukes polysemous `attractor` (JYK) | §5.6 flag-not-drop, polysemy+behaviour-safe |
| A-M2 | mem | eval circularity; re-imports the P19a-cut backtest | §5.3 guardrail-not-gate; keep `archive_outcome_signals`; novelty axis |
| B-1 | harness | promote unvalidated, no-tier `recommend.py` = silent delivery degradation | §3.3 tier fix; §5 shadow-then-eval-gate-then-flip |
| B-2 / C-I | harness | conflates dead Slack SEND with live orchestrator stack | §6 split: retire transport, KEEP orchestrator |
| B-3 | harness | gate cache-miss → silent recall collapse on live papers; unstaffed Opus pass | §5.5 cache-miss alarm + skip-notify |
| B-5 / C-E | harness | "builder column" doesn't stop the token-prune wipe | §3.3 prune-by-builder + reader-filter (or shadow table) |
| B-6 / C-B | harness | footgun is LIVE (plists loaded, self-approve), not dormant | §1 immediate disarm + delete self-approve |
| C-A | scope | P28 is uncommitted/unrecorded in git | §3.2 commit the built+reviewed P28 |
| C-C/D/F/G | scope | view/run_gate/eval/reconcile are gold-plating or un-buildable pre-survey | §6 CUT/DEFER |
| C-I | scope | operator rejects "big rewrite" bundle | reframed to additive "disarm + commit + harden" |
| (all) | — | diagnosis (§1–§2) is accurate and valuable | KEPT |
```
