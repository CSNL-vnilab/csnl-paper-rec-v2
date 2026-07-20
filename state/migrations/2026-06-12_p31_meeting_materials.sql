-- ===========================================================================
-- state/migrations/2026-06-12_p31_meeting_materials.sql
--
-- P31 — weekly GRM/PaperBlitz material ingest + presentation-summary space.
--
-- Two new tables in csnl_paper_rec (the schema we own — no csnl_ops scope-
-- expansion needed):
--   (1) archive_meeting_materials  — one row per slide FILE found on the NAS
--       (smb://147.47.70.15/CSNL_new/GRM/2026/<YYYYMMDD>/). Populated by the
--       Wednesday-afternoon scan (scripts/weekly/ingest_grm_nas.py).
--         kind: pb (Paper Blitz — all researchers bar the GRM presenter, 5-min
--               paper summaries) | grm (Research Meeting — one presenter, ~1h)
--               | focus_grm (Notion Type='Focus GRM', multi-presenter/special).
--   (2) archive_meeting_summaries  — the SPACE + flow for presentation SUMMARIES
--       uploaded LATER via Claude MCP (PB: each researcher's paper summary; GRM:
--       the presenter's research-talk summary). Optional link to the slide row.
--
-- Authoritative presenter/date/type truth = Notion DB "GRM 발표 순번 리스트"
-- (collection 4088bc86-a8e3-4386-8f3e-fee006563a0d). The ingest is NAS-scan-
-- primary (filename classification) and Notion-schedule-enrich where reachable.
--
-- BOUNDARY: new objects in csnl_paper_rec ONLY. csnl_research / csnl_ops /
-- archive_responses untouched. NAS is read-only (scan never writes the share).
-- Idempotent; operator-run:
--   ! python3 scripts/run_migration.py state/migrations/2026-06-12_p31_meeting_materials.sql
-- ===========================================================================

BEGIN;

-- ------------------------------------------------- archive_meeting_materials
-- One row per slide file on the NAS. UNIQUE(nas_path) makes the weekly scan
-- idempotent (re-scanning a folder never duplicates). presenter_* is the GRM
-- presenter (NULL for pb, which is the whole lab); schedule_* mirrors the
-- Notion row when the scan could match it by date.
CREATE TABLE IF NOT EXISTS csnl_paper_rec.archive_meeting_materials(
  material_id       BIGSERIAL PRIMARY KEY,
  meeting_date      DATE NOT NULL,
  kind              TEXT NOT NULL CHECK (kind IN ('pb','grm','focus_grm')),
  presenter_initial TEXT CHECK (presenter_initial IS NULL OR presenter_initial ~ '^[A-Z]{2,8}$'),
  presenter_name    TEXT,                     -- from Notion formula or filename
  nas_folder        TEXT NOT NULL,            -- 'GRM/2026/20260610'
  filename          TEXT NOT NULL,
  nas_path          TEXT NOT NULL,            -- '/Volumes/CSNL_new-1/GRM/2026/20260610/...'
  file_format       TEXT,                     -- pdf|pptx|key
  schedule_order    NUMERIC,                  -- Notion 발표 순번
  schedule_type     TEXT,                     -- Notion Type: 'GRM'|'Focus GRM'
  matched_schedule  BOOLEAN DEFAULT FALSE,    -- true if a Notion schedule row matched by date
  source            TEXT NOT NULL DEFAULT 'nas-scan',
  ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_jsonb         JSONB,                     -- {size,mtime,schedule_page_url,...}
  UNIQUE (nas_path)
);
CREATE INDEX IF NOT EXISTS ix_meeting_materials_date_kind
  ON csnl_paper_rec.archive_meeting_materials (meeting_date, kind);

-- ------------------------------------------------- archive_meeting_summaries
-- The space for LATER Claude-MCP-uploaded summaries. PB → one row per
-- researcher per paper (author_initial = the summariser, paper_ref = the
-- paper). GRM/focus_grm → one row per presenter (author_initial = presenter).
-- material_id optionally links the summary to its slide file.
CREATE TABLE IF NOT EXISTS csnl_paper_rec.archive_meeting_summaries(
  summary_id        BIGSERIAL PRIMARY KEY,
  meeting_date      DATE NOT NULL,
  kind              TEXT NOT NULL CHECK (kind IN ('pb','grm','focus_grm')),
  author_initial    TEXT CHECK (author_initial IS NULL OR author_initial ~ '^[A-Z]{2,8}$'),
  paper_ref         TEXT,                     -- PB: APA / title / DOI of the paper; GRM: NULL
  title             TEXT,
  summary_text      TEXT NOT NULL,
  keywords          JSONB,                    -- optional ["...","..."]
  material_id       BIGINT REFERENCES csnl_paper_rec.archive_meeting_materials(material_id),
  source            TEXT NOT NULL DEFAULT 'claude-mcp',   -- claude-mcp|manual|...
  source_version    TEXT,                     -- 'summary-<batch>@<date>'
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_jsonb         JSONB,
  UNIQUE (meeting_date, kind, author_initial, paper_ref)
);
CREATE INDEX IF NOT EXISTS ix_meeting_summaries_date_kind
  ON csnl_paper_rec.archive_meeting_summaries (meeting_date, kind);

-- Plaud provenance (the summaries are produced by Plaud → land in the Notion
-- "📑 GRM Meeting Notes" DB → flowed here by a Claude MCP session). Additive
-- so an already-applied install upgrades idempotently.
ALTER TABLE csnl_paper_rec.archive_meeting_summaries
  ADD COLUMN IF NOT EXISTS plaud_file_id TEXT;
ALTER TABLE csnl_paper_rec.archive_meeting_summaries
  ADD COLUMN IF NOT EXISTS notion_url    TEXT;
ALTER TABLE csnl_paper_rec.archive_meeting_summaries
  ADD COLUMN IF NOT EXISTS key_decisions TEXT;
ALTER TABLE csnl_paper_rec.archive_meeting_summaries
  ADD COLUMN IF NOT EXISTS speakers      TEXT;

COMMENT ON TABLE csnl_paper_rec.archive_meeting_materials IS
  'P31: weekly NAS GRM/2026 slide-file index (pb|grm|focus_grm). '
  'UNIQUE(nas_path) idempotent. Populated by ingest_grm_nas.py. NAS read-only.';
COMMENT ON TABLE csnl_paper_rec.archive_meeting_summaries IS
  'P31: space for Claude-MCP-uploaded PB/GRM presentation summaries. PB row per '
  'researcher+paper; GRM row per presenter. Optional FK to a material slide.';

-- ----------------------------------------------------------- sanity rail
DO $$
DECLARE n INT;
BEGIN
  SELECT count(*) INTO n FROM information_schema.tables
   WHERE table_schema='csnl_paper_rec'
     AND table_name IN ('archive_meeting_materials','archive_meeting_summaries');
  IF n <> 2 THEN RAISE EXCEPTION 'P31: expected 2 tables, got %', n; END IF;
  RAISE NOTICE 'P31: archive_meeting_materials + archive_meeting_summaries present (csnl_paper_rec only).';
  RAISE NOTICE 'P31 migration verified.';
END$$;

COMMIT;
