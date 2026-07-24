# P34 — autonomous evolution log

Distilled from `state/archive/_explore/batchNN/` (raw agent findings, gitignored).
The orchestrator keeps only the **Action backlog** below in context; everything else is re-read on demand.

> **Re-synced 2026-07-22 after the batch-06 adversarial review (verdict: NEEDS-REWORK).**
> The review (`state/archive/_explore/batch06/L1–L5`) proved this document stale in seven places
> and *actively misleading* in one (change-table row 1). Everything below is now re-verified
> against the working tree and against a live read-only `SELECT` on 2026-07-22, and every claim
> the review voided is listed in **§ Corrections** rather than quietly edited away.
>
> **Two structural changes to how this file works:**
> 1. **IDs are now batch-namespaced.** `R1` previously meant two different P1-class things in
>    this same file — batch-01's `init_db` resurrection (now **done**) and batch-04's undeclared
>    cross-repo contract (still **open**, and the single worst silent-failure generator in the
>    system). They are now `b01-R1` and `b04-R1`. All other IDs carry their batch prefix too.
> 2. **A closed item moves into the change table in the same commit that closes it.** The change
>    table had 2 rows where it should have had 9; five of eight "open" backlog rows were already
>    finished, so an operator prioritising from this document would have spent their first hour
>    on completed work (`L5 §3`). That is the process defect this re-sync exists to fix.

---

## 변경대조표 (change table) — running

| # | Area | Before | After | Reversal |
|---|---|---|---|---|
| 1 | `archive_researcher_queues.builder` | **column absent** — `build_digest.py:116` crashed with `UndefinedColumn`, swallowed by `\|\| true` in the weekly cron; boards could never refill | P33 migration applied; column present, **1400 rows backfilled `builder='brq'`**; `archive_discovery_watermark` created. The crash is genuinely gone. ⚠️ **The evidence originally recorded here — "`build_digest` dry-run clean for all 7" — is VOID. It was a printout of a deadlock. See § Corrections C1.** | `ALTER … DROP COLUMN builder` / `DROP TABLE archive_discovery_watermark` |
| 2 | Slack re-send gate | `state/.APPROVED_20260519-1539` present + live `SLACK_BOT_TOKEN` + intact `08_dm_drafts.json` → `deliver.py --send --operator-approved` was **one command from real DMs to 7 researchers** | stale approval token moved to `state/archive/_explore/_defused/`. ⚠️ **Narrowed claim:** the *token* is gone; the *gate* is not closed — `scripts/_legacy/run_weekly_cron.sh:82` still self-mints `touch state/.APPROVED_$RID` for whatever RID it just created (`L3-8`). Dormant only because `state/.CRON_ENABLED` is absent and no launchd job points at the legacy wrapper (verified: `launchctl` shows only `com.csnl.paper-rec.grm` from this repo). | move the file back |
| 3 | `b01-R1` dead-table resurrection | `init_db.py` recreated the 4 quarantined `*_dead` tables from `schema_v3.sql` / `schema_archive.sql` → running it undid P32 | DDL removed from both schema files (now tombstone comments), names dropped from `init_db.py:_TABLES`; `init_db.py:26-33` documents why | git revert |
| 4 | `b01-G7` `OPENALEX_API_KEY` read by no code | docs claimed P33 consumed it; `fetch_new_papers.py` never referenced it | `fetch_new_papers.py:135` `OPENALEX_KEY_ENV`, `:178` key-gate, `:977` `--require-keys` refusal path, explicit skip message | git revert |
| 5 | `b01-G3` discovery inputs producer missing | `discovery_run/inputs/<INIT>.json` was a required input **no script generated** | `scripts/archive/build_discovery_inputs.py` (34 KB, `ab524ff`). ⚠️ Its best source (`archive_survey_keywords`) is **0 rows**, so it silently falls through to the TF-IDF-junk fingerprint chain — see `b06-SURVEY` in the backlog | git revert |
| 6 | `b01-G4a` `researchers.yaml` emails | all 7 empty, while `docs/CSNL-INFO.md` falsely claimed the two files were in sync | **6 of 7 filled** (`ab524ff`); SYJ deliberately left `""` with an inline comment forbidding a guess. ⚠️ This **arms 6 live recipients** the moment SMTP creds land — see § Corrections C4 | git revert |
| 7 | `b01-F-NOLINT` no BANNED_TERMS executor on the live send path | all 5 parser copies lived on the retired Slack path | `scripts/weekly/_tone_lint.py` wired at `send_notion.py:48,171,187,254`. ⚠️ **It over-blocks**: it treats the deterministic, synopsis-derived `recommendation_ko` as *authored* prose, so `robust`/`holistic`/`leverage` are FATAL on 69/1400 queue rows across 6 of 7 researchers — and a blocked row wedges its slot permanently. See `b06-LINT` (P0) | git revert |
| 8 | `b01-T1`+orphans | 7 zero-reference scripts (~700 LOC) in the live tree | 6 retired to `scripts/_legacy/` (`classify_feedback`, `propose_followups`, `build_dm_drafts`, `build_scout_briefs`, `send_survey_invite`, `archive__init__`). `build_review_packet.py` **kept** — it is referenced by `docs/DISCOVERY-RUNBOOK.md:41` and is the operator's adversarial-review packet builder, i.e. not an orphan | `git mv` back |
| 9 | `b01-DANGLING` | 5 contract files cited scripts that never existed in this repo; `rules/02` anchored the **date law** to a missing `pipeline/02_discover.py` | All 5 converted to explicit *"was never written"* / *"Historical:"* annotations, and `rules/02` re-anchored to the live implementation (`pipeline/_util.py:kst_now()` + the scout SKILL). ⚠️ A naive `grep` for the paths still fires **by design** — the annotations name the non-existent files in order to document that they never existed. `L5 §3` marked this row "open" because it checked the document, not the annotation | git revert |

