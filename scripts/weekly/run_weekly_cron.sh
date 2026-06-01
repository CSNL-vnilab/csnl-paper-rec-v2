#!/usr/bin/env bash
# scripts/weekly/run_weekly_cron.sh — P23 weekly Notion delivery dispatcher.
#
# Invoked every 30 minutes by cron/com.csnl.paper-rec.p23.plist (TZ=Asia/Seoul)
# and self-routes by KST day-of-week + hour:
#   Mon 09:00  → build_digest --apply  then  send_notion --apply
#   Sun 18:00  → expire_pending --apply           (cooldown sweep, pre-build)
#   Sun 09:00  → dry_run_preview --send           (operator preview; self-gates
#                                                   to the rollout window)
#   else       → capture_responses --apply         (poll Notion Status → ledger)
#
# This path is DETERMINISTIC and LLM-free (DECISIONS-v3): the Korean rationale
# is assembled from the pre-built P21 synopsis, not generated at run time.
#
# Gates: state/.P23_ENABLED must exist (separate from the v3 .CRON_ENABLED, so
# P23 is enabled independently). A lockfile prevents overlapping runs. DB writes
# here are the OPERATOR's launchd process via .env creds — not agent-held access
# (consistent with the project boundary).

set -euo pipefail
export TZ="Asia/Seoul"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
LOG="state/cron-p23.log"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [ ! -f "state/.P23_ENABLED" ]; then
  echo "[p23] $TS state/.P23_ENABLED absent — silent exit" >> "$LOG"
  exit 0
fi

LOCK="state/.cron_p23.lock"
if [ -f "$LOCK" ]; then
  AGE=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
  if [ "$AGE" -lt 1500 ]; then
    echo "[p23] $TS lock held (age=${AGE}s) — exit" >> "$LOG"
    exit 0
  fi
  rm -f "$LOCK"
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

DOW="$(date +%u)"                 # 1=Mon .. 7=Sun
HOUR=$((10#$(date +%H)))          # force base-10 (avoid octal on 08/09)
MIN=$((10#$(date +%M)))
CUR_WEEK="$(date +%G-W%V)"        # ISO year-week, e.g. 2026-W23
LAST_WEEK_FILE="state/p23_last_build_week"
LAST_WEEK="$(cat "$LAST_WEEK_FILE" 2>/dev/null || echo none)"

# Run a python step in an `if`-context (so set -e does not abort the
# dispatcher). Returns the child's exit code; logs nonzero.
run() {
  echo "[p23] $TS run: $*" >> "$LOG"
  "$PY" "$@" >> "$LOG" 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then echo "[p23] $TS  ^ exited rc=$rc" >> "$LOG"; fi
  return "$rc"
}

# Should we build+send this tick? Either the scheduled Monday-09:00 slot, OR a
# catch-up: this ISO week has no recorded successful build yet and it is past
# 09:00 (covers a late launchd fire / wake-from-sleep, and auto-retries a send
# that failed last tick — LAST_WEEK is only written after BOTH succeed).
DO_BUILD=0
if [ "$DOW" -eq 1 ] && [ "$HOUR" -eq 9 ] && [ "$MIN" -lt 30 ]; then DO_BUILD=1; fi
if [ "$CUR_WEEK" != "$LAST_WEEK" ] && [ "$HOUR" -ge 9 ]; then DO_BUILD=1; fi

if [ "$DO_BUILD" -eq 1 ]; then
  echo "[p23] $TS build+send for $CUR_WEEK (last_built=$LAST_WEEK)" >> "$LOG"
  if run scripts/weekly/build_digest.py --apply; then
    if run scripts/weekly/send_notion.py --apply; then
      echo "$CUR_WEEK" > "$LAST_WEEK_FILE"
      echo "[p23] $TS marked $CUR_WEEK built+sent" >> "$LOG"
    else
      echo "[p23] $TS send failed — week NOT marked; retries next tick" >> "$LOG"
    fi
  else
    echo "[p23] $TS build failed — skipping send; retries next tick" >> "$LOG"
  fi
elif [ "$DOW" -eq 7 ] && [ "$HOUR" -eq 18 ] && [ "$MIN" -lt 30 ]; then
  echo "[p23] $TS Sunday 18:00 KST — expire pending" >> "$LOG"
  run scripts/weekly/expire_pending.py --apply || true
elif [ "$DOW" -eq 7 ] && [ "$HOUR" -eq 9 ] && [ "$MIN" -lt 30 ]; then
  echo "[p23] $TS Sunday 09:00 KST — dry-run preview" >> "$LOG"
  run scripts/weekly/dry_run_preview.py --send || true
else
  run scripts/weekly/capture_responses.py --apply || true
fi

echo "[p23] $TS done (dow=$DOW hour=$HOUR min=$MIN week=$CUR_WEEK)" >> "$LOG"
