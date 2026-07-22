# P34 — autonomous evolution log

Distilled from `state/archive/_explore/batchNN/` (raw agent findings, gitignored).
The orchestrator keeps only the **Action backlog** below in context; everything else is re-read on demand.

---

## 변경대조표 (change table) — running

| # | Area | Before | After | Reversal |
|---|---|---|---|---|
| 1 | `archive_researcher_queues.builder` | **column absent** — `build_digest.py:116` crashed with `UndefinedColumn`, swallowed by `\|\| true` in the weekly cron; boards could never refill | P33 migration applied; column present, **1400 rows backfilled `builder='brq'`**; `archive_discovery_watermark` created; `build_digest` dry-run clean for all 7 | `ALTER … DROP COLUMN builder` / `DROP TABLE archive_discovery_watermark` |
| 2 | Slack re-send gate | `state/.APPROVED_20260519-1539` present + live `SLACK_BOT_TOKEN` + intact `08_dm_drafts.json` → `deliver.py --send --operator-approved` was **one command from real DMs to 7 researchers** | approval token moved to `state/archive/_explore/_defused/`; gate now fails closed | move the file back |

---

## Batch 01 — recon (2026-07-21)

**Headline defects (were live):**
- `G1/F-SKEW` P33 migration unapplied → live crash. **FIXED** (row 1).
- `L1` loaded re-send gun. **DEFUSED** (row 2).
- `R1` `init_db.py` **resurrects** the 4 quarantined `*_dead` tables (`schema_v3.sql:7,27`, `schema_archive.sql:302,320`) → running it is unsafe. *Open.*
- `F-NOLINT` the `rules/01_tone.md` BANNED_TERMS backstop has **no executor on the live P23 send path** — all 5 parser copies live on the retired Slack path. *Open.*

**Untangling F-SKILLDRIFT (it is smaller than believed):**
- `SKILLDRIFT-T1` — 4 scripts have **zero** contract references (`classify_feedback`, `propose_followups`, `build_dm_drafts`, `build_scout_briefs`, 478 LOC). Retirable with no contract edit.
- `SKILLDRIFT-T2` — 4 more (`dedup_snapshot`, `apply_feedback`, `fetch_replies`, `migrate_legacy_ledger`) need a **4-file, ~8-line** `.claude` edit. Their tables have been dormant since 2026-05.
- Nothing in the **DB or plugin** blocks it; the knot is `.claude` prose only.

**Do NOT delete (pending ≠ dead):** `ingest_replies.py` (1700 LOC), `ingest_survey.py`, `recommend.py` — unwired because their migrations are ungated, not because they are superseded.

**Other:** 3 zero-ref orphans (`build_review_packet`, `archive/__init__.py`, `send_survey_invite`); 5 contract files cite scripts that **never existed** (`rules/02` anchors the date law to a missing `pipeline/02_discover.py`); SMTP logic triplicated ~90 lines; `build_researcher_queue.main()` = 492 LOC, untested.

**External research (all keyless / no-node):**
- `X2` **pgvector 0.8.0 is already installed and unused** — embeddings are still `json.loads`-ed (~7.8M floats) in Python. Migrate to `vector(1024)` + HNSW.
- `X3` replace the hand-tuned `--use-behaviour` phrase boost with a per-researcher linear classifier (arxiv-sanity-lite, MIT, ~50 lines sklearn). Labels already exist: **283 save / 253 reject / 31 read**.
- `X4` real ranker eval via `asreview-insights` (recall@k, WSS@95) + temporal split — today `validate_drift.py` has exactly one metric.
- `X5` **non-circular** ground truth from researchers' own reference lists (S2 `/paper/{id}/references`, keyless) — the current metric is computed only over papers the policy itself surfaced.
- `X1` log an exploration propensity slot now, or off-policy (IPS/SNIPS) evaluation stays permanently impossible.
- `X7` `bm25s` for correct corpus idf · `X8` `bib-dedupe` rules to harden `same_work()` · `X9` MMR diversity over the 5-slot board · `X6` SPECTER2 + RRF fusion · `X10` OpenScholar_Reranker for the gate.

**Blocked on a human (→ handoff):** `G4` email double-blocked (SMTP empty **and** all 7 `researchers.yaml` emails empty — while `docs/CSNL-INFO.md` falsely claims they are in sync); `G6` Notion DBs healthy but **unshared**, GRM schedule DB 404s weekly; `G7` `OPENALEX_API_KEY` is **read by no code** though P33 docs claim otherwise; `G3` P33 discovery cannot run — `discovery_run/inputs/<INIT>.json` is required but **no script generates it**; `G2` P28 is green except its migration (live dry-run parses 7/7).