---

## § Corrections — what the review proved stale or wrong

Listed because the operator prioritises from this file, and four of these changed a decision.

**C1 (P0) — "`build_digest` dry-run clean for all 7" was a printout of a deadlock.**
Live, today:

```
archive_weekly_digests : 1 week only — 2026-W23, 35 rows, 35 notion_page_id,
                         35 response_choice IS NULL, last sent 2026-06-01T08:07Z
```

All 35 rows are the 2026-06-01 P23 **smoke test** (CLAUDE.md P23: "build 35 + send 35"), and
none was ever answered. `build_digest._active_counts()` (`:83-89`) is
`SELECT researcher_id, count(*) … WHERE response_choice IS NULL GROUP BY researcher_id` —
**there is no `week_iso` filter**, so seven-week-old smoke rows still count as "active". At
`:335-338`, `active = 5` → `deficit = 5 − 5 = 0` → `print("… full, no refill")`. So the
"clean" output was seven lines of *no-op*, for all seven researchers, and `build_digest --apply`
is a **guaranteed no-op today**. The migration fix in row 1 is real and the crash is really
gone; the *verification* was void. Second-order: `:118-124` excludes any canonical_id already
present in `archive_weekly_digests` for that researcher — so those 35 papers, never seen by a
human, are **permanently burned** out of the candidate pool. Un-wedging this is `b06-WEDGE`.

**C2 (P1) — the precision figures quoted throughout this file are P24-era and wrong in three
of six cases.** Live `save_later/(save_later+not_relevant)` on all 586 responses, 2026-07-22
(independently reproduced by `L1 §7a` and `L2 §0`):

| | BHL | BYL | JOP | JYK | SMJ | SYJ | MSY |
|---|---|---|---|---|---|---|---|
| **live** | **41.5 %** | 79.7 % | 63.6 % (n=33) | **28.8 %** | **43.2 %** | 73.0 % (n=63) | no data |
| previously quoted | 42 % | 80 % | 64 % | 38 % | 55 % | 90 % (n=11) | — |

**Three** researchers are below the 0.45 `validate_drift` floor, not two — SMJ crossed it and
nobody noticed. **JYK degraded from 38 % to 28.8 % *after* the targeted P24 repair** (pre-P24
32.6 % → post-P24 13.0 % on the 23 responses that followed). JOP (n=31) is statistically
unevaluable — stop quoting his 64 %.

**C3 (P1) — SYJ has 65 responses, not 11.** The "n=11, statistically meaningless" caveat is
wrong by ~6× and must be withdrawn. She is the second-best-served researcher in the lab
(73.0 % at n=63 save/reject) *and* the one silently skipped by `notify.py` every week.

**C4 (P1) — "G4 email double-blocked (SMTP empty **and** all 7 emails empty)" is stale.**
Commit `ab524ff` filled 6 of 7 addresses (change-table row 6). Verified in `.env`:
`SMTP_HOST` set, `SMTP_USER`/`SMTP_PASS`/`SMTP_FROM` **empty**. So answering **B1** does not
"unblock a finished feature" — it **arms six live recipients in one step**, one of whom (JYK)
is on the operator's own address. B1 is therefore a safety interlock, not a convenience.

**C5 (P1) — `b01-G2` "P28 is green except its migration" is stale in the worst direction.**
The migration is applied. Live: **all seven `archive_survey_*` tables exist and all seven have
0 rows.** The remaining half is one operator command whose dry-run batch-01 already verified
green (7 researchers / 15 aims / 55 negatives / 105 keywords). The item vanished from the
backlog at exactly the moment it became actionable. It is now `b06-SURVEY` (P0).

**C6 (P2) — `b04-R1` (undeclared cross-repo contract) was never a `phase`-*enum* problem.**
Live: `csnl_research.projects.phase` is **nullable free text with no CHECK and no enum type**;
the only constraint on the table is its primary key. "Extend the enum" has no referent. The
gate is a closed whitelist against an open vocabulary the *other* repo mints at will. Worse,
the failure it is aimed at (`confidence_avg` decay → empty) **has never fired** — no active
project has ever decayed below 0.7 — while a different failure *is* firing today: `SMJ /
visual_search`, `phase = NULL`, `confidence_avg = 0.95`, `last_updated_at 2026-06-02` (the
**freshest row of all 14**) is dropped by `NULL IN (...)` → `NULL` at all six gate sites.

