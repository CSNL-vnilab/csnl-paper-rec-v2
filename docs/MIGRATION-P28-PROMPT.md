# P28 migration prompt — survey-driven memory rebuild + connection-based recommender

> Paste this into a NEW session to build the harness while the 7 researchers fill the
> Notion profile survey. Goal: when the surveys are done, the infra is ready to ingest
> them into a structured Postgres research-memory and run a connection-based paper
> recommender. **Build against the survey STRUCTURE + pre-fill now; do NOT read/parse the
> live (researcher-edited) Notion pages until the operator says the surveys are complete.**

## 0. Boundaries (inviolable)
- `csnl_research` = **READ-ONLY**. Writes go to `csnl_paper_rec` only.
- Prod-DB writes (migrations/upserts) run by the **operator via `!`** by default; an agent
  may run read-only `SELECT` (via `pipeline/_db.query_json`). Schema changes = idempotent
  migration files under `state/migrations/` that the operator applies.
- **Do NOT touch the live Notion surveys** (researchers are filling them). `--apply` on the
  survey publisher CLEARS+rewrites a page → it would wipe researcher answers. Notion is frozen.
- `archive_responses` (interview ledger) and the filled survey pages are **read-only truth** —
  never overwrite.
- No researcher-facing sends.
- Secrets in `.env` (gitignored): NOTION_API_KEY, DB creds. Never print/commit them. Two
  `ntn_` tokens were exposed in past chat → rotation still recommended.

## 1. Context — what exists
- **The survey** (`docs/INTERVIEW-SURVEY.md`, v13): per-researcher Notion profile. Sections:
  기본정보 · 연구 프로젝트별 핵심 주제 (per-project, with the **domain × phenomenon × task ×
  mechanism** tuple + hypothesis/exploratory + metric/condition/direction + background + seed) ·
  추천에서 빼고 싶은 주제 (H = phenomenon-mismatch `known_negatives` + concrete contrast) ·
  검색 키워드 (G1 list + G2 ambiguous-term **operational definitions**) · 실험 장비 (C) ·
  관심 있는 연구자 (D = PIs + connection + negative PIs) · 계산 모델 (E = model↔aim↔usage) ·
  관심 방법론 (F = data-modality × big-category approach, checkboxes). The operator appendix
  (`## 부록`) holds the **Connection invariant** spec + the 응답→메모리 매핑 + the list of
  previously-미구현 consumers — that appendix IS the build spec for §3.
- **7 Notion pages** under "CSNL 논문 추천" › "연구 프로파일 설문 (2026-06)". Page IDs:
  BHL 3762a38e-4f5f-8155-baff-d7282261ab29 · BYL 3762a38e-4f5f-8163-9594-e9ffb7755431 ·
  JOP 3762a38e-4f5f-81d5-a4c6-c7b454a8a04c · JYK 3762a38e-4f5f-8180-9000-e402f1542518 ·
  MSY 3762a38e-4f5f-81c3-afdc-f18adadb88a3 · SMJ 3762a38e-4f5f-8192-9dc8-f2ee905adcec ·
  SYJ 3762a38e-4f5f-8175-99a4-fe5cd61596fe. (MSY = blank, not yet interviewed.)
- Pre-fill markdown (the pre-edit baseline, gitignored): `state/archive/surveys/<INIT>.md`.
  Use this + `docs/INTERVIEW-SURVEY.md` to build the ingester offline; the live pages hold the
  researchers' edits to read later.
- **Notion helpers**: `scripts/weekly/_notion.py` (list_block_children, retrieve, requests-based,
  NOTION_API_KEY) — reuse for read-only retrieval. `scripts/weekly/notion_survey_pages.py` =
  the md→Notion publisher (its inverse logic informs the parser: to_do "Label: value [flag]"
  confirm-fields, callout answer-boxes, native tables, to_do MCQ, flag color tokens).
- **Current recommender**: `scripts/archive/build_researcher_queue.py` — substring keyword match
  + positive-only scoring + cosine over `archive_paper_embeddings` (is_lab_relevant=true), split
  into recent/mid/classic chunks, with a P26 reasoning-gate boost (COS_FLOOR=0.18). GAPS it does
  NOT do: per-researcher `known_negatives` veto, aim-tuple/same-job admission, definition-aware
  subtractive scoring, mechanism connection axis, PI polarity.
- **Existing `csnl_paper_rec` tables** (integrate, don't duplicate): archive_papers (corpus +
  source=live_search) · archive_paper_synopses (~2063: frameworks/core_question/key_findings/
  connecting_signals/out_of_scope) · archive_paper_embeddings · archive_responses (interview) ·
  archive_profile_verifications (P24 per-researcher dim/profile) · archive_relevance_decisions
  (P26 aim/phenomenon/mechanism judgments) · archive_researcher_queues (output) ·
  archive_filter_decisions · exclusion_rules · fingerprints (`state/archive/fingerprints/*.json`).
- `csnl_research.projects` (READ-ONLY, 14 rows, rich JSONB: purpose/background/apparatus/
  modalities/experiment_design/manipulation/analysis_pipeline/external_refs/...) — the operator-
  maintained ground truth; the survey is the researcher-confirmed overlay.
- Relevant design docs: `docs/HARNESS-DISCOVERY-DESIGN.md` (P26 relevance contract A/B/C),
  `docs/HARNESS-ALGORITHM-DESIGN.md` (P19 scoring), `docs/DISCOVERY-RUNBOOK.md`.

