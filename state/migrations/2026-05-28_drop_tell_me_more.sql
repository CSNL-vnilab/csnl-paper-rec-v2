-- ===========================================================================
-- state/migrations/2026-05-28_drop_tell_me_more.sql
--
-- Drop the 'tell_me_more' choice (the 4th MCQ option "더 자세히 소개해줘")
-- from csnl_paper_rec.archive_responses.
--
-- Rationale: the deep-dive explainer agent that 'tell_me_more' triggered
-- duplicated work the P21 synopsis layer now does inline inside Stage 2
-- Block 2 (paper-derived findings + framework matching in the Korean
-- recommendation rationale). Keeping a 4th MCQ option without a distinct
-- downstream signal added turn-length without producing usable labels.
--
-- Operator-run, idempotent. Safe to re-run.
--
-- Run:
--    ! psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
--           -f state/migrations/2026-05-28_drop_tell_me_more.sql
--
-- After this migration:
--   - existing rows with choice='tell_me_more' are converted to
--     choice='skipped' with detail_json carrying the legacy marker
--     (so analytics can still audit them as a separate cohort);
--   - CHECK constraint no longer accepts 'tell_me_more' for new inserts;
--   - archive_paper_status view loses the maybe_interested case branch.
--
-- Code-side companion changes ship in the same commit:
--   plugin/scripts/record_choice.py        — _VALID set updated
--   plugin/skills/...interview/SKILL.md    — MCQ block is 3 options
--   plugin/agents/belief-updater.md        — tell_me_more rule removed
--   scripts/archive/list_status.py         — label dropped
--   scripts/archive/validate_drift.py      — precision formula updated
--   docs/RESEARCHER-GUIDE.md               — table simplified to 3 options
-- ===========================================================================

BEGIN;

