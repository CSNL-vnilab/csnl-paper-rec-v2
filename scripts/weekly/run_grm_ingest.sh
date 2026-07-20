#!/usr/bin/env bash
# scripts/weekly/run_grm_ingest.sh — weekly NAS GRM/2026 material ingest.
#
# Fired by cron/com.csnl.paper-rec.grm.plist on Wednesday 15:00 KST
# (TZ=Asia/Seoul) — after the GRM (≈13:00 end) so the week's PB/GRM slides have
# been dropped on the NAS. RunAtLoad fires it on boot too; the once-per-week
# guard (state/grm_last_run_week) makes load/boot fires idempotent and gives
# catch-up for a Mac that was powered off at the scheduled time.
#
# Flow: a single `ingest_grm_nas.py --weeks 4 --apply` — scan the recent dated
# folders, classify PB/GRM files, (try to) enrich from the Notion schedule, and
# UPSERT archive_meeting_materials. The script itself is NAS read-only and only
# writes csnl_paper_rec. SUMMARIES are uploaded separately, later, via
# upload_meeting_summary.py from a Claude MCP session (not in this cron path).
#
# Week marking is gated on a clean exit: the week is recorded done ONLY when
# the ingest returns rc=0. A non-zero rc (rc=2 migration/DB unreachable, rc=3
# NAS share NOT mounted) leaves the week UNMARKED so the next boot/calendar
# fire retries — a transient NAS unmount at 15:00 no longer silently burns the
# week (recovered within the --weeks 4 catch-up window regardless).
#
# Gate: state/.GRM_INGEST_ENABLED (opt-in; separate from .P23_ENABLED and
# .CRON_ENABLED). DB writes are the OPERATOR's launchd process via .env creds
# (not agent-held DB access).

set -euo pipefail
export TZ="Asia/Seoul"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
LOG="state/cron-grm.log"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [ ! -f "state/.GRM_INGEST_ENABLED" ]; then
  echo "[grm] $TS state/.GRM_INGEST_ENABLED absent — silent exit" >> "$LOG"
  exit 0
fi

DOW="$(date +%u)"               # 1=Mon .. 7=Sun
HOUR=$((10#$(date +%H)))
CUR_WEEK="$(date +%G-W%V)"      # ISO year-week

# once-per-week guard
LAST_FILE="state/grm_last_run_week"
LAST="$(cat "$LAST_FILE" 2>/dev/null || echo none)"
if [ "$CUR_WEEK" = "$LAST" ]; then
  echo "[grm] $TS week $CUR_WEEK already ingested — exit" >> "$LOG"
  exit 0
fi

# only run on/after Wed 15:00 KST (RunAtLoad before then just waits)
if [ "$DOW" -lt 3 ] || { [ "$DOW" -eq 3 ] && [ "$HOUR" -lt 15 ]; }; then
  echo "[grm] $TS before Wed 15:00 KST ($CUR_WEEK dow=$DOW h=$HOUR) — waiting" >> "$LOG"
  exit 0
fi

# lockfile (avoid a load+calendar double-fire racing)
LOCK="state/.cron_grm.lock"
if [ -f "$LOCK" ]; then
  AGE=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
  if [ "$AGE" -lt 3600 ]; then
    echo "[grm] $TS lock held (age=${AGE}s) — exit" >> "$LOG"
    exit 0
  fi
  rm -f "$LOCK"
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

echo "[grm] $TS ===== weekly GRM ingest start ($CUR_WEEK) =====" >> "$LOG"
# --no-notion: the cron's integration token can't reach the schedule page
# (under "CSNL", 404). Presenter/type/keyword enrich is done separately by a
# Claude MCP session reading the "🎤 Presentations" DB. The cron does files only.
if "$PY" scripts/weekly/ingest_grm_nas.py --weeks 4 --apply --no-notion >> "$LOG" 2>&1; then
  echo "$CUR_WEEK" > "$LAST_FILE"
  echo "[grm] $TS ===== weekly GRM ingest done ($CUR_WEEK) =====" >> "$LOG"
else
  rc=$?
  echo "[grm] $TS ingest exited rc=$rc — NOT marking week done (will retry)" >> "$LOG"
  exit "$rc"
fi