**C7 (P2) — the real failure mode of the eligibility gate is *stale*, not *empty*.**
`build_researcher_queue.py:758-760` `print`s and `continue`s for a researcher with no eligible
projects; `:1100` therefore omits them from `rids`; `:1130-1134` prunes **per rid**, so their
previous 200 queue rows survive untouched, indefinitely. `build_digest` then reads a full,
healthy-looking queue built from a profile that no longer exists. Empty is loud; stale is
invisible. Any alarm must assert `built_at` freshness, not only zero-projects.

---

## Action backlog — OPEN items only

Closed items live in the change table above. Priorities are dependency-derived (`L5 §5`),
not inherited from the previous ordering.

**Blocking constraint stated once, because 9 of these rows are downstream of it:**
`archive_responses` has not grown since **2026-06-09**; `archive_weekly_digests` has one week
in it, unanswered, from **2026-06-01**; `state/.P23_ENABLED` is **absent**; the Notion boards
are **unshared**. The intake loop is closed at the delivery end. **No recommender change can be
measured until `B5` (share the boards) and `b06-WEDGE` are cleared.**

> ⚠️ **Concurrency, stated rather than hidden (2026-07-22).** Sibling units in this same run
> are editing the files behind three P0 rows **right now**, uncommitted: `b06-WEDGE`
> (`build_digest.py`, +359/−33 — a slot state machine replacing `_active_counts`), `b06-LINT`
> (`_tone_lint.py` + `send_notion.py`, +396/−51 — an authored/quoted re-split keyed on whether
> `recommendation_ko` rendered synopsis text), and `b06-CORPUS`
> (`scripts/archive/quarantine_bad_records.py`, new). **This document does not mark them
> closed**, because that is precisely the error change-table row 1 made: recording an unverified
> artifact as proof. They move to the change table when they are committed and verified against
> the tree — **verify at read time, not from this file.** Their rows below describe the defect
> as the review measured it, which remains the acceptance criterion either way.

