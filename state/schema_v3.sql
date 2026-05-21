-- state/schema_v3.sql — v3 cron pipeline schema extensions.
-- Idempotent; applied by scripts/init_db_v3.py (or re-run scripts/init_db.py
-- which now applies both base + v3). Schema templated via __SCHEMA__ token
-- substituted with $CPR_LEDGER_SCHEMA (default csnl_paper_rec).

-- Per (cycle_id, member_init) state machine. Drives cron_tick.py.
CREATE TABLE IF NOT EXISTS __SCHEMA__.cycle_state(
  cycle_id        TEXT,    -- 'YYYYMMDD' (the Friday date of cycle start)
  member_init     TEXT,
  unit_id         TEXT,
  state           TEXT CHECK (state IN
    ('pending_send','awaiting_initial_reply','reminded',
     'awaiting_decision','decided','passed','timeout','no_rec')),
  rid             TEXT,    -- the run_id of this cycle's active rec
  paper_doi       TEXT,    -- the recommended paper this cycle
  paper_title     TEXT,
  picked_alt_doi  TEXT,    -- if researcher picked an alternate; null otherwise
  reply_count     INTEGER DEFAULT 0,
  last_action_at  TEXT,    -- ISO KST timestamp of last cron-fired action
  next_action_at  TEXT,    -- ISO KST when cron should next consider this row
  last_reply_ts   TEXT,    -- highest Slack ts processed (idempotency for replies)
  notes           TEXT,
  PRIMARY KEY (cycle_id, member_init)
);

-- Audit log of rule-based evolutions applied at end-of-cycle.
CREATE TABLE IF NOT EXISTS __SCHEMA__.evolution_log(
  id            TEXT PRIMARY KEY,
  applied_at    TEXT,
  cycle_id      TEXT,
  change_type   TEXT,    -- 'exclusion_keyword'|'read_doi'|'query_seed_drop'|'criteria'
  unit_id       TEXT,
  detail_json   TEXT,    -- the before/after, and the supporting feedback rows
  source        TEXT     -- 'feedback' | 'silence_pattern' | 'manual'
);

CREATE INDEX IF NOT EXISTS ix_cycle_state_state ON __SCHEMA__.cycle_state(state);
CREATE INDEX IF NOT EXISTS ix_cycle_state_cycle ON __SCHEMA__.cycle_state(cycle_id);
CREATE INDEX IF NOT EXISTS ix_evol_cycle        ON __SCHEMA__.evolution_log(cycle_id);
