-- =====================================================================
-- csnl_assistant read-only research views over csnl_research.projects
-- (2026-06-12)
--
-- Goal: promote csnl_research.projects (14 rows, READ-ONLY, 16 JSONB
-- fields) into a queryable, read-only VIEW layer for an AI research
-- assistant. NO DATA MOVEMENT — every view is SELECT-only over
-- csnl_research.projects, reversible with DROP VIEW.
--
-- Boundaries (inviolable):
--   * csnl_research is NEVER written. These are pure SELECT views.
--   * Views live ONLY in csnl_assistant (already exists).
--   * Defensive JSONB access throughout: -> / ->> / jsonb_typeof /
--     jsonb_path_query_first. Uniform shape is NOT assumed — every
--     field guards on type and coalesces NULL, because the source is an
--     AI-extracted snapshot where keys/objects are present on only some
--     rows (verified read-only 2026-06-12).
--
-- Source columns confirmed read-only (information_schema):
--   init, project_slug, title, phase, confidence_avg (real) +
--   *_jsonb fields. NOTE: there is no scalar `confidence` column —
--   v_project exposes confidence_avg as `confidence`.
--
-- Reverse with:
--   DROP VIEW IF EXISTS csnl_assistant.v_modality;
--   DROP VIEW IF EXISTS csnl_assistant.v_aim;
--   DROP VIEW IF EXISTS csnl_assistant.v_project;
--   DROP VIEW IF EXISTS csnl_assistant.dim_researcher;
-- =====================================================================


-- ---------------------------------------------------------------------
-- (1) dim_researcher — one row per researcher initial (normalized),
--     with project count. init is normalized to upper(trim()) so callers
--     join on a stable key; rows with NULL init are excluded.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW csnl_assistant.dim_researcher AS
SELECT
    upper(trim(p.init))                       AS researcher_id,
    count(*)                                  AS n_projects,
    avg(p.confidence_avg)                     AS avg_confidence
FROM csnl_research.projects p
WHERE p.init IS NOT NULL AND trim(p.init) <> ''
GROUP BY upper(trim(p.init));


-- ---------------------------------------------------------------------
-- (2) v_project — one row per project with a stable project_uid
--     (init:project_slug). Exposes the scalar header columns. confidence
--     maps to the real source column confidence_avg (no scalar
--     `confidence` column exists on the source).
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW csnl_assistant.v_project AS
SELECT
    upper(trim(p.init))                                   AS researcher_id,
    p.init                                                AS init,
    p.project_slug                                        AS project_slug,
    upper(trim(p.init)) || ':' || p.project_slug          AS project_uid,
    nullif(btrim(p.title), '')                            AS title,
    nullif(btrim(p.phase), '')                            AS phase,
    p.confidence_avg                                      AS confidence
FROM csnl_research.projects p;


-- ---------------------------------------------------------------------
-- (3) v_aim — the research aim of each project, extracted defensively
--     from purpose_jsonb / background_jsonb.
--       research_question : purpose_jsonb.research_question
--       hypothesis        : purpose_jsonb.hypothesis
--       scientific_aim    : purpose_jsonb.scientific_aim
--       seed_prior_title  : background_jsonb.prior_studies[0].title
--                           (the "seed" / prior study). Guards on the
--                           array via jsonb_path_query_first so a missing
--                           or non-array prior_studies yields NULL, not
--                           an error.
--       n_prior_studies   : length of prior_studies when it is an array,
--                           else 0.
--     All scalar pulls use ->> + nullif so empty strings become NULL.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW csnl_assistant.v_aim AS
SELECT
    upper(trim(p.init)) || ':' || p.project_slug              AS project_uid,
    upper(trim(p.init))                                       AS researcher_id,
    p.project_slug                                            AS project_slug,
    nullif(p.purpose_jsonb ->> 'research_question', '')       AS research_question,
    nullif(p.purpose_jsonb ->> 'hypothesis', '')              AS hypothesis,
    nullif(p.purpose_jsonb ->> 'scientific_aim', '')          AS scientific_aim,
    nullif(
        jsonb_path_query_first(p.background_jsonb, '$.prior_studies[0].title') #>> '{}',
        ''
    )                                                         AS seed_prior_title,
    CASE
        WHEN jsonb_typeof(p.background_jsonb -> 'prior_studies') = 'array'
            THEN jsonb_array_length(p.background_jsonb -> 'prior_studies')
        ELSE 0
    END                                                       AS n_prior_studies