-- 1. Audit BEFORE the migration. The lab's live count (2026-05-28) is 0
--    across all 5 researchers — list_status.py confirms zero "더 알아볼
--    만함" rows. We HARD ABORT on any nonzero count so the operator
--    can examine the rows first instead of having them silently rewritten.
--    To force the rewrite anyway, set `migration.force_rewrite_tell_me_more`
--    via `SET LOCAL` before running, e.g.:
--        BEGIN; SET LOCAL migration.force_rewrite_tell_me_more = 'yes';
--        \i state/migrations/2026-05-28_drop_tell_me_more.sql
--    (codex adversarial review finding #6 — MEDIUM)
DO $$
DECLARE
    n_to_rewrite INT;
    forced TEXT;
BEGIN
    SELECT count(*) INTO n_to_rewrite
      FROM csnl_paper_rec.archive_responses
     WHERE choice = 'tell_me_more';
    BEGIN
        forced := current_setting('migration.force_rewrite_tell_me_more');
    EXCEPTION WHEN undefined_object THEN
        forced := NULL;
    END;
    RAISE NOTICE 'tell_me_more rows to be rewritten as skipped: %', n_to_rewrite;
    IF n_to_rewrite > 0 AND COALESCE(forced, '') <> 'yes' THEN
        RAISE EXCEPTION
          'expected 0 tell_me_more rows, found %; review the cohort before '
          'rerunning. Set migration.force_rewrite_tell_me_more=yes to override.',
          n_to_rewrite;
    END IF;
END$$;

-- 2. Rewrite legacy rows to 'skipped' with a deprecation marker in
--    choice_detail so we can audit later. UPSERT-safe because (researcher_id,
--    canonical_id) is the PK; we update in place.
--    Merge guard (codex finding #5 — LOW): JSONB `||` overwrites
--    duplicate keys. If a row already has `_migrated_from` from a prior
--    partial run, we preserve the original value rather than overwriting it.
UPDATE csnl_paper_rec.archive_responses
   SET choice        = 'skipped',
       choice_detail = CASE
         WHEN choice_detail ? '_migrated_from'
           THEN choice_detail   -- already migrated; do not overwrite the original marker
         ELSE COALESCE(choice_detail, '{}'::jsonb) ||
              jsonb_build_object(
                '_migrated_from', 'tell_me_more',
                '_migrated_at',   to_char(now() AT TIME ZONE 'Asia/Seoul',
                                          'YYYY-MM-DD"T"HH24:MI:SS+09:00')
              )
       END
 WHERE choice = 'tell_me_more';

-- 3. Recreate the CHECK constraint without 'tell_me_more'. Postgres has no
--    "ALTER CONSTRAINT" for CHECK bodies — we have to drop and re-add. The
--    constraint may exist under any of three names depending on when the
--    table was first created:
--      (a) auto-generated `<table>_choice_check` from the original CREATE
--      (b) any constraint whose definition still mentions 'tell_me_more'
--      (c) the canonical `archive_responses_choice_check` from a prior run
--          of THIS migration (re-run case — codex finding #14, HIGH)
--    We drop all three branches before adding the new one. This keeps the
--    migration safe to re-run after the first successful pass.
DO $$
DECLARE
    cname TEXT;
    choice_attnum SMALLINT;
BEGIN
    -- Resolve the attnum of the `choice` column so we can match constraints
    -- by their attached column too (not just by deparse text). This avoids
    -- relying on Postgres preserving the literal 'tell_me_more' in the
    -- constraint definition. (codex finding #1 — LOW)
    SELECT attnum INTO choice_attnum
      FROM pg_attribute
     WHERE attrelid = 'csnl_paper_rec.archive_responses'::regclass
       AND attname  = 'choice'
       AND NOT attisdropped;

    -- Branch (b): any CHECK on the choice column, regardless of name,
    -- whose definition mentions tell_me_more.
    FOR cname IN
        SELECT conname FROM pg_constraint
         WHERE conrelid = 'csnl_paper_rec.archive_responses'::regclass
           AND contype  = 'c'
           AND (
             pg_get_constraintdef(oid) ILIKE '%tell_me_more%'
             OR (choice_attnum IS NOT NULL AND choice_attnum = ANY(conkey))
           )
           AND conname <> 'archive_responses_choice_check'  -- canonical name handled below
    LOOP
        EXECUTE format(
          'ALTER TABLE csnl_paper_rec.archive_responses DROP CONSTRAINT %I',
          cname
        );
    END LOOP;
END$$;

-- Branch (a) + (c): drop the canonical name if it exists from any prior run.
ALTER TABLE csnl_paper_rec.archive_responses
  DROP CONSTRAINT IF EXISTS archive_responses_choice_check;

ALTER TABLE csnl_paper_rec.archive_responses
  ADD CONSTRAINT archive_responses_choice_check
  CHECK (choice IN ('save_later','not_relevant','already_read','skipped'));

-- 4. Recreate the archive_paper_status view via CREATE OR REPLACE so any
--    concurrent reader of the view never sees a window where the relation
--    is missing. Projected columns/types are unchanged, so REPLACE is
--    legal. The comment is updated separately via COMMENT ON.
--    (codex adversarial review finding #3 — LOW; tightened.)
CREATE OR REPLACE VIEW csnl_paper_rec.archive_paper_status AS
SELECT
  r.researcher_id,
  r.canonical_id,
  CASE r.choice
    WHEN 'save_later'    THEN 'to_read'
    WHEN 'already_read'  THEN 'read'
    WHEN 'not_relevant'  THEN 'not_interested'
    WHEN 'skipped'       THEN 'skipped'
    ELSE r.choice
  END AS paper_status,
  r.session_id,
  r.responded_at,
  r.choice_detail
FROM csnl_paper_rec.archive_responses r;

COMMENT ON VIEW csnl_paper_rec.archive_paper_status IS
  'P19e: per-(researcher, paper) status derived from archive_responses. '
  'Plain-Korean terms: read / to_read / not_interested / skipped. '
  '(maybe_interested retired 2026-05-28.)';

-- 5. Post-condition sanity rail: no row should remain at tell_me_more, and
--    the new CHECK should be in place. Both are verified by running a
--    SELECT inside the same transaction. If either fires, the transaction
--    aborts and rolls back the migration cleanly.
DO $$
DECLARE
    leftover INT;
    check_present BOOL;
BEGIN
    SELECT count(*) INTO leftover
      FROM csnl_paper_rec.archive_responses
     WHERE choice = 'tell_me_more';
    IF leftover <> 0 THEN
        RAISE EXCEPTION 'migration left % tell_me_more rows in archive_responses', leftover;
    END IF;

    SELECT EXISTS(
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'csnl_paper_rec.archive_responses'::regclass
           AND contype  = 'c'
           AND pg_get_constraintdef(oid) ILIKE '%skipped%'
           AND pg_get_constraintdef(oid) NOT ILIKE '%tell_me_more%'
    ) INTO check_present;
    IF NOT check_present THEN
        RAISE EXCEPTION 'new CHECK constraint not found on archive_responses';
    END IF;
    RAISE NOTICE 'migration verified: tell_me_more retired cleanly';
END$$;

COMMIT;
