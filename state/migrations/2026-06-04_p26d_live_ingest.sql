-- ===========================================================================
-- state/migrations/2026-06-04_p26d_live_ingest.sql
--
-- P26d — tag corpus rows by provenance so live-discovered (≤5y) papers added by
-- the discovery engine are identifiable and the ingest is REVERSIBLE.
--   archive_papers.source = 'archive'      (original frozen corpus, default)
--                         = 'live_search'  (P26 discovery, crawl.mjs ≤5y)
--
-- Reversal:  DELETE FROM csnl_paper_rec.archive_papers WHERE source='live_search';
--            (and the matching archive_paper_synopses rows by canonical_id)
--
-- Operator-run, idempotent.
--   ! python3 scripts/run_migration.py state/migrations/2026-06-04_p26d_live_ingest.sql
-- ===========================================================================

BEGIN;

ALTER TABLE csnl_paper_rec.archive_papers
  ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'archive';

CREATE INDEX IF NOT EXISTS ix_archive_papers_source
  ON csnl_paper_rec.archive_papers (source);

COMMENT ON COLUMN csnl_paper_rec.archive_papers.source IS
  'provenance: archive (original corpus) | live_search (P26 discovery, <=5y via '
  'crawl.mjs). Reversible: DELETE FROM archive_papers WHERE source=''live_search''.';

DO $$
DECLARE n INT; BEGIN
  SELECT count(*) INTO n FROM csnl_paper_rec.archive_papers WHERE source='live_search';
  RAISE NOTICE 'archive_papers.source column present; live_search rows now: %', n;
  RAISE NOTICE 'P26d migration verified';
END$$;

COMMIT;
