# Shared deployment — `csnl-paper-archive-interview` plugin

Two audiences:
- **Operator** (lab admin): builds the archive once, pushes to GitHub.
- **Researcher**: install + 1-paste setup, then `/paper-interview`.

## Operator — one-time build

```
# 1. Apply schema (idempotent).
! python scripts/init_db.py

# 2. Ingest sources (one-time; takes ~10–15 min total).
! python scripts/archive/ingest_classics.py --no-read-pdf --apply
! python scripts/archive/ingest_rec_log.py --enrich --apply
! python scripts/archive/ingest_pi_network.py --apply

# 3. Dedupe + filter + tag + embed.
! python scripts/archive/merge_dedupe_filter.py --apply
! python scripts/archive/tag_dimensions.py --apply
! python scripts/archive/compute_embeddings.py --apply

# 4. Per-researcher queues (re-run whenever projects/preferences move).
! python scripts/archive/build_researcher_queue.py --all --apply

# 5. Publish.
git push origin main
```

## Operator — share credentials

The lab already shares one Supabase admin password among researchers.
Send each researcher these 4 values via 1Password / Bitwarden / sealed
Slack DM (NOT a regular channel):

- `SUPABASE_DB_HOST` (e.g. `aws-1-ap-southeast-1.pooler.supabase.com`)
- `SUPABASE_DB_USER` (the shared pooler role, e.g. `postgres.<projectid>`)
- `SUPABASE_DB_PASSWORD` (the shared admin password)
- their researcher init (e.g. `JOP`, `BHL`)

The plugin's `_pdb.py` enforces a strict table allowlist + rejects
multi-statement SQL + rejects TRUNCATE/DROP/ALTER. So even though the
shared role has admin rights at the DB level, the plugin layer cannot
write outside the 4 plugin-writeable tables. Defense in depth via code,
not via DB-level GRANTs.

## Researcher — install + setup (3 commands)

In a fresh Claude Code session:

```
/plugin marketplace add CSNL-vnilab/csnl-paper-rec-v2
/plugin install csnl-paper-archive-interview@csnl-marketplace
```

(If the repo is private, Claude Code prompts for a GitHub token.)

Then in a terminal, paste the .env (substitute the 3 values you got
from the operator):

```
mkdir -p ~/.csnl-paper-archive && cat > ~/.csnl-paper-archive/.env <<EOF
SUPABASE_DB_HOST=<HOST_FROM_OPERATOR>
SUPABASE_DB_PORT=5432
SUPABASE_DB_NAME=postgres
SUPABASE_DB_USER=<USER_FROM_OPERATOR>
SUPABASE_DB_PASSWORD=<PASSWORD_FROM_OPERATOR>
CPR_LEDGER_SCHEMA=csnl_paper_rec
EOF
chmod 600 ~/.csnl-paper-archive/.env
```

Verify (optional but recommended):

```
python ~/.claude/plugins/cache/csnl-marketplace/csnl-paper-archive-interview/*/scripts/preflight.py <YOUR_INIT>
```

(The `*` glob picks up whichever plugin version is currently installed.)

→ `ok: true` with chunk counts. If you see a Korean error, fix what it
names and retry.

## Researcher — run

```
/csnl-paper-archive-interview:paper-interview <YOUR_INIT>
```

Korean UI throughout; scientific terms (RSA, MVPA, fMRI, BOLD, efficient
coding, pRF, Bayesian observer, …) stay in English. The skill walks you
through (one question per turn):

1. Topic confirmation
2. Methodology confirmation
3. Project-weighting (only if you have >1 active project; supply
   percentages e.g. `70/20/5/5`)
4. Dimension-preference confirmation
5. Paper-by-paper MCQ: `1` save / `2` not relevant / `3` already read /
   `4` tell me more
6. Active belief update every 10 answers (the skill explains in 2 short
   Korean sentences what it learned and what changes next cycle)

You can stop anytime. The next `/csnl-paper-archive-interview:paper-interview`
resumes from where you left off.

## Boundaries (verify these stay true)

- The plugin **never** sends Slack / email / DMs.
- The plugin **only** writes to `archive_interview_sessions`,
  `archive_profile_verifications`, `archive_responses`,
  `archive_meta_reviews`. Verified at runtime by `_pdb.py`.
- The plugin **never** writes to `csnl_research.*`, the recommendation
  ledger, the feedback events table, or the cron state machine.
- The plugin **never** triggers the operator's weekly recommendation
  cycle.
- Option 4 ("더 자세히 소개해줘") spawns an isolated explainer agent
  with its own context window; it falls back to the paper's stored
  abstract when the operator's full-text crawler is unreachable (the
  typical case for a marketplace install).

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `/plugin list` empty | Install didn't complete | Retry: `/plugin uninstall …@csnl-marketplace` then `/plugin install …@csnl-marketplace` |
| `Unknown command: /paper-interview` | Forgot the namespace | Use `/csnl-paper-archive-interview:paper-interview <INIT>` |
| `preflight` says "Supabase 연결 실패" | `.env` not picked up OR creds wrong | `chmod 600 ~/.csnl-paper-archive/.env` exists with all 5 values |
| `preflight` says "활성 프로젝트가 csnl_research 에 없습니다" | Your `csnl_research.projects` rows aren't above the confidence threshold | Update them via the CSNL self-archive tool first |
| `preflight` says "추천 큐가 아직 생성되지 않았습니다" | Operator hasn't run `build_researcher_queue.py <YOUR_INIT> --apply` yet | Ask the operator |
| Korean text shows raw codes (`F-EFC`, `M-RSA`, …) | The skill couldn't load the taxonomy | Restart the session; the skill loads taxonomy on Stage 0 |

## Updating

The plugin pulls from a git commit on each install. To pick up new
operator commits:

```
/plugin uninstall csnl-paper-archive-interview@csnl-marketplace
/plugin marketplace update csnl-marketplace
/plugin install csnl-paper-archive-interview@csnl-marketplace
```

## Optional — scoped DB role for revocation

The current model uses one shared admin password. If you later want
per-role isolation (e.g. to revoke a leaver without rotating the lab
password), apply `state/provision/csnl_archive_user.sql` — it creates a
`csnl_archive_user` role with INSERT/UPDATE/DELETE only on the 4
plugin-writeable tables. Researchers would then put
`SUPABASE_DB_USER=csnl_archive_user` in their `.env`. The plugin code
behavior is identical either way.
