-- state/provision/csnl_archive_user.sql — operator-run once.
--
-- Provisions a SHARED, scoped Postgres role that lab researchers use
-- through the marketplace plugin. The role has READ access to the
-- archive layer (so the plugin can render the queue) and WRITE access
-- to ONLY the 4 plugin-writeable tables. It has NO access to the
-- recommendation ledger, the feedback events table, the cron state
-- machine, or csnl_research writes.
--
-- This SQL is idempotent. Re-running it is safe.
--
-- USAGE:
--   1. Set a strong password below (or via psql variable, see comment).
--   2. ! psql ... -f state/provision/csnl_archive_user.sql
--   3. Share the role name + password with each researcher.
--   4. Each researcher fills `plugin/.env` (or ~/.csnl-paper-archive/.env)
--      with the role name + password and the operator's Supabase host.

-- Pick a password before running. The plugin's _pdb.py uses standard
-- libpq SSL by default; the password travels over a TLS connection.
\set archive_pw '''CHANGE_ME_TO_A_STRONG_PASSWORD'''

-- 1. Create the role if it doesn't exist (idempotent).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'csnl_archive_user') THEN
    EXECUTE format(
      'CREATE ROLE csnl_archive_user WITH LOGIN PASSWORD %L',
      :'archive_pw'
    );
  ELSE
    EXECUTE format(
      'ALTER ROLE csnl_archive_user WITH PASSWORD %L',
      :'archive_pw'
    );
  END IF;
END$$;

-- 2. Allow the role to *see* the right schemas (USAGE only).
GRANT USAGE ON SCHEMA csnl_paper_rec TO csnl_archive_user;
GRANT USAGE ON SCHEMA csnl_research  TO csnl_archive_user;

-- 3. READ access: archive layer + csnl_research.projects (read-only).
GRANT SELECT ON
  csnl_paper_rec.archive_papers,
  csnl_paper_rec.archive_paper_sources,
  csnl_paper_rec.archive_filter_decisions,
  csnl_paper_rec.archive_paper_embeddings,
  csnl_paper_rec.archive_paper_dim_tags,
  csnl_paper_rec.archive_researcher_queues,
  csnl_paper_rec.archive_interview_sessions,
  csnl_paper_rec.archive_profile_verifications,
  csnl_paper_rec.archive_responses,
  csnl_paper_rec.archive_meta_reviews
TO csnl_archive_user;

GRANT SELECT ON csnl_research.projects TO csnl_archive_user;

-- 4. WRITE access — ONLY the 4 plugin-writeable tables. The plugin's
--    _pdb.py also enforces this allowlist in code; this DB-side GRANT
--    is defense in depth.
GRANT INSERT, UPDATE, DELETE ON
  csnl_paper_rec.archive_interview_sessions,
  csnl_paper_rec.archive_profile_verifications,
  csnl_paper_rec.archive_responses,
  csnl_paper_rec.archive_meta_reviews
TO csnl_archive_user;

-- 5. Make sure FUTURE tables added under csnl_paper_rec do NOT auto-
--    inherit write access. (We rely on explicit GRANTs above.)
ALTER DEFAULT PRIVILEGES IN SCHEMA csnl_paper_rec
  REVOKE ALL ON TABLES FROM csnl_archive_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA csnl_research
  REVOKE ALL ON TABLES FROM csnl_archive_user;

-- 6. Sanity print — show what the role can do now.
SELECT grantee, table_schema, table_name, privilege_type
FROM information_schema.table_privileges
WHERE grantee = 'csnl_archive_user'
ORDER BY table_schema, table_name, privilege_type;