FROM csnl_research.projects p;


-- ---------------------------------------------------------------------
-- (4) v_modality — per-project modality presence + pipeline status,
--     extracted defensively from modalities_jsonb.
--
--     Shape varies (verified read-only):
--       * behavior is a BARE boolean on most rows (true/null), NOT an
--         object → detected via jsonb_typeof = 'boolean'.
--       * fmri / eeg / meg / eyetracker are OBJECTs with a `present`
--         flag (boolean or null), or the sub-object may be absent → each
--         present-flag guards on jsonb_typeof and coalesces to false.
--       * pipeline status lives on only a few rows:
--           eyetracker.analysis_pipeline_status (e.g. "raw_only …")
--           fmri.status                         (e.g. "inherited_…")
--         both NULL-safe via ->> + nullif.
--
--     _present_flag(field) pattern, inlined per modality:
--       boolean   -> the boolean itself
--       object    -> coalesce((->> 'present')::boolean, false)
--       else      -> false
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW csnl_assistant.v_modality AS
SELECT
    upper(trim(p.init)) || ':' || p.project_slug          AS project_uid,
    upper(trim(p.init))                                   AS researcher_id,
    p.project_slug                                        AS project_slug,

    -- behavior: bare boolean (most rows) or object fallback
    CASE
        WHEN jsonb_typeof(p.modalities_jsonb -> 'behavior') = 'boolean'
            THEN (p.modalities_jsonb ->> 'behavior')::boolean
        WHEN jsonb_typeof(p.modalities_jsonb -> 'behavior') = 'object'
            THEN coalesce((p.modalities_jsonb -> 'behavior' ->> 'present')::boolean, false)
        ELSE false
    END                                                   AS behavior_present,

    -- fmri: object with present flag (boolean fallback for safety)
    CASE
        WHEN jsonb_typeof(p.modalities_jsonb -> 'fmri') = 'object'
            THEN coalesce((p.modalities_jsonb -> 'fmri' ->> 'present')::boolean, false)
        WHEN jsonb_typeof(p.modalities_jsonb -> 'fmri') = 'boolean'
            THEN (p.modalities_jsonb ->> 'fmri')::boolean
        ELSE false
    END                                                   AS fmri_present,

    -- eeg: object with present flag (boolean fallback for safety)
    CASE
        WHEN jsonb_typeof(p.modalities_jsonb -> 'eeg') = 'object'
            THEN coalesce((p.modalities_jsonb -> 'eeg' ->> 'present')::boolean, false)
        WHEN jsonb_typeof(p.modalities_jsonb -> 'eeg') = 'boolean'
            THEN (p.modalities_jsonb ->> 'eeg')::boolean
        ELSE false
    END                                                   AS eeg_present,

    -- eyetracker: object with present flag
    CASE
        WHEN jsonb_typeof(p.modalities_jsonb -> 'eyetracker') = 'object'
            THEN coalesce((p.modalities_jsonb -> 'eyetracker' ->> 'present')::boolean, false)
        WHEN jsonb_typeof(p.modalities_jsonb -> 'eyetracker') = 'boolean'
            THEN (p.modalities_jsonb ->> 'eyetracker')::boolean
        ELSE false
    END                                                   AS eyetracker_present,

    -- pipeline status (present on only a few rows; NULL-safe)
    nullif(p.modalities_jsonb -> 'eyetracker' ->> 'analysis_pipeline_status', '')
                                                          AS eyetracker_pipeline_status,
    nullif(p.modalities_jsonb -> 'eyetracker' ->> 'device', '')
                                                          AS eyetracker_device,
    nullif(p.modalities_jsonb -> 'fmri' ->> 'status', '') AS fmri_status,
    nullif(p.modalities_jsonb -> 'fmri' ->> 'scanner', '') AS fmri_scanner
FROM csnl_research.projects p;
