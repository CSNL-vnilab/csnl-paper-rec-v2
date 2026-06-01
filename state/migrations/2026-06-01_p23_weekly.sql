-- ===========================================================================
-- state/migrations/2026-06-01_p23_weekly.sql
--
-- P23 — weekly Notion delivery harness. Three new objects in the read-write
-- ledger schema (csnl_paper_rec); nothing here touches csnl_research.
--
--   1. archive_researcher_channels  — per-researcher consent + delivery slot
--   2. archive_weekly_digests       — one row per (week, researcher, paper)
--   3. archive_paper_cooldown (view)— re-recommend suppression window
--
-- Operator-run, idempotent. Safe to re-run.
--
-- Run:
--    ! python3 scripts/run_migration.py state/migrations/2026-06-01_p23_weekly.sql
--    (or: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f <this file>)
--
-- Design source: docs/HARNESS-WEEKLY-DELIVERY-DESIGN.md §4 (tables/view).
--
-- Boundary notes:
-- * archive_responses is NEVER modified by this migration — the weekly
--   capture path UPSERTs into it with ON CONFLICT DO NOTHING (무손상
--   contract). These three objects are additive only.
-- * Type harmonization vs the design doc: researcher_id and canonical_id are
--   TEXT (not varchar(8)/varchar(64)) so they join cleanly to the rest of the
--   archive_* layer (archive_papers.canonical_id, archive_responses.* are all
--   TEXT). Postgres varchar(n) and TEXT are otherwise identical; the doc's
--   widths were illustrative.
-- * No cross-table FKs — the archive_* layer deliberately avoids them
--   (archive_responses has no FK to archive_papers either). build_digest.py is
--   the sole writer of archive_weekly_digests and only inserts canonical_ids
--   that came out of archive_researcher_queues, so referential integrity is
--   enforced in application code, consistent with the existing layer.
-- ===========================================================================

BEGIN;

-- ------------------------------------------------ 1. researcher_channels (§4-1)
-- One row per researcher who has CONSENTED to automatic weekly recommendations.
-- No row → that researcher is silently excluded from build_digest. consented_at
-- is the explicit opt-in timestamp (security gate, design §8).
CREATE TABLE IF NOT EXISTS csnl_paper_rec.archive_researcher_channels(
  researcher_id     TEXT        NOT NULL
                                  CHECK (researcher_id ~ '^[A-Z]{2,8}$'),
  channel_type      TEXT        NOT NULL DEFAULT 'notion'
                                  CHECK (channel_type IN ('notion')),
  channel_target    TEXT        NOT NULL,   -- Notion Select value for this
                                            -- researcher (== researcher_id by
                                            -- default; the "Researcher" prop).
  delivery_dow      SMALLINT    NOT NULL DEFAULT 1    -- 0=Sun .. 1=Mon ..
                                  CHECK (delivery_dow BETWEEN 0 AND 6),
  delivery_hour_kst SMALLINT    NOT NULL DEFAULT 9
                                  CHECK (delivery_hour_kst BETWEEN 0 AND 23),
  enabled           BOOLEAN     NOT NULL DEFAULT TRUE,
  consented_at      TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (researcher_id, channel_type)
);

COMMENT ON TABLE csnl_paper_rec.archive_researcher_channels IS
  'P23: per-researcher weekly-delivery consent + slot. No row => excluded. '
  'channel_type is notion-only for P23; future channels need a CHECK migration.';

-- ------------------------------------------------ 2. weekly_digests (§4-2)
-- One row per paper sent (or staged for send) in a given ISO week. The UNIQUE
-- (week_iso, researcher_id, canonical_id) lets build_digest.py be idempotent
-- across re-runs within the same week. response_choice is NULL while pending,
-- one of the three real choices once captured from Notion, or 'expired' once
-- the cooldown sweep marks an unanswered row.
CREATE TABLE IF NOT EXISTS csnl_paper_rec.archive_weekly_digests(
  digest_id        BIGSERIAL    PRIMARY KEY,
  week_iso         TEXT         NOT NULL
                                  CHECK (week_iso ~ '^[0-9]{4}-W[0-9]{2}$'),
  researcher_id    TEXT         NOT NULL
                                  CHECK (researcher_id ~ '^[A-Z]{2,8}$'),
  canonical_id     TEXT         NOT NULL,
  tier_at_send     CHAR(1)      NOT NULL
                                  CHECK (tier_at_send IN ('S','A','B','C')),
  rank_in_digest   SMALLINT     NOT NULL,           -- 1..N within the digest
  notion_page_id   TEXT,                            -- set by send_notion.py
  sent_at          TIMESTAMPTZ  NOT NULL,           -- staged-at (build) time
  response_choice  TEXT
                     CHECK (response_choice IS NULL OR response_choice IN
                       ('save_later','not_relevant','already_read','expired')),
  response_at      TIMESTAMPTZ,
  UNIQUE (week_iso, researcher_id, canonical_id)
);
CREATE INDEX IF NOT EXISTS ix_archive_weekly_digests_rid_choice
  ON csnl_paper_rec.archive_weekly_digests (researcher_id, response_choice);
