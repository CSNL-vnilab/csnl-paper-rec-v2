#!/usr/bin/env bash
# scripts/run_archive_weekly.sh — Tuesday 18:00 KST entry. Generates the
# week's archive recommendations and the upcoming Wednesday Paper Blitz
# schedule from already-persistent state in csnl_paper_rec.archive_*.
#
# NO send paths. NO LLM. Pure deterministic SQL → DB upserts. The operator
# (or researchers via /csnl-paper-archive-interview:paper-weekly and
# /csnl-paper-archive-interview:paper-blitz) reads the results from the DB.
#
# Gate: state/.CRON_ENABLED. Lockfile prevents concurrent runs.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG="state/archive_cron.log"
echo "[archive-weekly] $(date -u +%Y-%m-%dT%H:%M:%SZ)  start" >> "$LOG"

if [ ! -f "state/.CRON_ENABLED" ]; then
  echo "[archive-weekly] state/.CRON_ENABLED absent — silent exit" >> "$LOG"
  exit 0
fi

LOCK="state/.cron_archive_weekly.lock"
if [ -f "$LOCK" ]; then
  AGE=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
  if [ "$AGE" -lt 3600 ]; then
    echo "[archive-weekly] lock held (age=${AGE}s) — exit" >> "$LOG"
    exit 0
  fi
  rm -f "$LOCK"
fi
echo $$ > "$LOCK"
trap "rm -f $LOCK" EXIT

# (a) Weekly unread-paper recommendations — all researchers, top 5.
python3 scripts/archive/weekly_recommend.py --top 5 >> "$LOG" 2>&1 || {
  echo "[archive-weekly] weekly_recommend.py failed" >> "$LOG"
  exit 1
}

# (b) Wednesday Paper Blitz — next upcoming Wednesday.
python3 scripts/archive/paper_blitz_feed.py >> "$LOG" 2>&1 || {
  echo "[archive-weekly] paper_blitz_feed.py failed" >> "$LOG"
  exit 1
}

echo "[archive-weekly] $(date -u +%Y-%m-%dT%H:%M:%SZ)  done" >> "$LOG"
