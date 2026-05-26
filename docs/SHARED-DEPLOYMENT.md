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

# 4. Per-researcher queues — ONE-TIME --all-in-scope build.
#    After this, the session is self-driving: pick_next.py re-ranks against
#    latest dim_preferences on every call (P17 in-session re-rank), so the
#    operator does NOT need to re-run --apply per cycle. Only re-run when:
#      (a) new papers are ingested into the archive, OR
#      (b) a researcher's csnl_research.projects text changes substantially.
! python scripts/archive/build_researcher_queue.py --all --apply --all-in-scope

# 5. Install the Tuesday-18:00-KST cron (generates weekly recs + Wed Blitz).
#    Pure deterministic SQL upserts, NO LLM, NO send paths.
! cp cron/com.csnl.paper-archive.weekly.plist ~/Library/LaunchAgents/
! launchctl load ~/Library/LaunchAgents/com.csnl.paper-archive.weekly.plist
! touch state/.CRON_ENABLED                       # gate is opt-in

# 6. Publish.
git push origin main
```

### Operator — verify the weekly cycle is wired

```
# Dry-run both downstream scripts (read-only against current state).
! python scripts/archive/weekly_recommend.py    --dry-run --top 3
! python scripts/archive/paper_blitz_feed.py    --dry-run

# Inspect any researcher's accumulated paper-status DB.
! python scripts/archive/list_status.py JOP

# Print any researcher's full research-context priming payload.
! python scripts/archive/get_researcher_context.py JOP --human
```

## Operator — share credentials

The lab already shares one Supabase admin password among researchers.
Send each researcher these **3 secret values + their init** via
1Password / Bitwarden / sealed Slack DM (NOT a regular channel):

- `SUPABASE_DB_HOST` (e.g. `aws-1-ap-southeast-1.pooler.supabase.com`)
- `SUPABASE_DB_USER` (the shared pooler role, e.g. `postgres.<projectid>`)
- `SUPABASE_DB_PASSWORD` (the shared admin password)
- their researcher init (e.g. `JOP`, `BHL`)

The other 3 values (PORT=5432, NAME=postgres, SCHEMA=csnl_paper_rec)
are inline defaults in the heredoc / `setup.py` — researchers don't
need to ask about them.

The plugin's `_pdb.py` enforces a strict table allowlist + rejects
multi-statement SQL + rejects TRUNCATE/DROP/ALTER. So even though the
shared role has admin rights at the DB level, the plugin layer cannot
write outside the 4 plugin-writeable tables. Defense in depth via code,
not via DB-level GRANTs.

## Researcher — install + setup

In a fresh Claude Code session:

```
/plugin marketplace add CSNL-vnilab/csnl-paper-rec-v2
# If you previously installed an older version, uninstall first to
# force a clean refresh (version pinning may otherwise keep stale files):
/plugin uninstall csnl-paper-archive-interview@csnl-marketplace
/plugin marketplace update csnl-marketplace
/plugin install   csnl-paper-archive-interview@csnl-marketplace
```

(If the repo is private, Claude Code prompts for a GitHub token.)

Then set up the `.env`. The interactive setup is the easiest —
**run this in a real terminal window, NOT in the Claude Code chat**
(the password prompt needs a real TTY; pasting into Claude Code chat
will refuse or, worse, echo the password):

```
python ~/.claude/plugins/cache/csnl-marketplace/csnl-paper-archive-interview/*/scripts/setup.py
```

It prompts for the 3 secret values, writes `~/.csnl-paper-archive/.env`
with `chmod 600`, and verifies the DB connection if you give it your
init.

If you'd rather paste a heredoc:

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

Either way, verify (optional but recommended):

```
python ~/.claude/plugins/cache/csnl-marketplace/csnl-paper-archive-interview/*/scripts/preflight.py <YOUR_INIT>
```

(The `*` glob picks up whichever plugin version is currently installed.)

→ `ok: true` with chunk counts. If you see a Korean error, fix what it
names and retry — the error message includes the exact path and the
exact setup command.

## Researcher — run

**Four researcher-facing slash commands** (all Korean UI; scientific terms
stay in English):

```
/csnl-paper-archive-interview:paper-interview <YOUR_INIT>   # main MCQ walk
/csnl-paper-archive-interview:paper-weekly    <YOUR_INIT>   # this week's top-5 unread
/csnl-paper-archive-interview:paper-blitz     <YOUR_INIT>   # next Wed Paper Blitz assignment
/csnl-paper-archive-interview:paper-context   <YOUR_INIT>   # prime any session with your research context
```

### paper-interview (the main loop)

One question per turn:

1. Topic confirmation
2. Methodology confirmation
3. Project-weighting (only if you have >1 active project; supply
   percentages e.g. `70/20/5/5`)
4. Dimension-preference confirmation
5. Paper-by-paper MCQ: `1` save / `2` not relevant / `3` already read /
   `4` tell me more
6. Active belief update every 10 answers (the skill explains in 2 short
   Korean sentences what it learned and what changes next cycle)

You can stop anytime. The next `paper-interview` resumes from where you
left off.

### paper-weekly + paper-blitz (downstream payoff)

These read from the Tuesday-18:00-KST operator cron output. If the cron has
not yet generated this week's batch, both commands report that and suggest
you run `paper-interview` directly.

- `paper-weekly` — your top-5 unread papers this week, persisted in
  `archive_weekly_picks`. Stable across operator re-runs.
- `paper-blitz` — your Wednesday 5-min journal-club assignment, picked
  automatically from papers you marked `already_read` in the prior 7 days.
  Claude can generate a 5-min outline (claim / method / result / how it
  connects to your projects).

### paper-context (research-context priming)

Run this at the start of ANY Claude Code session where you want help with
your research. It pulls your `csnl_research.projects` rows + your fingerprint
vocabulary + your latest `dim_preferences` + your last 60 days of
already-read / save-later / not-relevant responses — usually in <1 second.
After this, the session knows your work without you having to re-explain.

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
| `preflight` says "Supabase 연결 정보 .env 파일이 없어요" | First-time setup not done | Run `setup.py` (the message names it) or paste the heredoc into a terminal |
| `preflight` says "Supabase 연결 실패" | `.env` exists but creds wrong | Re-run `setup.py --force` to overwrite, or edit `~/.csnl-paper-archive/.env` directly |
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
