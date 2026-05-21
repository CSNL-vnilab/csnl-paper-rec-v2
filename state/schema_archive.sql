-- state/schema_archive.sql — csnl-paper-rec archive layer (P13).
--
-- Reference data + retrieval scaffold for the marketplace-plugin interview
-- flow. Lives in the SAME read-write schema as the ledger (__SCHEMA__,
-- default csnl_paper_rec). All tables are prefixed `archive_` so they are
-- visually distinct from the existing ledger tables and can be moved into
-- their own schema later without code surgery.
--
-- Idempotent. Applied by scripts/init_db.py (templated __SCHEMA__).
--
-- Boundary notes:
-- * csnl_research stays READ-ONLY. Nothing here writes to it.
-- * Embeddings are stored as JSONB (a JSON array of floats) so the schema
--   is portable across Postgres setups without pgvector. The queue builder
--   does cosine sim in Python at write time; the plugin only ever reads
--   archive_researcher_queues. If the operator later enables pgvector, the
--   embedding_json column can be backfilled into a VECTOR(dim) column.

-- ------------------------------------------------------------------ papers
-- Canonical merged record. canonical_id is the **first 32 hex characters
-- (128 bits)** of sha256(key); see scripts/archive/_common.py canonical_id().
--   key = "doi:<normalized_doi>"             when DOI is known, OR
--         "ttl:<norm_title>|<year_or_blank>"  otherwise.
-- The 32-hex prefix is intentional for cheap indexes; collision risk
-- across ≤1e5 papers is negligible (<1e-29 per pair). Operators wishing
-- to migrate to the full 64-hex digest later must rewrite this column
-- and every FK in archive_*.
CREATE TABLE IF NOT EXISTS __SCHEMA__.archive_papers(
  canonical_id     TEXT PRIMARY KEY,
  doi              TEXT,                -- normalized lower-case, no prefix
  title            TEXT,
  title_norm       TEXT,                -- lowercased, alnum-only (for fuzz)
  authors_json     JSONB,               -- ["Doe, J.", ...]
  venue            TEXT,
  year             INTEGER,
  pub_date         TEXT,                -- ISO YYYY-MM-DD if known
  is_preprint      BOOLEAN DEFAULT FALSE,
  abstract         TEXT,
  page_count       INTEGER,             -- from PDF when available (textbook signal)
  pdf_path         TEXT,                -- local SMB path when applicable
  first_seen_at    TEXT NOT NULL,       -- ISO KST
  last_updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_archive_papers_doi   ON __SCHEMA__.archive_papers(doi);
CREATE INDEX IF NOT EXISTS ix_archive_papers_year  ON __SCHEMA__.archive_papers(year);
-- Note: title_norm has no direct btree index. The norm preserves Hangul/Han
-- characters which are 3 bytes/UTF-8; a multilingual title easily exceeds
-- the Postgres btree v4 limit of 2704 bytes per index row. The merge step
-- does its fuzz-match in Python after loading JSONL, so no SQL-side index
-- is needed. If lookups by exact title_norm become hot later, use an
-- expression index like md5(title_norm) or a partial substring index.

-- ----------------------------------------------------------- paper_sources
-- Provenance — which source contributed each paper, with raw payload.
-- One canonical paper may come from multiple sources.
CREATE TABLE IF NOT EXISTS __SCHEMA__.archive_paper_sources(
  canonical_id     TEXT,
  source           TEXT CHECK (source IN ('classics_smb','cwll_rec_log','pi_network')),
  source_ref       TEXT,                -- filename | csv-row-id | PI display_name
  source_payload   JSONB,               -- raw extracted fields, untouched
  observed_at      TEXT NOT NULL,
  PRIMARY KEY (canonical_id, source, source_ref)
);
CREATE INDEX IF NOT EXISTS ix_archive_paper_sources_canon ON __SCHEMA__.archive_paper_sources(canonical_id);

-- ------------------------------------------------------- filter_decisions
-- Outcome of the rule-based filter pass. is_lab_relevant gates the
-- queue-builder — false rows stay in the archive but never reach a
-- researcher recommendation queue.
CREATE TABLE IF NOT EXISTS __SCHEMA__.archive_filter_decisions(
  canonical_id     TEXT PRIMARY KEY,
  is_textbook      BOOLEAN DEFAULT FALSE,
  is_draft         BOOLEAN DEFAULT FALSE,
  is_poster        BOOLEAN DEFAULT FALSE,
  is_lab_relevant  BOOLEAN DEFAULT TRUE,
  lab_scope_tags   JSONB,               -- ["BDM","NN",...] from keyword bag
  filter_reason    JSONB,               -- {textbook:"page_count=812", ...}
  decided_at       TEXT NOT NULL
);

-- ---------------------------------------------------------- embeddings
-- One row per canonical paper × embedding model. Plugin never reads this
-- directly; only the queue-builder consumes it.
CREATE TABLE IF NOT EXISTS __SCHEMA__.archive_paper_embeddings(
  canonical_id     TEXT,
  model_name       TEXT,                -- e.g. 'BAAI/bge-m3'
  dim              INTEGER,
  embedding_json   JSONB,               -- array of floats, length = dim
  generated_at     TEXT NOT NULL,
  PRIMARY KEY (canonical_id, model_name)
);

-- ------------------------------------------------------- researcher_queues
-- Per-researcher pre-computed ranked queue, split into 3 chunks.
--   chunk: 'recent' (≤5y) | 'mid' (5–10y) | 'classic' (>10y)
-- The plugin paginates through this in chunk-then-rank order.
--
-- `build_token` is a UUID stamped once per build run; the queue builder
-- prunes stale rows by `build_token != <new>` instead of by `built_at`
-- (a second-resolution timestamp can collide across concurrent builds).
CREATE TABLE IF NOT EXISTS __SCHEMA__.archive_researcher_queues(
  researcher_id    TEXT,
  canonical_id     TEXT,
  chunk            TEXT CHECK (chunk IN ('recent','mid','classic')),
  rank_in_chunk    INTEGER,
  similarity       REAL,                -- cosine sim of researcher↔paper
  built_at         TEXT NOT NULL,
  build_token      TEXT,                -- UUID per build run; see queue builder
  PRIMARY KEY (researcher_id, canonical_id)
);
-- Idempotent migration for installs that have archive_researcher_queues
-- without the build_token column.
ALTER TABLE __SCHEMA__.archive_researcher_queues
  ADD COLUMN IF NOT EXISTS build_token TEXT;
CREATE INDEX IF NOT EXISTS ix_archive_queues_rid_chunk
  ON __SCHEMA__.archive_researcher_queues(researcher_id, chunk, rank_in_chunk);
CREATE INDEX IF NOT EXISTS ix_archive_queues_build_token
  ON __SCHEMA__.archive_researcher_queues(researcher_id, build_token);

-- ------------------------------------------------------ interview_sessions
-- One row per interview attempt for a researcher. A researcher may have
-- many sessions over time as the queue evolves.
--
-- `current_issue` stages the paper most recently returned by pick_next.py
-- for this session so record_choice.py can verify the canonical_id the
-- plugin is recording was actually issued by the queue (not forged from a
-- stray CLI arg).
CREATE TABLE IF NOT EXISTS __SCHEMA__.archive_interview_sessions(
  session_id       TEXT PRIMARY KEY,    -- uuid (operator/plugin-generated)
  researcher_id    TEXT NOT NULL,
  started_at       TEXT NOT NULL,
  last_active_at   TEXT,
  completed_at     TEXT,                -- null until done
  papers_seen      INTEGER DEFAULT 0,
  choice_counts    JSONB,               -- {save_later:N, not_relevant:N, ...}
  notes            TEXT,
  current_issue    JSONB                -- {"canonical_id": "...", "issued_at": "..."}
);
ALTER TABLE __SCHEMA__.archive_interview_sessions
  ADD COLUMN IF NOT EXISTS current_issue JSONB;
CREATE INDEX IF NOT EXISTS ix_archive_sessions_rid
  ON __SCHEMA__.archive_interview_sessions(researcher_id);

-- -------------------------------------------- profile_verifications (stage 1)
CREATE TABLE IF NOT EXISTS __SCHEMA__.archive_profile_verifications(
  session_id        TEXT PRIMARY KEY,
  researcher_id     TEXT NOT NULL,
  profile_snapshot  JSONB NOT NULL,     -- topics+methods+authors we showed
  corrections       JSONB,              -- researcher-supplied diff
  confirmed_at      TEXT NOT NULL
);

-- --------------------------------------------------- responses (per paper)
-- One row per (researcher, paper) — UPSERT semantics (latest answer wins).
CREATE TABLE IF NOT EXISTS __SCHEMA__.archive_responses(
  researcher_id    TEXT,
  canonical_id     TEXT,
  session_id       TEXT,
  choice           TEXT CHECK (choice IN
    ('save_later','not_relevant','already_read','tell_me_more','skipped')),
  choice_detail    JSONB,               -- e.g. citation note, why-not-relevant text
  responded_at     TEXT NOT NULL,
  PRIMARY KEY (researcher_id, canonical_id)
);
CREATE INDEX IF NOT EXISTS ix_archive_responses_session
  ON __SCHEMA__.archive_responses(session_id);
CREATE INDEX IF NOT EXISTS ix_archive_responses_choice
  ON __SCHEMA__.archive_responses(researcher_id, choice);

-- =========================================================================
-- P14 — dimension tagging + composite ranking
-- (Additive; existing rows keep working. All migrations idempotent.)
-- =========================================================================

-- One row per (paper × dimension × category). Normalized for SQL-side
-- queries like "papers matching method=RSA and stim=FACE". The denorm
-- mirror lives on archive_filter_decisions.dim_tags JSONB (added below).
CREATE TABLE IF NOT EXISTS __SCHEMA__.archive_paper_dim_tags(
  canonical_id    TEXT NOT NULL,
  dimension       TEXT NOT NULL CHECK (dimension IN ('focus','method','stim','subj')),
  category_code   TEXT NOT NULL,             -- ≤ 6 chars; from taxonomy.json
  strength        REAL NOT NULL DEFAULT 1.0, -- 0..1; sqrt-normalized hit count
  match_evidence  JSONB,                     -- {"hits":[...], "src":"title|abstract|venue"}
  tagged_at       TEXT NOT NULL,
  tagger_version  TEXT NOT NULL,             -- e.g. "v1.2026-05-22"
  PRIMARY KEY (canonical_id, dimension, category_code)
);
CREATE INDEX IF NOT EXISTS ix_archive_dim_tags_dim_cat
  ON __SCHEMA__.archive_paper_dim_tags(dimension, category_code, strength DESC);
CREATE INDEX IF NOT EXISTS ix_archive_dim_tags_cid
  ON __SCHEMA__.archive_paper_dim_tags(canonical_id);

-- Denormalized mirror on filter_decisions (plugin's hot-path table).
ALTER TABLE __SCHEMA__.archive_filter_decisions
  ADD COLUMN IF NOT EXISTS dim_tags JSONB;
  -- Shape: {"focus":["F-BEH","F-BAY"],"method":["M-PSY"],"stim":["S-ORI"],"subj":["U-HUM"]}

-- Per-researcher dim preferences + chunk mix (Stage-1 confirmed).
ALTER TABLE __SCHEMA__.archive_profile_verifications
  ADD COLUMN IF NOT EXISTS dim_preferences JSONB;
  -- Shape: {"focus":{"F-BEH":0.8,"F-NIM":0.3},
  --         "method":{"M-RSA":1.0}, "stim":{"S-FAC":0.7},
  --         "subj":{"U-HUM":1.0}, "combo_bonus":[["M-RSA","S-FAC"]],
  --         "source":"auto-then-confirmed","version":1}
ALTER TABLE __SCHEMA__.archive_profile_verifications
  ADD COLUMN IF NOT EXISTS chunk_mix JSONB;
  -- Shape: {"recent":120,"mid":60,"classic":20}; null → default mix in builder.

-- Queue rows carry the composite + tier + match record so pick_next.py
-- needs no extra joins to colour the introduction Korean sentence.
ALTER TABLE __SCHEMA__.archive_researcher_queues
  ADD COLUMN IF NOT EXISTS tier      TEXT;     -- 'S' | 'A' | 'B' | 'C'
ALTER TABLE __SCHEMA__.archive_researcher_queues
  ADD COLUMN IF NOT EXISTS composite REAL;     -- final score
ALTER TABLE __SCHEMA__.archive_researcher_queues
  ADD COLUMN IF NOT EXISTS dim_match JSONB;    -- {"matched":{...},"combos":[...],"tier":"S"}
CREATE INDEX IF NOT EXISTS ix_archive_queues_rid_tier
  ON __SCHEMA__.archive_researcher_queues(researcher_id, tier, rank_in_chunk);

-- ----------------------------------- meta_reviews (every 10 answers, stage 6)
-- The (session_id, at_response_count) uniqueness is enforced by a UNIQUE
-- INDEX added *outside* the CREATE TABLE so that re-applying schema_archive
-- on an existing install (which already has archive_meta_reviews without
-- the constraint) installs the index idempotently. CREATE TABLE IF NOT
-- EXISTS would silently skip inline UNIQUE clauses on a pre-existing table.
CREATE TABLE IF NOT EXISTS __SCHEMA__.archive_meta_reviews(
  id                  TEXT PRIMARY KEY,         -- uuid
  session_id          TEXT NOT NULL,
  researcher_id       TEXT NOT NULL,
  at_response_count   INTEGER NOT NULL,
  choice_breakdown    JSONB NOT NULL,
  criterion_proposal  JSONB,                    -- what we'd change
  applied             BOOLEAN DEFAULT FALSE,
  applied_at          TEXT,
  recorded_at         TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_archive_meta_session_n
  ON __SCHEMA__.archive_meta_reviews(session_id, at_response_count);
CREATE INDEX IF NOT EXISTS ix_archive_meta_session
  ON __SCHEMA__.archive_meta_reviews(session_id);
