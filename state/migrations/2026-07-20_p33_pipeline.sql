-- ===========================================================================
-- state/migrations/2026-07-20_p33_pipeline.sql
--
-- P33 — ingest-pipeline hardening core (Track A migration; MF-11/MF-1/MF-6, B1).
--
-- Three additive, idempotent objects that let the LIVE recommendation path and
-- the operator-gated discovery track coexist without a parallel stack:
--
--   (1) archive_discovery_watermark — the fetch cursor for the FETCH-ONLY
--       discovery track (fetch_new_papers.py). One row per (source, query_hash).
--       Cursor keys on the source's INGEST/INDEX date (Crossref from-index-date,
--       arXiv submittedDate, OpenAlex from_created_date) via last_index_date, and
--       ALSO stores the event date (last_event_date) so a late-indexed but
--       old-dated paper is still fetched. last_success_at gates the tri-state
--       "advance only on a non-exception run" rule (B1 / MF-3). Nothing here
--       connects to prod; the watermark is read/advanced by the operator-run
--       discovery step, never by the weekly cron.
--
--   (2) archive_researcher_queues.builder — makes the AUTHORITATIVE builder
--       explicit (MF-1). build_researcher_queue.py stamps 'brq' (the builder
--       build_digest reads); recommend.py's parked P28 path stamps 'p28'. Each
--       builder then prunes ONLY its own rows. A static column DEFAULT is
--       insufficient because existing rows predate the column, so an idempotent
--       backfill sets the historical rows to 'brq' (they were produced by
--       build_researcher_queue.py).
--
--   (3) idx_archive_papers_title_norm — a NON-UNIQUE index on md5(title_norm)
--       (MF-6/MF-A). Hashing to a fixed 32 bytes keeps a long/CJK title_norm
--       under the Postgres btree ~2704-byte/row ceiling, so CREATE INDEX can
--       never abort the whole transaction (schema_archive.sql:46-51 prescribes
--       exactly this md5/substring form). UNIQUE is FORBIDDEN: title_norm is
--       intentionally shared across preprint<->published twins that same_work()
--       collapses — a UNIQUE constraint would reject legitimate rows.
--
-- BOUNDARY: the read-only research schema is never touched. archive_responses
-- and the filled survey pages are read-only truth — nothing here writes them.
-- Additive only; every object guarded by IF NOT EXISTS / ADD COLUMN IF NOT
-- EXISTS so re-application is a no-op. Every statement is csnl_paper_rec.-
-- qualified and lives inside one transaction. Operator-run:
--   ! python3 scripts/run_migration.py state/migrations/2026-07-20_p33_pipeline.sql
--
-- Reverse (operator-run, new objects ONLY — never the existing tables/data):
--   remove table  csnl_paper_rec.archive_discovery_watermark
--   remove column csnl_paper_rec.archive_researcher_queues.builder
--   remove index  csnl_paper_rec.idx_archive_papers_title_norm
-- ===========================================================================

BEGIN;

-- ------------------------------------------------------- discovery watermark
-- The fetch cursor for the operator-gated, FETCH-ONLY discovery track. One row
-- per (source, query_hash): source is the adapter name ('crossref','arxiv',
-- 'openalex','europepmc','biorxiv','medrxiv','pubmed','doaj','semantic'),
-- query_hash pins the specific query/profile slice so distinct scouts advance
-- independent cursors. last_index_date is the primary cursor (the source's
-- ingest/index date); last_event_date is the max publication/event date seen,
-- kept so a late-INDEXED but old-DATED paper is still fetched on the next run.
-- last_success_at records the last exception-free advance (the tri-state
-- "persist-then-advance, only on a non-exception run" rail — B1 / MF-3).
CREATE TABLE IF NOT EXISTS csnl_paper_rec.archive_discovery_watermark(
  source           TEXT NOT NULL,          -- adapter name
  query_hash       TEXT NOT NULL,          -- stable hash of the query/profile slice
  last_event_date  DATE,                   -- max publication/event date seen
  last_index_date  DATE,                   -- primary cursor: source ingest/index date
  last_success_at  TIMESTAMPTZ,            -- last exception-free advance (tri-state gate)
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (source, query_hash)
);

-- ------------------------------------------------------ authoritative builder
-- Provenance of each queue row's producer (MF-1). build_researcher_queue.py
-- writes 'brq' (build_digest's authoritative source); recommend.py's parked
-- P28 path writes 'p28'. Each builder prunes ONLY its own rows (WHERE builder=
-- <own> AND build_token<>...) AND each UPSERT's DO UPDATE is guarded by
-- WHERE builder=EXCLUDED.builder, so a conflict on the shared PK
-- (researcher_id,canonical_id) never flips a row to the other builder (MF-B).
-- Net: the two coexist for NON-overlapping (rid,cid); an overlapping row keeps
-- its current owner (the other builder's insert becomes a no-op).
ALTER TABLE csnl_paper_rec.archive_researcher_queues
  ADD COLUMN IF NOT EXISTS builder TEXT DEFAULT 'brq';

-- Belt-and-suspenders backfill. On a FRESH add, `DEFAULT 'brq'` already fills
-- existing rows and every future stray insert (a NULL-builder row would be
-- invisible: unmatched by the DO UPDATE WHERE builder=EXCLUDED.builder guard,
-- unprunable by WHERE builder='brq', unread by build_digest — so a safe default
-- is important). This UPDATE additionally covers the case where the column
-- ALREADY existed as NULL from a prior partial run (then IF NOT EXISTS skips the
-- ADD, so the default is not applied retroactively). Re-runs are no-ops.
UPDATE csnl_paper_rec.archive_researcher_queues
  SET builder = 'brq'
  WHERE builder IS NULL;

-- --------------------------------------------------- title_norm lookup index
-- NON-UNIQUE index on md5(title_norm) (MF-A): hashing to a fixed 32 bytes keeps
-- a long/CJK title under the btree per-row byte ceiling so CREATE INDEX can
-- never abort the whole transaction. A future equality lookup uses it via
-- WHERE md5(title_norm) = md5($1). UNIQUE is FORBIDDEN: title_norm is
-- deliberately shared across preprint<->published twins that same_work()
-- collapses in Python; a UNIQUE index would reject valid rows.
CREATE INDEX IF NOT EXISTS idx_archive_papers_title_norm
  ON csnl_paper_rec.archive_papers (md5(title_norm));

-- ----------------------------------------------------------------- comments
COMMENT ON TABLE csnl_paper_rec.archive_discovery_watermark IS
  'P33: fetch cursor for the operator-gated FETCH-ONLY discovery track. One '
  'row per (source, query_hash); last_index_date is the primary cursor, '
  'last_event_date keeps late-indexed old-dated papers fetchable, '
  'last_success_at gates the tri-state advance. NOT read by the weekly cron.';
COMMENT ON COLUMN csnl_paper_rec.archive_researcher_queues.builder IS
  'P33: authoritative builder id — brq (build_researcher_queue, LIVE) or p28 '
  '(recommend, parked). build_digest reads builder=brq; each builder prunes '
  'only its own rows.';
COMMENT ON INDEX csnl_paper_rec.idx_archive_papers_title_norm IS
  'P33: NON-UNIQUE btree on title_norm for same-work / title_norm-aware '
  'exclusion lookups. UNIQUE forbidden — title_norm is shared across twins.';

COMMIT;
