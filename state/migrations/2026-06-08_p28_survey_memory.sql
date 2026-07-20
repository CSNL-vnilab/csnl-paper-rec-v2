-- ===========================================================================
-- state/migrations/2026-06-08_p28_survey_memory.sql
--
-- P28a — survey-driven research-memory layer + connection-gate provenance.
--
-- Builds the structured Postgres memory that the v13 profile survey
-- (docs/INTERVIEW-SURVEY.md) feeds, and the provenance columns the P28b
-- connection-based recommender writes. Seven new `archive_survey_*` tables
-- (one per survey section that carries structured rows) PLUS two idempotent
-- column extensions that REUSE existing tables rather than duplicate them:
--   * archive_relevance_decisions (P26)  += connected_aim / veto_hit / gate_engine
--     — its relevance_type (A/B/C/none) ALREADY is the connection axis, so the
--       survey-based connection gate writes here, tagged gate_engine='p28-...'.
--   * archive_researcher_queues          += connected_aim / connection_axis /
--     veto_checked — per-row connection provenance for the recommender output.
--
-- Reconciliation (operator §5 [confirm] — "extend, don't duplicate"):
--   * archive_profile_verifications (P24 dim_preferences/chunk_mix) is LEFT
--     UNTOUCHED — it is a separate, session-keyed dimension-weight overlay.
--     archive_survey_profile is the researcher-keyed survey overlay; they
--     coexist (the recommender reads both).
--   * fingerprints/*.json keep their phrase weights; the survey keyword
--     definitions live in archive_survey_keywords (authoritative for the
--     definition-aware subtraction), not bolted onto the fingerprint files.
--   * §I (theory frame / format pref) was deleted in survey v10 → NO
--     theory_frame/format_pref columns (the prompt's indicative spec predates
--     v10). The §계산 모델 stance fields it DOES collect are added instead.
--
-- Survey → memory map (appendix C of the survey):
--   기본 정보            -> archive_survey_profile (summary = sole source)
--   연구 프로젝트별 + 요약 -> archive_survey_aims (domain×phenomenon×task×mechanism
--                          = the CONNECTION ANCHOR; population = priority signal)
--   추천에서 빼고 싶은 주제 -> archive_survey_negatives (the per-researcher veto)
--   검색 키워드          -> archive_survey_keywords (G2 operational_def → def-aware)
--   실험 장비·환경        -> archive_survey_profile.infra
--   관심 있는 연구자       -> archive_survey_pis (polarity +/- ; negative = deprioritise)
--   계산 모델            -> archive_survey_models (확정 모델만)
--   관심 방법론          -> archive_survey_methods (data-modality × approach)
--
-- BOUNDARY: csnl_research is never touched. archive_responses (interview
-- ledger) and the filled survey pages are read-only truth — nothing here
-- writes them. Idempotent; operator-run:
--   ! python3 scripts/run_migration.py state/migrations/2026-06-08_p28_survey_memory.sql
-- ===========================================================================

BEGIN;

-- ----------------------------------------------------------------- profile
-- One row per researcher. summary is the SOLE source of profile.summary
-- (survey §A "주 연구 한 문장 ★필수"). raw_jsonb keeps the verbatim parsed
-- section text + per-field flag provenance so an audit can trace every value.
CREATE TABLE IF NOT EXISTS csnl_paper_rec.archive_survey_profile(
  researcher_id        TEXT PRIMARY KEY
                          CHECK (researcher_id ~ '^[A-Z]{2,8}$'),
  name                 TEXT,
  role                 TEXT,                 -- 직책
  lab                  TEXT,                 -- 소속 lab / 지도교수
  n_projects           INTEGER,              -- active 프로젝트 수
  summary              TEXT,                 -- 주 연구 한 문장 (profile.summary)
  infra                TEXT,                 -- 실험 장비·환경 (§C)
  research_scope       TEXT,                 -- 내 연구 범위 한 단락 (§H preamble)
  modeling_stance      TEXT,                 -- 한다|안한다|읽기만|미정
  wants_modeling_recs  TEXT,                 -- 적극|가끔|거의불필요
  other_methods        TEXT,                 -- §F 기타 free-text
  source_version       TEXT NOT NULL,        -- e.g. 'survey-v13@2026-06-08'
  ingested_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_jsonb            JSONB
);

-- -------------------------------------------------------------------- aims
-- One row per project block. (domain, phenomenon, task, mechanism) is the
-- CONNECTION ANCHOR — the recommender admits a paper iff it genuinely
-- connects on aim ∨ phenomenon ∨ mechanism (NOT exact-match). population /
-- domain are PRIORITY signals only — species never disqualifies. metric /
-- condition / direction are rerankers. confidence is derived from the survey
-- flag ([자동]=high · 【확인필요】=medium · (직접 작성)/blank=low).
CREATE TABLE IF NOT EXISTS csnl_paper_rec.archive_survey_aims(
  researcher_id   TEXT NOT NULL CHECK (researcher_id ~ '^[A-Z]{2,8}$'),
  aim_id          TEXT NOT NULL,            -- 'P1' | 'P2' | 'P3'
  aim_label       TEXT,                     -- project codename if present (Time2Dist)
  hyp_type        TEXT CHECK (hyp_type IN ('유형1','유형2','unknown')),
  domain          TEXT,                     -- anchor
  phenomenon      TEXT,                     -- anchor (the core connection criterion)
  task            TEXT,                     -- anchor
  mechanism       TEXT,                     -- connection axis C (per-aim primary source)
  population      TEXT,                     -- 대상/종 — priority signal, NEVER veto
  metric          TEXT,                     -- 정량화 지표 (reranker)
  condition       TEXT,                     -- 비교 조건 (reranker)
  direction       TEXT,                     -- 방향 (reranker; 유형2 may be 미정)
  hypothesis      TEXT,                     -- 가설 한 문장 OR 탐구 질문
  background      TEXT,
  seed_paper      TEXT,
  measures        JSONB,                    -- 동시측정 {behavior,fMRI,EEG,eye,pupil,tES}
  confidence      TEXT CHECK (confidence IN ('high','medium','low')),
  source_version  TEXT NOT NULL,
  ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_jsonb       JSONB,
  PRIMARY KEY (researcher_id, aim_id)
);
CREATE INDEX IF NOT EXISTS ix_survey_aims_rid
  ON csnl_paper_rec.archive_survey_aims (researcher_id);

-- -------------------------------------------------------------- negatives
-- The per-researcher exclude pass that was ALWAYS MISSING (P24 follow-up ①).
-- neg_type ∈ phenomenon | research-focus | anti-example | adjacent. The veto
-- fires ONLY on phenomenon/research-focus mismatch — NEVER on species/domain/
-- method (those are welcome). contrast_reason is the structured "왜 아닌가".
CREATE TABLE IF NOT EXISTS csnl_paper_rec.archive_survey_negatives(
  researcher_id   TEXT NOT NULL CHECK (researcher_id ~ '^[A-Z]{2,8}$'),
  neg_id          INTEGER NOT NULL,         -- ordinal within researcher
  excl_topic      TEXT,                     -- 제외 항목 (구체)
  neg_type        TEXT,                     -- phenomenon|research-focus|anti-example|adjacent
  contrast_reason TEXT,                     -- 왜 아닌가 (구체 대조)
  confidence      TEXT CHECK (confidence IN ('high','medium','low')),
  source_version  TEXT NOT NULL,
  ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_jsonb       JSONB,
  PRIMARY KEY (researcher_id, neg_id)
);
CREATE INDEX IF NOT EXISTS ix_survey_negatives_rid
  ON csnl_paper_rec.archive_survey_negatives (researcher_id);

-- --------------------------------------------------------------- keywords
-- §G1 list (is_ambiguous=false, no def) + §G2 ambiguous terms (is_ambiguous
-- =true WITH operational_def + conflict_term). The definition drives the
-- definition-aware subtraction: a paper that matches the keyword in the
-- conflict_term's sense (not the researcher's operational_def) is penalised.
CREATE TABLE IF NOT EXISTS csnl_paper_rec.archive_survey_keywords(
  researcher_id   TEXT NOT NULL CHECK (researcher_id ~ '^[A-Z]{2,8}$'),
  keyword         TEXT NOT NULL,
  is_ambiguous    BOOLEAN DEFAULT FALSE,
  operational_def TEXT,                     -- §G2 본인 연구에서의 정의 (nullable)
  conflict_term   TEXT,                     -- §G2 헷갈리는 옆 용어 — 아닌 것 (nullable)
  bound_aim       TEXT,                     -- nullable keyword↔aim binding
  source_version  TEXT NOT NULL,
  ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_jsonb       JSONB,
  PRIMARY KEY (researcher_id, keyword)
);
CREATE INDEX IF NOT EXISTS ix_survey_keywords_rid
  ON csnl_paper_rec.archive_survey_keywords (researcher_id);

-- ---------------------------------------------------------------- methods
-- §관심 방법론 — checked-only (modality × big-category approach). A weak
-- reranker (per appendix C: "method rerank(약)").
CREATE TABLE IF NOT EXISTS csnl_paper_rec.archive_survey_methods(
  researcher_id   TEXT NOT NULL CHECK (researcher_id ~ '^[A-Z]{2,8}$'),
  modality        TEXT NOT NULL,            -- behavior|eye|neural|ann
  approach        TEXT NOT NULL,            -- big-category label (checked only)
  raw_label       TEXT,                     -- full checkbox text
  source_version  TEXT NOT NULL,
  ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (researcher_id, modality, approach)
);

-- ----------------------------------------------------------------- models
-- §계산 모델 — 확정된 모델만 (E). usage_modes ⊆ {적용,검증,확장,반론}. The
-- model connects via axis C only when it is used FOR the aim's phenomenon
-- (same-job); applied_aim records that binding.
CREATE TABLE IF NOT EXISTS csnl_paper_rec.archive_survey_models(
  researcher_id   TEXT NOT NULL CHECK (researcher_id ~ '^[A-Z]{2,8}$'),
  model_name      TEXT NOT NULL,
  applied_aim     TEXT,                     -- 적용 현상(프로젝트)
  usage_modes     JSONB,                    -- ["적용","검증","확장","반론"]
  confidence      TEXT CHECK (confidence IN ('high','medium','low')),
  source_version  TEXT NOT NULL,
  ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_jsonb       JSONB,
  PRIMARY KEY (researcher_id, model_name)
);

-- ------------------------------------------------------------------- pis
-- §관심 있는 연구자 — polarity '+' (주목) deprioritise-inverse, '-' (자주
-- 추천되지만 관심 없는 PI/그룹) deprioritise. A reranker only — never admits.
CREATE TABLE IF NOT EXISTS csnl_paper_rec.archive_survey_pis(
  researcher_id   TEXT NOT NULL CHECK (researcher_id ~ '^[A-Z]{2,8}$'),
  pi_name         TEXT NOT NULL,
  affiliation     TEXT,
  connection      TEXT,                     -- 내 어느 프로젝트/현상과 연결
  polarity        TEXT NOT NULL DEFAULT '+' CHECK (polarity IN ('+','-')),
  source_version  TEXT NOT NULL,
  ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_jsonb       JSONB,
  PRIMARY KEY (researcher_id, pi_name)
);

-- ===========================================================================
-- REUSE — extend existing tables (no duplication).
-- ===========================================================================

-- archive_relevance_decisions (P26) gains survey-gate provenance. The
-- connection gate (P28b) writes A/B/C/none rows here just like the P26 scout,
-- but tags gate_engine='p28-connection' and records WHICH aim connected and
-- WHICH negative (if any) vetoed it. P26 rows keep gate_engine NULL; the
-- recommender filters its own cache by gate_engine so the two never blur.
ALTER TABLE csnl_paper_rec.archive_relevance_decisions
  ADD COLUMN IF NOT EXISTS connected_aim TEXT;
ALTER TABLE csnl_paper_rec.archive_relevance_decisions
  ADD COLUMN IF NOT EXISTS veto_hit      TEXT;   -- neg_id matched, or NULL
ALTER TABLE csnl_paper_rec.archive_relevance_decisions
  ADD COLUMN IF NOT EXISTS gate_engine   TEXT;   -- 'p28-connection' | NULL (p26)
CREATE INDEX IF NOT EXISTS ix_archive_relevance_engine
  ON csnl_paper_rec.archive_relevance_decisions (researcher_id, gate_engine);

-- archive_researcher_queues gains per-row connection provenance. Additive —
-- the legacy build_researcher_queue.py UPSERT lists explicit columns and
-- never touches these; recommend.py (P28b) populates them.
ALTER TABLE csnl_paper_rec.archive_researcher_queues
  ADD COLUMN IF NOT EXISTS connected_aim   TEXT;
ALTER TABLE csnl_paper_rec.archive_researcher_queues
  ADD COLUMN IF NOT EXISTS connection_axis TEXT;   -- 'A'|'B'|'C'
ALTER TABLE csnl_paper_rec.archive_researcher_queues
  ADD COLUMN IF NOT EXISTS veto_checked    BOOLEAN DEFAULT FALSE;

-- ----------------------------------------------------------------- comments
COMMENT ON TABLE csnl_paper_rec.archive_survey_profile IS
  'P28: per-researcher survey profile overlay (researcher-keyed). summary is '
  'the sole source of profile.summary. Coexists with archive_profile_'
  'verifications (P24, session-keyed dim weights).';
COMMENT ON TABLE csnl_paper_rec.archive_survey_aims IS
  'P28: the connection ANCHOR. (domain,phenomenon,task,mechanism) admit a paper '
  'via genuine aim/phenomenon/mechanism connection — NOT exact match. '
  'population/domain = priority signals only; species never disqualifies.';
COMMENT ON TABLE csnl_paper_rec.archive_survey_negatives IS
  'P28: per-researcher veto (the always-missing exclude pass). Fires ONLY on '
  'phenomenon/research-focus mismatch — never species/domain/method.';
COMMENT ON COLUMN csnl_paper_rec.archive_relevance_decisions.gate_engine IS
  'P28: p28-connection = survey-based connection gate; NULL = P26 scout.';

-- ----------------------------------------------------------------- sanity rail
DO $$
DECLARE
  n_tables INT;
BEGIN
  SELECT count(*) INTO n_tables
  FROM information_schema.tables
  WHERE table_schema = 'csnl_paper_rec'
    AND table_name IN ('archive_survey_profile','archive_survey_aims',
                       'archive_survey_negatives','archive_survey_keywords',
                       'archive_survey_methods','archive_survey_models',
                       'archive_survey_pis');
  RAISE NOTICE 'P28a: % / 7 archive_survey_* tables present', n_tables;
  IF n_tables <> 7 THEN
    RAISE EXCEPTION 'P28a migration incomplete: expected 7 survey tables, got %', n_tables;
  END IF;
  RAISE NOTICE 'P28a: relevance/queue provenance columns added (idempotent)';
  RAISE NOTICE 'P28a migration verified.';
END$$;

COMMIT;