## 2. P28a — DB rebuild from surveys
Design + write an idempotent migration (`state/migrations/2026-06-XX_p28_survey_memory.sql`)
creating `csnl_paper_rec` tables (names indicative; reconcile with existing):
- `archive_survey_profile`(researcher_id PK, name, role, lab, summary, infra, theory_frame,
  format_pref, source_version, ingested_at, raw_jsonb)
- `archive_survey_aims`(researcher_id, aim_id, domain, phenomenon, task, mechanism, metric,
  condition, direction, hyp_type {유형1|유형2}, hypothesis, background, seed_paper, confidence,
  PK(researcher_id, aim_id)) — **domain·phenomenon·task·mechanism = the connection anchor**.
- `archive_survey_negatives`(researcher_id, neg_id, excl_phenomenon, contrast_reason, source)
- `archive_survey_keywords`(researcher_id, keyword, operational_def, conflict_term, bound_aim)
- `archive_survey_methods`(researcher_id, modality, approach)
- `archive_survey_models`(researcher_id, model, applied_aim, usage_mode)
- `archive_survey_pis`(researcher_id, pi_name, connection, polarity {+|−})

Then `scripts/archive/ingest_survey.py`:
- Read each Notion page (read-only, `_notion`), recurse blocks. Parse by section using the known
  v13 render layout: to_do **checked** states (관심 방법론 modality×approach, B4 modality, E usage)
  → methods/models; callout/answer-box text + confirm-field to_do labels ("Label: value [flag]")
  → profile/aims/background/etc.; native tables → B-요약 aims, H negatives, G2 keywords, D PIs,
  E models; flag tokens ([자동]/【확인필요】/(직접 작성)) → per-field confidence/provenance.
- Where free-text needs structuring (e.g., a researcher's edited B-요약 cell), use a small
  LLM-assisted extractor (Opus) constrained to the field's schema; keep verbatim raw in raw_jsonb.
- Idempotent upsert (PK conflict → update), provenance + source_version, dry-run default,
  operator `--apply`. Validate: every [자동] traces to a source; flag unresolved.
- **Build/test now against `state/archive/surveys/<INIT>.md` (pre-fill) + a hand-made edited
  sample**; run on the live pages only after the operator confirms surveys are complete.

## 3. P28b — connection-based recommender (the "Connection invariant")
Upgrade `build_researcher_queue.py` (or new `recommend.py`) to implement the appendix spec:
1. **Candidate generation** (per researcher): embedding cosine (paper synopsis/abstract vs each
   aim + profile) ∪ keyword/aim lexical match → pool. Reuse archive_paper_embeddings + synopses.
2. **Connection score** (candidate × aim) — admit iff a *genuine* connection on **aim ∨
   phenomenon ∨ mechanism** (same computational JOB; species/domain/method never disqualify).
   Hybrid: structured overlap on synopsis fields (frameworks/core_question/connecting_signals vs
   the aim's domain/phenomenon/task/mechanism) as a cheap pre-filter → **LLM reasoning-gate**
   (P26-style: "does this paper genuinely connect to aim X via aim/phenomenon/mechanism, not mere
   word overlap?") on borderline cases. Record which aim + which axis connected (provenance).
3. **Veto**: drop candidates whose phenomenon/research-focus matches an `archive_survey_negatives`
   row (the per-researcher exclude that was always missing).
4. **Definition-aware subtraction**: for ambiguous keywords (G2 operational_def), penalize
   wrong-sense matches; require the paper's usage matches the researcher's definition.
5. **Rerank** (connected papers only): w·(aim-connection) + w·(mechanism match) + w·(keyword,
   def-aware) + w·(PI polarity ±) + w·(method modality×approach) + w·(recency/tier). Tunable.
6. **Output**: per-researcher ranked queue → archive_researcher_queues, with provenance columns
   (connected_aim, connection_axis, veto_checked). Keep the recent/mid/classic chunking.
Honest knobs: log anything capped/dropped; no silent truncation.

## 4. P28c — verify
- Dry-run on the existing corpus using a researcher's current profile (pre-fill / csnl_research)
  as a stand-in; sanity-check that known_negatives veto fires and spurious word-overlap is cut.
- Adversarial review: Opus skeptic (codex is account-blocked — see memory `codex-account-blocked`;
  substitute Opus). Check: over-admission (C-axis over-fire on model-name/generic words),
  veto correctness, definition subtraction, provenance honesty.
- NO live recommendation send (.P23_ENABLED gate stays off). Operator gates any downstream.

## 5. Decisions to confirm at the start (defaults shown)
- Connection scoring = **hybrid** (structured pre-filter + LLM gate on borderline). [default]
- Ingestion source = **live Notion pages** (researcher-edited truth) once complete; build/test
  against pre-fill markdown meanwhile. [default]
- Build order = schema+ingester (P28a) → recommender (P28b) → review (P28c), implementing now;
  ingest when the operator says surveys are done. [default]
- Reconcile new tables vs existing (`archive_profile_verifications`, `archive_relevance_decisions`,
  `fingerprints`) — extend rather than duplicate where they overlap. [confirm]

## 6. Log
Add a CLAUDE.md change-log row (P28). Keep `archive_responses` + filled surveys untouched.
