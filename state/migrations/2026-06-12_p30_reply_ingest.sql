-- ===========================================================================
-- state/migrations/2026-06-12_p30_reply_ingest.sql
--
-- P30 — reply-ingestion add-on over P28 survey memory.
--
-- ADDITIVE over P28 (2026-06-08_p28_survey_memory.sql). Adds exactly two things
-- so that researcher REPLIES (answers to the 26 "researcher-only" questions)
-- can be ingested concretely, idempotently, reversibly, and with supersede:
--   (1) archive_survey_answers — the proposal/audit ledger (raw reply →
--       proposed_update → applied/superseded/reverted), the single truth for
--       reply lifecycle + reversibility snapshot + supersede chain.
--   (2) archive_survey_negatives.exclude_mode — stores the conditional-exclude
--       policy (always | focus-only | comparison-allowed). DEFAULT 'always'
--       preserves the existing veto behaviour exactly; consumption wiring is a
--       P28b follow-up (the column is INERT for now).
--
-- BOUNDARY: new objects live in csnl_paper_rec ONLY. The P28 base 7 tables and
-- their rows are UNCHANGED (exclude_mode is an additive column with a default).
-- csnl_research / archive_responses are never touched. Idempotent; operator-run:
--   ! python3 scripts/run_migration.py state/migrations/2026-06-12_p30_reply_ingest.sql
-- Prereq: the P28 base migration must be applied first (guarded below).
-- ===========================================================================

BEGIN;

-- GUARD: P28 base must exist, else the ALTER below dies with "relation does not
-- exist". Fail loud and tell the operator what to run.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                 WHERE table_schema = 'csnl_paper_rec'
                   AND table_name   = 'archive_survey_negatives') THEN
    RAISE EXCEPTION 'P30 requires the P28 base. Run state/migrations/2026-06-08_p28_survey_memory.sql first.';
  END IF;
END$$;

-- ---------------------------------------------- archive_survey_answers (ledger)
-- One row per (researcher, question, distinct reply text). Tracks the whole
-- lifecycle. proposals-only staging + reversibility + supersede in one table;
-- no soft-delete column on the base tables — restoration is via prior_snapshot.
CREATE TABLE IF NOT EXISTS csnl_paper_rec.archive_survey_answers(
  answer_id        BIGSERIAL PRIMARY KEY,
  researcher_id    TEXT NOT NULL CHECK (researcher_id ~ '^[A-Z]{2,8}$'),
  question_id      TEXT NOT NULL,               -- 'BHL-Q7' (registry key)
  channel          TEXT NOT NULL CHECK (channel IN ('notion','email','manual')),
  raw_answer       TEXT,                        -- verbatim reply text
  raw_ref          JSONB,                       -- {page_id,block_id} | {email_msgid,lines}
  proposed_update  JSONB,                       -- {table,op,pk,set} deterministic patch
  prior_snapshot   JSONB,                       -- re-captured in the --apply tx
                                                -- (ingest_replies.py). Shapes:
                                                --   {"items":[{"pk":{..},"row":<row>|null}]}
                                                --     row=null  => confirmed-absent (delete on revert)
                                                --     row={..}  => full prior row (restore on revert)
                                                --   {} = operator_action (nothing to restore)
                                                --   {"restructure": {aims/keywords/models/pis:[..]}}
                                                --   {"_unread": true} = unknown -> REFUSES apply & revert
  target_table     TEXT,                        -- NULL = operator_action
  target_op        TEXT NOT NULL,               -- pi_upsert|aim_upsert|aim_delete
                                                --   |negative_upsert|keyword_upsert
                                                --   |keyword_delete|model_upsert|restructure
                                                --   |relevance_decision_reject|operator_action
  status           TEXT NOT NULL DEFAULT 'proposed'
                     CHECK (status IN ('proposed','staged','needs_review','applied',
                                       'superseded','rejected','reverted','noop')),
  confidence       TEXT CHECK (confidence IN ('high','medium','low')),
  content_hash     TEXT,                        -- sha1(raw_answer) — idempotency
  source_version   TEXT NOT NULL,               -- 'reply-r1@2026-06-12'
  needs_review     BOOLEAN DEFAULT FALSE,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  applied_at       TIMESTAMPTZ,
  superseded_by    BIGINT REFERENCES csnl_paper_rec.archive_survey_answers(answer_id),
  UNIQUE (researcher_id, question_id, content_hash)   -- same reply re-ingested = no-op
);
CREATE INDEX IF NOT EXISTS ix_survey_answers_rid_q
  ON csnl_paper_rec.archive_survey_answers (researcher_id, question_id, status);

-- --------------------------------------- archive_survey_negatives.exclude_mode
-- Conditional-exclude policy (Gap #3: BYL-Q7, JYK-Q2). DEFAULT 'always' keeps
-- every existing negatives row at its current veto behaviour. 'comparison-
-- allowed' / 'focus-only' consumption is a P28b follow-up — INERT for now.
ALTER TABLE csnl_paper_rec.archive_survey_negatives
  ADD COLUMN IF NOT EXISTS exclude_mode TEXT DEFAULT 'always'
    CHECK (exclude_mode IN ('always','focus-only','comparison-allowed'));

COMMENT ON TABLE csnl_paper_rec.archive_survey_answers IS
  'P30: reply-ingestion ledger. raw reply -> proposed_update -> applied/'
  'superseded/reverted. prior_snapshot (re-SELECTed in --apply tx) is the '
  'reversibility source; {} = confirmed-absent, {"_unread":true} = unknown '
  '(refuses apply/revert). UNIQUE(rid,question_id,content_hash) = idempotent.';
COMMENT ON COLUMN csnl_paper_rec.archive_survey_negatives.exclude_mode IS
  'P30: always|focus-only|comparison-allowed. DEFAULT always preserves veto. '
  'Consumption wiring is P28b follow-up; INERT now.';

-- ----------------------------------------------- sanity rail (additive proof)
DO $$
DECLARE n_always INT; n_other INT;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                 WHERE table_schema='csnl_paper_rec' AND table_name='archive_survey_answers')
  THEN RAISE EXCEPTION 'P30: archive_survey_answers missing'; END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_schema='csnl_paper_rec' AND table_name='archive_survey_negatives'
                   AND column_name='exclude_mode')
  THEN RAISE EXCEPTION 'P30: negatives.exclude_mode missing'; END IF;
  SELECT count(*) INTO n_always FROM csnl_paper_rec.archive_survey_negatives WHERE exclude_mode='always';
  SELECT count(*) INTO n_other  FROM csnl_paper_rec.archive_survey_negatives WHERE exclude_mode IS DISTINCT FROM 'always';
  RAISE NOTICE 'P30: % negatives @ exclude_mode=always (veto behaviour preserved), % non-always', n_always, n_other;
  RAISE NOTICE 'P30: exclude_mode consumption is INERT until P28b veto wiring (column only).';
  RAISE NOTICE 'P30 migration verified (new objects in csnl_paper_rec only).';
END$$;

COMMIT;