| P | id | action | risk |
|---|---|---|---|
| **0** | `b06-WEDGE` | Un-wedge `build_digest`: scope `_active_counts` to the current week (or expire aged/undelivered active rows), and decide the fate of the 35 W23 smoke rows — they are burned out of the candidate pool for 7 researchers. **Decision → interview A5.** | safe (needs a DB write to clear the smoke ledger) |
| **0** | `b06-LINT` | `_tone_lint` blocks 69/1400 queue rows across 6 of 7 researchers (3 of them the researcher's S-tier pick) on the paper's *own* title/framework words, because `recommendation_ko` — a deterministic concatenation of synopsis fields (`render.py:9-13,142-172`) — is classified `authored` at `send_notion.py:117-136`. And a blocked row never gets a `notion_page_id`, so it counts as *active* forever and the slot never refills. Reclassify as verbatim (or drop the AI-jargon subset for it) **and** stop blocked rows counting as active. **Must land before any board is shared.** | safe |
| **0** | `b06-CORPUS` | Quarantine `canonical_id 417e4953f0c03eb30028183920dbdd35` — real serial-dependence abstract + synopsis, **Bulgarian humanities title, wrong year, a third work's DOI**. It sits in **6 of 7 live queues**, `chunk='classic'`, **A-tier for JYK and MSY**, unanswered and fully eligible for the next refill. `mirror_history.py` would copy the bad citation into the researcher's permanent 논문 리스트. A second such row exists (Кипр/PNAS) but is unqueued. Add a title↔DOI↔year↔synopsis coherence gate that *quarantines* rather than silently drops. **No corpus-hygiene row existed anywhere before this review.** | safe |
| **0** | `b06-SURVEY` | Run `ingest_survey.py --source notion --apply` (operator `!`). Seven tables, 0 rows, migration already applied, dry-run green. Prerequisite for D1, D2, D3, `b04-R5`, and for `build_discovery_inputs` quality. **Ordering hazard: survey first, ops-derived second, always** — D2 writes `confidence='low'` rows into the same tables, and if it lands first the low-confidence import becomes the incumbent. | safe (operator-gated write) |
| **1** | `b04-R1` / E2′ | Sever the cross-repo *control* coupling. Not "extend the enum" (there is no enum — C6). Replace the implicit ops-owned predicate with a paper-rec-owned `archive_project_eligibility(init, project_slug, include DEFAULT true, reason)` read by all **six** gate sites, so a new project fails **noisily-included** instead of silently-excluded. **Decision → interview E2.** | safe |
| **1** | `b06-NULLPHASE` | Minimum fix if E2′ is not adopted: `AND (phase IS NULL OR phase IN (...))` at all six sites. One line, restores SMJ's freshest project (conf 0.95) today. SMJ is at 43.2 %, below floor, and her whole interest text is currently built from one project row whose `background_jsonb`/`connected_graph_jsonb` are both NULL. | safe |
| **1** | `b06-STALE` | Freshness assertion on `archive_researcher_queues.built_at` in the weekly routine (C7). Queues were built **2026-06-05** — 47 days stale — and nothing downstream can tell a queue built this morning from one built in May. | safe |
| **1** | `b06-GRANT` | Provision `csnl_archive_user` — the least-privilege role **already written** at `state/provision/csnl_archive_user.sql` and never applied. Live: `current_user = postgres`, `has_table_privilege('csnl_research.projects','UPDATE') = true`, `csnl_archive_user` does not exist. The `csnl_research` read-only boundary is **prose only**. Compounds with distribution: `plugin/scripts/_pdb.py` hands the same `SUPABASE_DB_*` contract to researcher laptops behind a client-side regex whitelist. **Confirm what credential researchers were actually given.** | safe (grants only) |
| **1** | `b06-AFFORD` | The live Notion channel offers **no 관련 없음 control** — only `읽음`. So the rational way to clear an unwanted paper is to tick "read", which writes a permanent, non-deletable `already_read` row that (a) excludes the paper forever, (b) counts toward the 10-multiple belief trigger, (c) becomes a **positive** signal under `--use-behaviour`. Meanwhile `not_relevant` — the lab's single most valuable signal (BHL 69, JYK 84, SMJ 46) — came from the retired plugin interview and **cannot be produced through the live channel at all**. Add the affordance before sharing. (Also: `발표 예정` is written by `send_notion.py:107` and read by nothing, while `notify.py:109,121` tells researchers to tick it weekly.) | safe |
| **1** | `b06-CAPTURE` | Add `shown_context_jsonb` (tier / rank / composite / build_token / source at time of showing) to the capture path (`plugin/scripts/record_choice.py`, `scripts/weekly/capture_responses.py`). ~20 lines, additive, reversible. Today `archive_responses` carries none of it, so only **247 of 534** save/reject labels join to any current queue row, and pre-2026-06-04 rows join against a *different* queue silently. **Every offline eval in this backlog is built on that broken join.** Cost strictly increases with delay. | safe |
| **1** | `b04-R2` | `notify.py` skips SYJ every week (`config/researchers.yaml:48 email: ""`). Her address exists in `csnl_ops.researchers` (verified live). **`b01-G4a` as originally written could not have fixed this** — it said "fill from `CSNL-INFO §2`", and §2 has no SYJ row. Re-source from `csnl_ops.researchers`. **Decision → interview A1** (two registries disagree about whether she is in the lab). | safe |
| **2** | `b06-PK` | `archive_researcher_queues` PK is `(researcher_id, canonical_id)` — **`builder` is not in it**. Both writers guard with `… DO UPDATE … WHERE queues.builder = EXCLUDED.builder`, so whichever builder inserts a row first **owns it permanently** and the loser is a silent no-op. Currently latent (1400/1400 `brq`), but **D3's shadow evaluation is exactly the action that triggers it**: a p28 shadow run could only score the papers `brq` did *not* pick. Fix the key (or write shadow rankings to a separate table) **before** D3. | safe |
| **2** | `b06-CANON10` | `config/nas_catalog.json → initials{}` has been filled with 18 people incl. `full_name` + `status`, making it the **10th roster copy** — the risk `O3-canon.md:54` flagged as pending. It already contradicts `csnl_ops.researchers` on four rows (`MJC Minjin Choi` vs `Min Jin Choe`; `SYJ active` vs `active=false`; `HJH Hyunju Hwang` vs `HJH (unknown)`; key `SHL` vs `SL` — a **third** identifier for the PI while the `PI→SL` collapse is still undecided) and introduces a fourth status vocabulary. **Strip `full_name`/`status`; keep `aliases`/`dirs`/`dirs_absent`/`observed_filename_tokens`/`evidence`** — those are real NAS facts and belong nowhere else. As a *NAS* canon the file is the best-designed artifact in this review; the defect is scope creep into identity. | safe |
| **2** | `b06-SAMEWORK` | `build_digest._drop_same_work` (`:160-180`) suppresses candidates via `_common.same_work()` and **prints nothing**. `same_work` falls back to `token_set_ratio ≥ 92` whenever either side lacks a real DOI — **5 863 of 9 015 papers (65 %) have no DOI** — with no minimum-title-length and no year guard. Realized drops today: **0** (good). But the corpus already holds 131 pairs ≥ 92 with non-identical titles, incl. four genuinely distinct `Weekly LS Letter May1..May5` documents. Log every drop with both titles + branch; require `len(title_norm) ≥ 40` for a title-only merge. | safe |
| **2** | `b01-DANGLING2` | Not a defect — recorded so it is not re-litigated. The 5 dangling paths still appear in `grep` **because the fix was annotation, not deletion**. See change-table row 9. | — |
| **3** | `b01-X8` | `bib-dedupe` rules to harden `same_work()` (folds into `b06-SAMEWORK`) | safe |
| **3** | `b01-X3`/`X4`/`X5` | Learned scorer + non-circular eval. **Starves until B5.** Measured, though: a 15-line per-researcher TF-IDF + logistic regression on the *existing* 586 labels gets temporal-split AUC **0.925 (BHL)**, 0.857 (SYJ), 0.848 (BYL), 0.821 (SMJ) against a live composite AUC of ~0.63 (and 0.405 for BYL — worse than a coin flip). Needs a min-token/abstain guard (`L2 §2b`: BHL's rank-#8 unshown paper is a Bulgarian humanities title) and must **blend** with `composite`, not replace it. **Explicitly not the answer for JYK** (0.671) and **impossible for MSY** (0 labels). | M |
| **4** | `b01-X2` | pgvector migration. **Demoted from P3.** Same vectors, same cosine, **identical recommendations**; it is a performance refactor over a 9 015-paper corpus that fits in RAM, and it is the only backlog item flagged `needs-backup` — highest risk, least user-visible return. Justify on latency or on enabling full-corpus scoring, never on precision. | needs-backup |
| **4** | `b01-X9` | MMR diversity. **Demoted.** It deliberately trades precision for coverage while three of six researchers are below the floor and one is at 28.8 %. ~150 reason texts were read across JYK/BHL; **not one complains of redundancy** — every complaint is topical mismatch. | safe |

**Dropped outright** (do not re-open without new evidence): `b01-X10` OpenScholar_Reranker —
unvalidatable before an eval exists, *and* it puts a neural model in the unattended path, which
collides with DECISIONS-v3 ("NO LLM in unattended path"). `b01-X6` SPECTER2 — same
unvalidatability, defer indefinitely.

---

## STOP COLLECTING — recorded so later batches do not re-litigate

| # | Thing | Why |
|---|---|---|
| S1 | New exploration batches / new corpora | **Frozen until B5 + `b06-SURVEY` land and at least one new response arrives.** The run opened ≈49 findings against ≈8 closures (~6:1) and batch-02 opened four NAS corpus programs — CWLL (967 entries), PaperBlitz (122 decks), GRM_magazine, `Memory/MetaData` — each comparable to the whole original P33 scope, while the core loop `recommend → deliver → respond → learn` has been dead for 7 weeks on a UI toggle. One closed loop beats a fifth corpus. |
| S2 | ops `researcher_summaries` bodies | A decayed, ~60-char-truncated rendering of `csnl_research.projects`, which paper-rec already reads **directly**. Importing them re-introduces May values under a July date stamp. |
| S3 | More `archive_relevance_decisions` | 1824 rows, **`gate_engine` NULL on every one** → unattributable to a policy, unusable for eval. Backfill the provenance column before generating more. |
| S4 | More BYL interview volume | n=140, precision .80 — the best-characterised researcher in the lab. Marginal information is now in negatives, not positives. |
| S5 | Slack channel config | 14 IDs, verified identical across 5 copies, on a route P23 retired. **Delete, don't canonicalise.** |
| S6 | An 11th roster copy | See `b06-CANON10`. Nine already exist. |

---

## Saturation scoreboard — the stop condition, made checkable

The operator's stop condition is *"run until csnl-ops and paper-rec need no more information."*
`grep -rn "saturat\|stop condition" docs/*.md CLAUDE.md` returned **0 hits** before this
re-sync — the condition existed only in the prompt, so no run could evaluate it. Recorded here
with today's values so subsequent batches report **movement**, not activity.

| id | Assertion | Today (2026-07-22, live) |
|---|---|---|
| **R-S1** profile coverage | every active target has ≥1 complete `archive_survey_aims` tuple, ≥1 negative, no ambiguous keyword lacking `operational_def` | **0 / 7** — all seven tables 0 rows |
| **R-S2** *consumption* | every field asserted in R-S1 is read by the builder `build_digest` accepts (`builder='brq'`) | **0 fields.** `grep -c archive_survey build_researcher_queue.py` = 0. Only the parked `recommend.py` (`builder='p28'`, **0 rows**) reads them. This is the clause that makes saturation about the researcher's experience rather than table population |
| **R-S3** marginal information | next harvest changes each top-20 by Jaccard ≥ 0.90 and adds < 5 % new anchor phrases | **unmeasurable** — no rebuild cadence, no retained queue diff, no phrase provenance |
| **R-S4** non-circular calibration | recall@k over ≥50 **off-policy** positives per researcher, stable ±5 % across two builds | **0 off-policy labels.** The only metric (`validate_drift.mcq_precision_30d`) is computed over papers the policy itself surfaced |
| **R-S5** freshness | every artefact the live builder consumes is within a declared TTL | **no TTL exists.** Fingerprints 2026-05-26 · queues 2026-06-05 · responses 2026-06-09 · digests 2026-06-01 |
| **O-S1** roster closure | every initial resolves to exactly one row via `researchers ∪ researcher_aliases`; no `(unknown)`/`(past member)` stubs | **2 stubs** (`SYJ`, `HJH`); `csnl_ops.researcher_aliases` **does not exist** (verified) |
| **O-S2** event closure | `presenter_initial` on all GRM; PB attribution ≥ 78 %; `paper_doi` where the filename names a paper | grm 47/107 · **PB 0/52** · **paper_doi 0/172** |
| **O-S3** corpus closure | `csnl_ops.cwll_entries` within ±2 % of ≈967 | **0** |
| **O-S4** anomaly closure | unresolved `sync_anomalies` bounded, under a written triage rule | **2356**, no rule |
| **O-S5** contract closure | an active person with 0 eligible projects raises | 2 of 14 rows silently ineligible; **no alarm** |

**Saturation is FALSE on all ten and UNMEASURABLE on four (R-S2..R-S5).** An autonomous run
told to continue until no more information is needed therefore *cannot halt* — not because
information is missing, but because no instrument reports sufficiency. Note the sequencing
trap: decay/TTL must land **after** a live reinforcement source, never before — ops' own
`JOP.md` shows everything ageing out in one cycle to `Confirmed: (none)`.

---

## Batch 06 — adversarial review (2026-07-22)

Five read-only lenses, verdicts: **L1 architecture NEEDS-REWORK · L2 recommender NEEDS-REWORK
(of the *ordering*, not the recon) · L3 risk NEEDS-REWORK · L4 sufficiency SOUND-WITH-GAPS ·
L5 prioritisation SOUND-WITH-GAPS.** Full reasoning with file:line and live SQL in
`state/archive/_explore/batch06/`. What it changed here: § Corrections C1–C7, the eight new
`b06-*` backlog rows, the STOP list, the saturation scoreboard, and the A4/E2 rewrite in
`docs/P34-DECISION-INTERVIEW.md`.

**What it explicitly endorsed, so it is not re-litigated:** do-not-merge the repos (though for
better reasons than were recorded — see the interview E1); *eval before promotion* for P28;
D2's "deprioritise-priors, not hard vetoes" (the P24 lesson); the metadata-first NAS catalog as
the right answer to the hardcoding bug class; and the two boundary calls — refusing to rotate a
credential, and refusing to invent SYJ's address.

**Process observation it made about itself:** this "read-only review batch" ran *concurrently*
with implementation — `check_materials.py` grew 990 → 1034 lines mid-review and
`ingest_grm_nas.py` gained 920 changed lines. The stale ledger was the direct consequence.

---

## Batch 01 — recon (2026-07-21)

> Numbers in this section are superseded where § Corrections says so — in particular the
> precision figures (C2), SYJ's response count (C3), the email claim (C4) and G2 (C5).

**Headline defects (were live):**
- `b01-G1/F-SKEW` P33 migration unapplied → live crash. **FIXED** (change-table row 1, with C1).
- `b01-L1` loaded re-send gun. **DEFUSED** (change-table row 2, narrowed).
- `b01-R1` `init_db.py` resurrected the 4 quarantined `*_dead` tables. **FIXED** (row 3).
- `b01-F-NOLINT` no BANNED_TERMS executor on the live P23 send path. **FIXED — and now
  over-blocking** (row 7 + `b06-LINT`).

**Untangling F-SKILLDRIFT (it is smaller than believed):**
- `b01-T1` — 4 scripts with **zero** contract references. **Retired** (row 8).
- `b01-T2` — 4 more (`dedup_snapshot`, `apply_feedback`, `fetch_replies`,
  `migrate_legacy_ledger`) need a **4-file, ~8-line** `.claude` edit. Tables dormant since
  2026-05. *Open — interview C1.*
- Nothing in the **DB or plugin** blocks it; the knot is `.claude` prose only.

**Do NOT delete (pending ≠ dead):** `ingest_replies.py` (1700 LOC), `ingest_survey.py`,
`recommend.py` — unwired because their migrations were ungated, not because they are superseded.
(`ingest_survey.py`'s migration is now applied — see `b06-SURVEY`.)

**Other:** SMTP logic triplicated ~90 lines; `build_researcher_queue.main()` = 492 LOC, untested.

**External research (all keyless / no-node):** `X2` pgvector 0.8.0 installed and unused (~7.8 M
floats through `json.loads`) — **demoted to P4**, see the backlog. `X3` per-researcher linear
classifier — **measured, promoted**. `X4`/`X5` eval + non-circular ground truth — starve until
B5. `X1` exploration propensity slot — now folded into `b06-CAPTURE`. `X7` `bm25s` · `X8`
`bib-dedupe` · `X9` MMR (**demoted**) · `X6` SPECTER2 + `X10` reranker (**dropped**).

**Blocked on a human (→ interview):** `b01-G4` email (see C4 — no longer double-blocked) ·
`b01-G6` Notion DBs healthy but **unshared** + GRM schedule DB 404s weekly · `b01-G2` P28 (see
C5) · `b01-G3`/`G7`/`G4a` **all closed**, see change table.

---

## Batch 02 — NAS exploration (share newly mounted)

**Hardcoding bugs that would have emailed researchers false accusations** (the operator's
"do not over-hardcode" directive, vindicated):
- `check_materials.mm_material_present` yymmdd-substring matched only **83/122** real
  meeting-days — **BYL 0/33, SMJ 1/7**. A `--send` would have told BYL 33 decks were missing.
  **Repair in flight** (catalog-driven, `881a91f`).
- `CSNL-INFO §3a`'s `MM_yymmdd.pptx` matches **zero** files; three incompatible per-person
  patterns exist. **2025 GRM lives under `GRM Archive/2025_GRM/` — there is no `GRM/2025/`.**
- Office lock stubs (`~$*.pptx`, 165 B) counted as material → false PRESENT.
- **Root self-symlink + 2 shadow recycle bins + 1 EACCES dir** infinite-loop or crash any walker.
- `ingest_grm_nas` hardcoded `pb → presenter NULL`; false for **122 of 267** PB files.
  **Repaired** (`7435c99`). NAS filenames are **NFD** (NFC fixes 98.0 → 99.94 %).

**The review found three more in the same family — all still live** (interview C2):
- The **folder-missing** branch was never touched by the BYL fix. `nas_catalog.json` records a
  per-person `dirs_absent` list — and for BHL even carries the adjudication *"genuinely does not
  exist; a TRUE gap, not a rule bug"* — but `check_materials` **never reads `dirs_absent`** and
  treats absence as an accusation for everyone. Worse, **SK has 28 `milestone_meetings` rows
  (six in nine days) against exactly one file in `MM/SK`** — any `--send` with a >4-week window
  accuses him of ~27 missing decks. That is BYL-33 reproduced, for a different person, in a
  different code path, *after* the fix.
- **`--audit` — the control that is supposed to catch this — is a tautology.** It derives "real
  meeting days" from the filenames in the same folder, then asks whether a file with that date
  exists there. It always does: `new%` is **always 100 %**, whatever the matcher does. The
  catalog itself flags the load-bearing assumption as `"unverified"` with `tol_days: 0`, so a
  systematic ±1-day naming convention yields 100 % false gaps *and* a PASS. The audit needs a
  calendar-anchored arm (join `csnl_ops.milestone_meetings`, report Δdays).
- **The GRM/PB half is a silent no-op**: rows with a blank presenter are `continue`d *before*
  the checked-counter and with **no warning**, and live `csnl_ops.lab_meetings` has
  `paper_blitz` **0/52** presenters and 11 of 11 recent GRM rows NULL. The report prints
  `GRM=0`, which is indistinguishable from "all present" — the mirror image of a false
  accusation.
- `send_mail` has no `try/except`; a mid-list SMTP refusal skips the once-per-week stamp and
  re-mails everyone already served on the next run.

**New signal far exceeding the 586 `archive_responses` rows** — *harvest frozen per STOP S1,
recorded so it is not lost:* CWLL 237 letters / **967 entries** with the member's own APA +
Keywords + summary, 66 % join with no fuzzy matching, **non-circular** (written independently of
any recommendation) — the best identity+keyword source found, and 5 of 7 fingerprints currently
have **zero anchored phrases** (SMJ has zero multiword phrases at all). Caveat: `citations_log.csv`
DOIs are 10–23 % wrong → re-resolve from the APA string, never the DOI column.
`GRM_magazine/*.pdf` · PaperBlitz 122 attributable decks with explicit "why I chose this paper"
(JYK has **1**) · `Memory/` 49 hypothesis+keyword configs + 136 curated Context PDFs.

**Un-backlogged structural finding the review added (`L2 §4`) — a 3.5× recall ceiling.**
`build_researcher_queue.py:248 _has_usable_abstract` drops every paper with < 100 chars of
abstract. Measured: `archive_papers` 9 015, **with usable abstract 2 399**, without abstract but
with an embedding **6 139**. The recommender's real candidate universe is ~2 399, not 9 015 —
which is why P26 `live_search` is 3.8 % of the corpus but **42 % of every queue**, and why the
best-measured provenance (`classics_smb`, 60.5 % precision) is only 9 % of it. Measured
precision by source: classics 60.5 % · CWLL 59.2 % · pi_network 52.6 % · **live_search 40.4 %**
— and `live_search` splits **JYK 13.6 % / SMJ 60 %**, i.e. it is medicine for one researcher and
poison for another while occupying 81–92 of every researcher's 200 rows *uniformly*.
`scripts/archive/backfill_abstracts.py` already exists. This is a **use-what-we-have** repair,
not a new corpus, so it is compatible with STOP S1 — but it is a code change and belongs to a
code-owning unit, so it is recorded here rather than actioned.

---

## Batch 04 — csnl-ops distillation (merge question answered)

**Verdict: do NOT merge the repos** — endorsed by the review, but note that E1's *stated*
reasons (live Vercel deploy, 86 uncommitted changes, two toolchains) are the weakest available;
they are transient. The durable reasons are structural: the NAS is LAN-only and csnl-ops's own
`AGENTS.md` forbids NAS access from Vercel/CI (physically disjoint execution environments);
paper-rec ships a **versioned plugin to researcher laptops** (`plugin.json 0.6.0`, force-bump
cache-invalidation convention, **no version handshake between plugin code and DB schema**) — a
release surface csnl-ops has no business inheriting; and the schemas already have their FKs in
the right places.

- **`b04-R1` (critical) — undeclared cross-repo contract.** `csnl-ops` writes
  `csnl_research.projects`; paper-rec gates on `phase IN (...) AND confidence_avg >= 0.7` with
  no contract and no alarm on empty. **Amended by C6/C7**: there is no enum, the decay failure
  has never fired, a NULL-phase failure *is* firing, the gate's only two correct exclusions are
  correct by accident, and it lives in **six** copies — two of which ship on researcher laptops.
  `plugin/scripts/preflight.py:100` additionally **misattributes blame to the researcher**
  (*"CSNL 자가아카이브로 프로젝트 정보를 먼저 업데이트해주세요"*) when the real cause is an
  ops-side value. Same harm class as the BYL-33 bug. → backlog `b04-R1`/E2′ + `b06-NULLPHASE`.
- **`b04-R2` — SEVEN registries, not four.** SYJ is an active target (survey + queue + **65**
  responses, C3) yet absent from §2 → `notify.py` skips her every week. JWL/JHR/MJC are active
  with zero recommendations. PI≡SL unaliased — and `nas_catalog.json` has since minted a
  **third** identifier, `SHL` (`b06-CANON10`).
- **`b04-R3` — MSY: re-scoped by the review.** The claim that "ops holds the only coherent
  profile" is **false**. Live: MSY has **two eligible, grounded `csnl_research.projects` rows**
  (`cat_mag_main` 0.86, `face_cond_ver10` 0.75, both with `_grounding` arrays) **and a full
  200-row queue**. The ops summary is a 60-char-truncated rendering of those same rows. MSY's
  actual gap is narrower and different: **0 interview responses** and a blank survey layer.
  → interview D1.
- **`b04-R4` — P28 features starved.** `archive_survey_{negatives,pis}` +
  `keywords.operational_def` are EMPTY, so definition-subtraction is dead code and the PI rerank
  is inert. **Amended**: *all seven* tables are empty (C5), and — the part that was missing —
  **no live path could consume them even if loaded.** `build_researcher_queue.py` (the `brq`
  producer of all 1400 live rows, the only builder `build_digest` accepts) contains **zero**
  `archive_survey_*` references and **zero** `archive_responses` references. 586 human labels,
  100 % reason-annotated on the negative side, and not one line of the live path reads them.
  → interview D2 must name its consumer or it is a no-op.
- **`b04-R5`** — repair JYK's aim axes from the ops record. **Must be gated**: the last
  intervention of exactly this class (P24) made him *worse* (32.6 % → 13.0 %), and his
  rejections show the discriminator is a **stance** axis (task-optimized network with a
  manipulable loss vs. a hand-built dynamical model of the same phenomenon), not a lexical one —
  his rejected papers are lexically on-target, and he has *saved* a serial-dependence paper
  while rejecting others. Bag-of-words cannot represent this. Rewriting someone's research aim
  needs their word. → interview D6.

**Shared canon — the design question, moved to the interview.** `O3-canon.md` (the batch
commissioned to design it) **rejects** a new schema and puts the canon in `csnl_ops`. The
previous A4 banked the opposite. See `docs/P34-DECISION-INTERVIEW.md` A4 — rewritten as a
genuine two-option decision.

**Dormant, unreferenced:** the P32 backlog — 44 actions, 12 closed 2026-06-12, **32 never
re-triaged** and not referenced anywhere in the P34 documents. Fold them in or formally retire
them; an unreferenced 32-item backlog is indistinguishable from lost work.

---

## Batch 8-11 outcomes (2026-07-24) — incident closed + recommender consumes the survey (eval-gated)

**MM false-alarm (live incident):** csnl-ops chased researchers + cc'd the PI about slides that
EXIST on the NAS. Root cause: `milestone_meetings.slides_submitted` is flipped only by a dead node
resolver (Vercel can't see the LAN NAS). Fixed: `reconcile_mm_slides.py` (catalog-driven, NAS-read-
only, False→True only) flipped the **24 stale flags**; escalation PR #19 merged (first notice never
cc's the PI; students only); chaser stays disabled until the reconciler is scheduled ahead of it.

**Survey memory — supply AND consumption (the review's real bottleneck):**
- Loaded `archive_survey_*` (fixed a PK-collision that had silently aborted the whole load):
  **7/7 coverage** (MSY cold-started from the ops summary, confidence=low).
- The AUTHORITATIVE `brq` builder now consumes it — `grep -c archive_survey build_researcher_queue.py`
  **0 → 6**. Survey grounds the query embedding (50% blend) + seeds fingerprints (15 TF-IDF-junk
  phrases → 40-50 real anchors; JOP's curated anchors protected from eviction).
- **eval_recommender.py** (new, temporal held-out, deduped pool, less-circular) GATED the apply.
  Result recall@50: **SMJ 43→79, BHL 0→7, BYL 0→3, SYJ 0→8, JOP flat; JYK 57→29 (regressed)**.
- Honored the gate PER-RESEARCHER: applied survey grounding to the 6 it helps; **JYK held at
  baseline** (his discriminator is a stance axis — his lever is the p28 negatives, not grounding).
  Encoded durably as `SURVEY_GROUNDING_EXCLUDE={'JYK'}` so the go-live command can't regress him.
  Final gate: **PASS** (no target regresses; JYK 57→57).

**Built, not yet applied:** recommend.py (parked p28) negatives/PI/def consumption with the P24
saved-phenomenon-shield fixed; keyless `backfill_abstracts.py` (the 2,399/9,015 abstract ceiling).

**NAS blueprint** (`docs/P34-NAS-BLUEPRINT.md`): nas_index + local-only read-only MCP + reuse-the-
ingesters router, under two hard constraints — efficiency (index conventions not contents) and
**NAS read-only + no path egress off-lab** (operator 2026-07-24).

## Batch follow-up (2026-07-25) — abstract backfill widened the candidate pool 2.4×

Keyless abstract backfill (Crossref/EuropePMC/PubMed, err=0) filled **4,483** null/short abstracts
(rollback manifest saved). Usable-abstract pool 2,399 → **6,882** (26.6% → 76.3%); RECOMMENDABLE
pool (usable ∩ embedded) ~2,399 → **5,798** (a further 1,084 await compute_embeddings — a bonus).
Rebuilt all 7 queues on the widened pool (JYK auto-excluded from survey grounding by the durable
guard); the held-out eval gate **PASSES** (BHL 0→7, SMJ 43→79, JYK 57→57, no target regresses).
The pool-widening's value is forward-looking (more/newer candidates) and additive — the backward-
looking held-out recall is unchanged because held-out positives were already in the old pool.
