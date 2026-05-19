-- state/schema.sql — csnl-paper-rec-v2 PostgreSQL ledger DDL.
--
-- Ported from the predecessor sqlite schema (BUILD_SPEC.md §schema.sql),
-- same columns + semantics, with unit_id/member_init (already present in
-- the predecessor) carried forward. Applied by scripts/init_db.py, which
-- substitutes __SCHEMA__ with $CPR_LEDGER_SCHEMA (default csnl_paper_rec).
--
-- Idempotent: CREATE ... IF NOT EXISTS throughout. Timestamps are kept as
-- TEXT (ISO-8601 KST strings) to preserve byte-parity with the legacy
-- harness ledger during migration — never re-typed.
--
-- This schema is READ-WRITE. csnl_research is a DIFFERENT schema and is
-- strictly READ-ONLY (never referenced here).

CREATE SCHEMA IF NOT EXISTS __SCHEMA__;

CREATE TABLE IF NOT EXISTS __SCHEMA__.paper_recommendations(
  run_id       TEXT,
  unit_id      TEXT,
  member_init  TEXT,
  channel_id   TEXT,
  slack_ts     TEXT,
  paper_doi    TEXT,
  paper_title  TEXT,
  paper_date   TEXT,
  tier         TEXT,
  posted_at    TEXT,
  PRIMARY KEY (unit_id, paper_doi)
);

CREATE TABLE IF NOT EXISTS __SCHEMA__.recommendation_messages(
  id           TEXT PRIMARY KEY,
  channel_id   TEXT,
  message_ts   TEXT,
  unit_id      TEXT,
  paper_doi    TEXT,
  posted_at    TEXT,
  context_json TEXT,
  UNIQUE (channel_id, message_ts)
);

CREATE TABLE IF NOT EXISTS __SCHEMA__.paper_recommendations_read(
  unit_id        TEXT,
  member_init    TEXT,
  paper_doi      TEXT,
  paper_title    TEXT,
  marked_read_at TEXT
);

CREATE TABLE IF NOT EXISTS __SCHEMA__.feedback_events(
  id                TEXT PRIMARY KEY,
  occurred_at       TEXT,
  recommendation_doi TEXT,
  unit_id           TEXT,
  member_init       TEXT,
  signal            TEXT CHECK (signal IN
    ('thumbs_up','thumbs_down','thinking','thread_reply',
     'already_read','saved','cited')),
  payload_json      TEXT,
  idem_key          TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS __SCHEMA__.exclusion_rules(
  unit_id      TEXT,
  member_init  TEXT,
  excluded_term TEXT,
  reason       TEXT,
  declared_at  TEXT,
  source       TEXT,
  UNIQUE (unit_id, excluded_term)
);

-- Dedup read paths (rules/04). Helpful, non-unique indexes.
CREATE INDEX IF NOT EXISTS ix_pr_unit   ON __SCHEMA__.paper_recommendations(unit_id);
CREATE INDEX IF NOT EXISTS ix_prr_unit  ON __SCHEMA__.paper_recommendations_read(unit_id);
CREATE INDEX IF NOT EXISTS ix_excl_unit ON __SCHEMA__.exclusion_rules(unit_id);