CREATE INDEX IF NOT EXISTS ix_archive_weekly_digests_page
  ON csnl_paper_rec.archive_weekly_digests (notion_page_id);
-- capture_responses.py polls "pending and recently sent" — partial index keeps
-- that scan tight as the table grows week over week.
CREATE INDEX IF NOT EXISTS ix_archive_weekly_digests_pending
  ON csnl_paper_rec.archive_weekly_digests (sent_at)
  WHERE response_choice IS NULL;

COMMENT ON TABLE csnl_paper_rec.archive_weekly_digests IS
  'P23: per-(week, researcher, paper) weekly recommendation row. '
  'response_choice NULL=pending, expired=cooldown sweep, else captured. '
  'archive_responses is the permanent truth source; this table is the '
  'delivery + cooldown ledger only.';

-- ------------------------------------------------ 3. cooldown view (§4-3)
-- Re-recommend suppression: a (researcher, paper) is "in cooldown" for 8 weeks
-- after the last time it was SENT BUT NOT GENUINELY ANSWERED — i.e. still
-- pending (response_choice IS NULL) or expired. Once a researcher gives a real
-- choice it lands in archive_responses and build_digest excludes it
-- permanently via NOT EXISTS, so it never needs the cooldown window.
--
-- NOTE (spec correction): design §4-3 wrote
--     WHERE response_choice IN (NULL, 'expired')
-- which in SQL never matches NULL (x = NULL is unknown, not true) and would
-- collapse to `= 'expired'` — dropping the most common case (a freshly-sent,
-- not-yet-answered paper) out of cooldown and letting it be re-sent next week.
-- The correct predicate is the explicit IS NULL OR = 'expired' below.
CREATE OR REPLACE VIEW csnl_paper_rec.archive_paper_cooldown AS
SELECT researcher_id,
       canonical_id,
       MAX(sent_at) AS last_sent_at
FROM csnl_paper_rec.archive_weekly_digests
WHERE response_choice IS NULL
   OR response_choice = 'expired'
GROUP BY researcher_id, canonical_id;

COMMENT ON VIEW csnl_paper_rec.archive_paper_cooldown IS
  'P23: last send time per (researcher, paper) among unanswered/expired rows. '
  'build_digest.py excludes candidates with last_sent_at within the cooldown '
  'window (default 8 weeks). Answered papers are excluded via archive_responses '
  'instead, so they are intentionally absent from this view.';

-- ------------------------------------------------ 4. belief_due flags (durable)
-- capture_responses.py persists a row here (no LLM) whenever a researcher's
-- cumulative archive_responses count crosses a multiple of 10, so an unattended
-- cron capture that crosses the boundary leaves a durable, machine-readable
-- signal for the operator to run the belief update + queue rebuild (design §9).
-- This is the 4th P23 object (acceptance §11: "4 개 새 테이블/뷰").
CREATE TABLE IF NOT EXISTS csnl_paper_rec.archive_weekly_belief_due(
  id                BIGSERIAL    PRIMARY KEY,
  researcher_id     TEXT         NOT NULL
                                  CHECK (researcher_id ~ '^[A-Z]{2,8}$'),
  at_response_count INTEGER      NOT NULL,
  flagged_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
  resolved          BOOLEAN      NOT NULL DEFAULT FALSE,
  resolved_at       TIMESTAMPTZ,
  UNIQUE (researcher_id, at_response_count)   -- one flag per 10-multiple crossing
);
CREATE INDEX IF NOT EXISTS ix_archive_belief_due_unresolved
  ON csnl_paper_rec.archive_weekly_belief_due (researcher_id)
  WHERE resolved = FALSE;

COMMENT ON TABLE csnl_paper_rec.archive_weekly_belief_due IS
  'P23: durable belief-update-due flags. Written by capture_responses.py (no '
  'LLM); operator queries unresolved rows, runs the belief update + '
  'build_researcher_queue.py --apply, then marks resolved=true.';

-- ------------------------------------------------ sanity rails (NOTICE only)
DO $$
DECLARE
  n_ch   INT;
  n_dg   INT;
  n_bd   INT;
  has_v  BOOL;
BEGIN
  SELECT count(*) INTO n_ch FROM csnl_paper_rec.archive_researcher_channels;
  SELECT count(*) INTO n_dg FROM csnl_paper_rec.archive_weekly_digests;
  SELECT count(*) INTO n_bd FROM csnl_paper_rec.archive_weekly_belief_due;
  SELECT EXISTS(
    SELECT 1 FROM pg_views
     WHERE schemaname = 'csnl_paper_rec'
       AND viewname  = 'archive_paper_cooldown'
  ) INTO has_v;

  RAISE NOTICE 'archive_researcher_channels rows: %', n_ch;
  RAISE NOTICE 'archive_weekly_digests rows: %', n_dg;
  RAISE NOTICE 'archive_weekly_belief_due rows: %', n_bd;
  IF NOT has_v THEN
    RAISE EXCEPTION 'archive_paper_cooldown view was not created';
  END IF;
  RAISE NOTICE 'P23 migration verified: 3 tables + 1 view present';
END$$;

COMMIT;
