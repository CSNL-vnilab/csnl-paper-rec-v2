# Cron install — `csnl-paper-rec`

> **⚠ RETIRED v3 doc below (kept for history).** The three v3 Slack crons this
> file documented (`com.csnl.paper-rec.{weekly,tick,evolution}`) are **deleted**
> (plists git-removed; `run_weekly_cron.sh`/`cron_tick.py`/`apply_evolution.py`
> moved to `scripts/_legacy/`; the `state/.CRON_ENABLED` gate is gone). They no
> longer run and must not be reinstalled without the R2 "v3 retired vs.
> break-glass" decision. Everything from "What runs and when" onward is
> **historical** and refers to files that have moved or been removed.

## Live crons (current — KST)

| plist | When | Wrapper | Gate | Purpose |
|---|---|---|---|---|
| `cron/com.csnl.paper-rec.p23.plist` | **Wed 08:00 + Wed 14:00** | `scripts/weekly/run_weekly_cron.sh` | `state/.P23_ENABLED` (**absent = dormant, no send**) | P23 weekly Notion digest: `capture → build → send → mirror → notify`, rolling replace-to-5 |
| `cron/com.csnl.paper-rec.grm.plist` | **Wed 15:00** (retries) | `scripts/weekly/run_grm_ingest.sh` | `state/.GRM_INGEST_ENABLED` (present = on) | P31 GRM NAS → `archive_meeting_materials` weekly ingest |

Both wrappers self-guard on host timezone (lab Mac = KST), a once-per-week
stamp (`state/p23_last_run_week` / `state/grm_last_run_week`), and a lockfile.
`launchctl` on the lab Mac needs Full Disk Access (see `[[macos-launchd-tcc-fda]]`).

---

## RETIRED — v3 Slack automation (historical; do not install)

macOS launchd integration for the Friday-weekly recurring recommendation +
multi-turn conversation + evolution loop. Re-opens the manual-only gate per
`docs/DECISIONS-v3.md`.

### What ran and when (KST) — v3, retired

| plist | When | Script | Purpose |
|---|---|---|---|
| `com.csnl.paper-rec.weekly`    | every **Friday 14:00** | `scripts/_legacy/run_weekly_cron.sh`    | start cycle, build interest+briefs, send initial DMs |
| `com.csnl.paper-rec.tick`      | every **4 h** (00/04/08/12/16/20) | `scripts/_legacy/run_tick_cron.sh` → `_legacy/cron_tick.py` | state machine: fetch replies, classify, send acks/reminders |
| `com.csnl.paper-rec.evolution` | every **Thursday 23:00**  | `scripts/_legacy/run_evolution_cron.sh` → `_legacy/apply_evolution.py` | end-of-cycle rule-based evolution |

All three honored `state/.CRON_ENABLED` (now removed), the lockfile
`state/.cron_*.lock`, and the tone-lint + dedup + gate inside `deliver.py`.
TZ pinned to `Asia/Seoul` in each plist.

## Install (one-time, on the lab Mac)

```sh
cd /Users/csnl/Documents/claude/csnl-paper-rec

# 1) apply v3 schema additions (cycle_state + evolution_log)
python3 scripts/init_db.py

# 2) make scripts executable
chmod +x scripts/run_weekly_cron.sh scripts/run_tick_cron.sh scripts/run_evolution_cron.sh

# 3) symlink plists to ~/Library/LaunchAgents (or copy)
mkdir -p ~/Library/LaunchAgents
ln -sf "$(pwd)/cron/com.csnl.paper-rec.weekly.plist"    ~/Library/LaunchAgents/
ln -sf "$(pwd)/cron/com.csnl.paper-rec.tick.plist"      ~/Library/LaunchAgents/
ln -sf "$(pwd)/cron/com.csnl.paper-rec.evolution.plist" ~/Library/LaunchAgents/

# 4) ENABLE (this is the operator's deliberate first-external-action gate)
touch state/.CRON_ENABLED

# 5) load the three jobs
launchctl load ~/Library/LaunchAgents/com.csnl.paper-rec.weekly.plist
launchctl load ~/Library/LaunchAgents/com.csnl.paper-rec.tick.plist
launchctl load ~/Library/LaunchAgents/com.csnl.paper-rec.evolution.plist

# 6) verify
launchctl list | grep csnl.paper-rec
```

## Pre-flight test (no sends; safe)

```sh
# dry-run the tick — fetches replies + computes state transitions but
# skips actual Slack send (no apply_feedback, no deliver).
python3 scripts/cron_tick.py --dry-run
```

## Pause / disable

```sh
# Pause (immediate, soft): cron scripts see this and exit silently
rm state/.CRON_ENABLED

# Resume
touch state/.CRON_ENABLED

# Full uninstall
launchctl unload ~/Library/LaunchAgents/com.csnl.paper-rec.weekly.plist
launchctl unload ~/Library/LaunchAgents/com.csnl.paper-rec.tick.plist
launchctl unload ~/Library/LaunchAgents/com.csnl.paper-rec.evolution.plist
rm ~/Library/LaunchAgents/com.csnl.paper-rec.{weekly,tick,evolution}.plist
```

## Operator workflow each week

1. **(Recommended) Wednesday–Thursday:** open a Claude Code session at the
   repo root and run `/paper-rec-orchestrator`. The Opus scout team will
   produce `state/runs/<RID>/08_dm_drafts.json` for the next Friday's `RID`
   (= `YYYYMMDD-1400` for the upcoming Friday). This preserves the
   validated 6/6 Opus-quality path. Quality regression risk if skipped.
2. **Friday 14:00:** cron auto-fires:
   - Re-runs the deterministic head (`csnl_research` SELECT, briefs, dedup).
   - Picks up your `08_dm_drafts.json`.
   - Dry-run preview → tone-lint → send.
   - Seeds `cycle_state` with `awaiting_initial_reply` per recipient.
3. **Friday 18:00 onward:** ticks every 4 h drive the conversation:
   - +24 h no reply → reminder DM (neutral, NO Paper Blitz per rules/00).
   - any reply → classify → ack DM with how the feedback will be reflected.
   - `picked_alternate` → next cycle re-recommends that alternate.
   - `thumbs_down` / `already_read` → `exclusion_rules` + `paper_recommendations_read` row.
4. **Thursday 23:00:** evolution runs; entries land in `evolution_log`.
5. **Next Friday 14:00:** new cycle, dedup already excludes anything
   marked read / excluded from prior cycles.

## What this cron will NEVER do (immutable)

- Mention Paper Blitz or CWLL (`rules/00` — SMJ's domain).
- Write to `csnl_research` (read-only).
- Send a message that hits a BANNED_TERMS substring (`rules/01`; tone-lint
  hard-abort per recipient).
- Re-recommend a paper already in `paper_recommendations` /
  `paper_recommendations_read` / `exclusion_rules` for that unit (`rules/04`).
- Auto-rewrite `rules/*.md` or `.claude/agents/*.md` (binding contracts).
- Call an Anthropic / OpenAI / OpenRouter / Ollama API (`rules/00` +
  DECISIONS-v3 #v5).

## Logs + audit

- `state/cron.log` — append-only cron stdout/stderr.
- `csnl_paper_rec.cycle_state` — per-(cycle,member) state machine.
- `csnl_paper_rec.evolution_log` — rule-based evolution audit trail.
- `csnl_paper_rec.feedback_events` — every classified reply.
- `csnl_paper_rec.paper_recommendations` / `_messages` / `_read` — sends.
