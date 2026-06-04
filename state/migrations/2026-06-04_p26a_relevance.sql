-- ===========================================================================
-- state/migrations/2026-06-04_p26a_relevance.sql
--
-- P26a — relevance-decision ledger for the aim/phenomenon/mechanism-based
-- discovery engine (docs/HARNESS-DISCOVERY-DESIGN.md §6).
--
-- One row per (researcher, paper) relevance judgement by the reasoning gate:
--   relevance_type  A = aim connection
--                   B = discovered-phenomenon connection
--                   C = shared mechanism / computational theory
--                   none = not relevant (rejected — NEVER for species/method/subject)
--   reason          one-line scientific reason (auditable)
--   source          archive | live  (live = found by crawl.mjs search, not the
--                                     frozen corpus)
--
-- Decisions are CACHED here so the gate never re-judges a paper unless the
-- researcher's profile changes (cost control). archive_responses (the
-- researcher's own choices + reasons) stays the held-out calibration truth and
-- is NEVER written by this layer.
--
-- Operator-run, idempotent.
--   ! python3 scripts/run_migration.py state/migrations/2026-06-04_p26a_relevance.sql
-- ===========================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS csnl_paper_rec.archive_relevance_decisions(
  researcher_id   TEXT        NOT NULL
                                CHECK (researcher_id ~ '^[A-Z]{2,8}$'),
  canonical_id    TEXT        NOT NULL,
  relevance_type  TEXT        NOT NULL
                                CHECK (relevance_type IN ('A','B','C','none')),
  reason          TEXT,
  source          TEXT        NOT NULL DEFAULT 'archive'
                                CHECK (source IN ('archive','live')),
  confidence      TEXT        CHECK (confidence IN ('low','medium','high')),
  scout_version   TEXT        NOT NULL,
  decided_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (researcher_id, canonical_id)
);
CREATE INDEX IF NOT EXISTS ix_archive_relevance_rid_type
  ON csnl_paper_rec.archive_relevance_decisions (researcher_id, relevance_type);
CREATE INDEX IF NOT EXISTS ix_archive_relevance_source
  ON csnl_paper_rec.archive_relevance_decisions (source);

COMMENT ON TABLE csnl_paper_rec.archive_relevance_decisions IS
  'P26: reasoning-gate relevance judgements. relevance_type A=aim / B=phenomenon '
  '/ C=mechanism-or-theory / none=reject (never for species/method/subject). '
  'Cached to avoid re-judging; archive_responses stays the held-out truth.';

DO $$
DECLARE n INT; BEGIN
  SELECT count(*) INTO n FROM csnl_paper_rec.archive_relevance_decisions;
  RAISE NOTICE 'archive_relevance_decisions rows: %', n;
  RAISE NOTICE 'P26a migration verified: relevance-decision table present';
END$$;

COMMIT;
