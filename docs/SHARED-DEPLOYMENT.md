# Shared deployment — `csnl-paper-archive-interview` plugin

Walkthrough for a lab operator to share this plugin with other CSNL
researchers, and for those researchers to install + run it.

## 1 — Operator: one-time provisioning

### 1a. Apply the schema (already done if you're running the recommendation
cycle; safe to re-run)

```
! python scripts/init_db.py
```

### 1b. Build the archive once

```
! python scripts/archive/ingest_classics.py --no-read-pdf --apply
! python scripts/archive/ingest_rec_log.py --enrich --apply
! python scripts/archive/ingest_pi_network.py --apply
! python scripts/archive/merge_dedupe_filter.py --apply
! python scripts/archive/tag_dimensions.py --apply
! python scripts/archive/compute_embeddings.py --apply
! python scripts/archive/build_researcher_queue.py --all --apply
```

Each step is idempotent and operator-gated. The last step writes one
queue per active researcher in `csnl_research.projects`.

### 1c. Provision the scoped Supabase role

Researchers should NOT receive the lab's `postgres` pooler credentials.
Provision a scoped role instead:

1. Edit `state/provision/csnl_archive_user.sql` and set a strong
   password on line 22 (`\set archive_pw ...`).
2. Apply it:
   ```
   ! psql -h <SUPABASE_HOST> -p 5432 -U postgres -d postgres \
       -f state/provision/csnl_archive_user.sql
   ```
3. The script prints the resulting GRANTs at the end — verify the role
   can SELECT all `archive_*` tables and INSERT/UPDATE/DELETE only on:
   - `archive_interview_sessions`
   - `archive_profile_verifications`
   - `archive_responses`
   - `archive_meta_reviews`

### 1d. Publish the plugin

The repo `github:CSNL-vnilab/csnl-paper-rec-v2` already contains the
plugin tree. Push the latest commits so researchers can pull:

```
git push origin main
```

(The repo is set up as a Claude Code marketplace — `.claude-plugin/marketplace.json`
at the repo root.)

### 1e. Share credentials with researchers

Send each researcher:
- Supabase host (`SUPABASE_DB_HOST` value from your `.env`)
- The scoped role name (`csnl_archive_user`) and the password you set in 1c
- Their researcher init (e.g. `JOP`, `BHL`)
- A pointer to this document and the `plugin/README.md`

Send via 1Password / Bitwarden / a sealed Slack DM — **never paste
credentials into a shared channel**.

## 2 — Researcher: install + run

### 2a. Install the plugin in Claude Code

In a fresh Claude Code session:

```
/plugin marketplace add github:CSNL-vnilab/csnl-paper-rec-v2
/plugin install csnl-paper-archive-interview@csnl-marketplace
```

(If the repo is private, you'll need a GitHub token — Claude Code will
prompt. Alternatively the operator can give you a local-checkout path:
`/plugin marketplace add /path/to/csnl-paper-rec`.)

### 2b. Configure credentials

Create the env file the plugin reads. Pick ONE of:

- **Per-machine:** `~/.csnl-paper-archive/.env` (recommended — works
  across plugin reinstalls).
- **Per-install:** `~/.claude/plugins/cache/csnl-marketplace/csnl-paper-archive-interview/<version>/.env`

```
SUPABASE_DB_HOST=<host from operator>
SUPABASE_DB_PORT=5432
SUPABASE_DB_NAME=postgres
SUPABASE_DB_USER=csnl_archive_user
SUPABASE_DB_PASSWORD=<password from operator>
CPR_LEDGER_SCHEMA=csnl_paper_rec
```

Lock it down so other users on your machine cannot read it:

```
chmod 600 ~/.csnl-paper-archive/.env
```

### 2c. Verify the install

```
python ~/.claude/plugins/cache/csnl-marketplace/csnl-paper-archive-interview/0.1.0/scripts/preflight.py <YOUR_INIT>
```

(Replace `<YOUR_INIT>` with the two/three-letter init the operator gave
you. The script prints `ok: true` with queue counts if everything is in
place, or a Korean error message naming the missing piece.)

### 2d. Run the interview

```
/csnl-paper-archive-interview:paper-interview <YOUR_INIT>
```

The skill walks you through (one question per turn):
1. Topic confirmation
2. Methodology confirmation
3. Project-weighting (only if you have >1 active project)
4. Dimension-preference confirmation
5. Paper-by-paper MCQ (`1` save / `2` not relevant / `3` already read /
   `4` tell me more)
6. Active belief update every 10 answers

All researcher-facing text is in Korean; scientific terms stay in
English (RSA, MVPA, efficient coding, …).

## 3 — Boundaries (verify these are still true)

- The plugin **never** sends Slack / email / DMs.
- The plugin **only** writes to the 4 plugin-writeable tables; the DB
  role's GRANTs enforce this on the server side, and `plugin/scripts/_pdb.py`
  enforces it on the client side.
- The plugin **never** writes to `csnl_research.*`.
- The plugin **never** triggers the recommendation cycle, the cron, or
  any operator-only pipeline step.
- The explainer agent (option 4 "더 자세히 소개해줘") falls back to the
  paper's abstract when the operator's `pipeline/crawl.mjs` is not
  reachable on disk (the typical case for a marketplace install).

## 4 — Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `/plugin list` empty | Install didn't complete | Reinstall: `/plugin uninstall …@csnl-marketplace` then `/plugin install …@csnl-marketplace` |
| `Unknown command: /paper-interview` | Forgot the namespace | Use `/csnl-paper-archive-interview:paper-interview <INIT>` |
| `preflight.py` says "Supabase 연결 실패" | `.env` not picked up OR creds wrong | Check `chmod 600 ~/.csnl-paper-archive/.env` exists and matches operator-given values |
| `preflight.py` says "활성 프로젝트가 csnl_research 에 없습니다" | Your projects haven't been entered above the confidence threshold | Update them via the CSNL self-archive tool first |
| `preflight.py` says "추천 큐가 아직 생성되지 않았습니다" | Operator hasn't run `build_researcher_queue.py <YOUR_INIT> --apply` yet | Ask the operator |
| Korean text shows the raw codes (`F-EFC`, `M-RSA`, …) | The skill's tag-rendering didn't load the taxonomy | Restart the session; the skill loads taxonomy on Stage 0. |

## 5 — Updating the plugin

The plugin is installed from a Git tag/commit. To update:

```
/plugin uninstall csnl-paper-archive-interview@csnl-marketplace
/plugin marketplace update csnl-marketplace
/plugin install csnl-paper-archive-interview@csnl-marketplace
```

Or wait — Claude Code may auto-update on a daily refresh.

## 6 — Revoking access

If a researcher leaves the lab:

```sql
REVOKE ALL ON csnl_paper_rec.archive_interview_sessions FROM csnl_archive_user;
REVOKE ALL ON csnl_paper_rec.archive_profile_verifications FROM csnl_archive_user;
REVOKE ALL ON csnl_paper_rec.archive_responses FROM csnl_archive_user;
REVOKE ALL ON csnl_paper_rec.archive_meta_reviews FROM csnl_archive_user;
DROP ROLE csnl_archive_user;
```

…then re-provision with a new password via `csnl_archive_user.sql` and
re-share with active researchers. (Per-researcher roles + RLS is a
future enhancement; for now the lab uses one shared role.)