---

## Batch 02 — NAS exploration (share newly mounted)

**Hardcoding bugs that would have emailed researchers false accusations** (the operator's
"do not over-hardcode" directive, vindicated):
- `check_materials.mm_material_present` yymmdd-substring matched only **83/122** real meeting-days —
  **BYL 0/33, SMJ 1/7**. A `--send` would have told BYL 33 decks were missing. Latent only because SMTP is empty.
- `CSNL-INFO §3a`'s `MM_yymmdd.pptx` matches **zero** files; three incompatible per-person patterns exist.
- **2025 GRM lives under `GRM Archive/2025_GRM/` — there is no `GRM/2025/`** → 100% false "missing" for 2025.
- Office lock stubs (`~$*.pptx`, 165 B) counted as material → false PRESENT.
- **Root self-symlink + 2 shadow recycle bins + 1 EACCES dir** infinite-loop or crash any NAS walker.
- `ingest_grm_nas` hardcodes `pb → presenter NULL`; false for **122 of 267** PB files. `classify()` drops
  20.8% of files and invents junk presenters (`AIGRM`, `special`). NAS filenames are **NFD** (NFC fixes 98.0→99.94%).

**New signal far exceeding the 586 `archive_responses` rows:**
- **CWLL** — 237 weekly letters 2019→2026, **967 entries**, each with the member's own APA + Keywords +
  summary; 66% join with no fuzzy matching. Best identity+keyword source; replaces TF-IDF-junk fingerprints
  for 5/7 researchers. (Warning: `citations_log.csv` DOIs are 10–23% wrong — re-resolve from APA.)
- **GRM_magazine/*.pdf** — weekly per-researcher research question + competing models + open objection.
- **PaperBlitz** — 122 attributable decks with explicit "why I chose this paper" + takeaway.
- **Memory/** — 49 hypothesis+keyword configs, 136 curated Context PDFs; can fully bootstrap MSY.

## Batch 04 — csnl-ops distillation (merge question answered)

**Verdict: do NOT merge the repos** (live Vercel deploy, 86 uncommitted changes, two toolchains).
Build a shared canon instead. Evidence found:
- **R1 (critical) — undeclared cross-repo contract.** `csnl-ops` writes `csnl_research.projects`;
  paper-rec's `build_researcher_queue.py:107` + `build_fingerprints.py:147` silently gate on
  `phase IN (...) AND confidence_avg >= 0.7` — two fields **only csnl-ops writes**, with **no contract and
  no alarm on empty**. The ops decay engine is *designed* to push `confidence_avg` down, so a researcher
  silently drops out of the queue. `phase` has no `pilot`/`paradigm_design`, so BHL has no correct value.
- **R2 — SEVEN registries, not four.** SYJ is an active target (survey+queue+11 responses) yet absent from
  §2 → notify skips her **every week**. JWL/JHR/MJC are active with zero recommendations. PI≡SL unaliased.
- **R3 — MSY:** ops holds the only coherent profile (paper-rec's survey is a blank template) → cold-start today.
- **R4 — P28 features starved:** `archive_survey_{negatives,pis}` + `keywords.operational_def` are EMPTY, so
  definition-subtraction is dead code and the PI rerank is inert for BHL (42%, below floor). Ops has the data.

## Action backlog (the only thing carried in context)

| P | id | action | risk |
|---|---|---|---|
| ~~0~~ | ~~G1~~ | ~~apply P33 migration~~ **DONE** | — |
| ~~0~~ | ~~L1~~ | ~~disarm approval token~~ **DONE** | — |
| 1 | R1 | de-resurrect the 4 `*_dead` tables from `init_db.py` + 2 schema files | safe |
| 1 | G4a | fill `researchers.yaml` emails from CSNL-INFO §2; fix the false "in sync" claim | safe |
| 1 | G7 | make `fetch_new_papers` actually read `OPENALEX_API_KEY`; fix the false doc | safe |
| 1 | G3 | write the missing `discovery_run/inputs/<INIT>.json` producer | safe |
| 2 | T1+orphans | retire 7 zero-reference scripts (~700 LOC) | safe |
| 2 | DANGLING | fix 5 contract files citing never-existent scripts | safe |
| 2 | F-NOLINT | put a BANNED_TERMS executor on the live send path | safe |
| 3 | X2 | pgvector migration (extension already installed) | needs-backup |
| 3 | X8/X9 | bib-dedupe hardening + MMR diversity | safe |
| 4 | X3/X4/X5 | learned scorer + non-circular eval harness | M/L |
