#!/usr/bin/env bash
# scripts/weekly/run_weekly_cron.sh — P23 weekly routine.
#
# Runs ONCE per ISO week, on/after Wednesday 14:00 KST (right after the Wed-
# morning PaperBlitz). The plist fires it at Wed 14:00 AND at load/boot
# (RunAtLoad); this wrapper's once-per-week guard makes those idempotent and
# provides catch-up: if the Mac was off at Wed 14:00, the next boot (Wed 14:00+
# or any later day that week) runs the missed routine.
#
# One deterministic, LLM-free pass:
#   1. capture_responses — read 읽음 checkboxes; checked → archive_responses
#      (already_read) + Notion page archived (leaves the board).
#   2. build_digest      — refill each board back up to 5 active (1-for-1).
#   3. send_notion       — create Notion rows for the freshly-staged papers.
#   4. mirror_history    — sync "논문 리스트" from archive_responses.
# Capture precedes build (frees the slots build refills); a HARD capture failure
# (rc=2 schema drift) gates build+send. mirror always runs.
#
# Timezone: the wrapper forces TZ=Asia/Seoul for `date`, so the once-per-week
# Wed-14:00-KST guard is correct even if launchd evaluates the calendar slot in
# the host timezone. (For the scheduled fire to land at 14:00 KST the host Mac
# should also be set to Asia/Seoul.)
#
# Gates: state/.P23_ENABLED must exist. Lockfile prevents overlap. DB writes are
# the OPERATOR's launchd process via .env creds (not agent-held access).

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

# Once-per-week + catch-up guard (KST).
CUR_WEEK="$(date +%G-W%V)"          # ISO year-week, e.g. 2026-W23
DOW="$(date +%u)"                   # 1=Mon .. 7=Sun
HOUR=$((10#$(date +%H)))
LAST_FILE="state/p23_last_run_week"
LAST="$(cat "$LAST_FILE" 2>/dev/null || echo none)"

if [ "$CUR_WEEK" = "$LAST" ]; then
  echo "[p23] $TS week $CUR_WEEK already done — exit" >> "$LOG"
  exit 0
fi
if [ "$DOW" -lt 3 ] || { [ "$DOW" -eq 3 ] && [ "$HOUR" -lt 14 ]; }; then
  echo "[p23] $TS before Wed 14:00 KST ($CUR_WEEK dow=$DOW h=$HOUR) — waiting" >> "$LOG"
  exit 0
fi

LOCK="state/.cron_p23.lock"
if [ -f "$LOCK" ]; then
  AGE=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
  if [ "$AGE" -lt 3600 ]; then
    echo "[p23] $TS lock held (age=${AGE}s) — exit" >> "$LOG"
    exit 0
  fi
  rm -f "$LOCK"
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

# Run a step without letting its non-zero exit abort the routine; log the code.
run() {
  echo "[p23] $TS run: $*" >> "$LOG"
  "$PY" "$@" >> "$LOG" 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then echo "[p23] $TS  ^ exited rc=$rc" >> "$LOG"; fi
  return "$rc"
}

echo "[p23] $TS ===== Wednesday routine start ($CUR_WEEK) =====" >> "$LOG"

# Only a HARD capture failure (rc=2 = schema drift) gates build+send — a
# transient archive failure (rc=1) is safe because capture marks reads BEFORE
# archiving, so build's active counts are correct and lingering pages self-heal.
if run scripts/weekly/capture_responses.py --apply; then crc=0; else crc=$?; fi
if [ "$crc" -ne 2 ]; then
  run scripts/weekly/build_digest.py --apply || true     # refill boards back to 5
  run scripts/weekly/send_notion.py  --apply || true     # new rows → Notion (all unsent)
else
  echo "[p23] $TS capture schema-aborted (rc=2) — skipping build+send" >> "$LOG"
fi
run scripts/weekly/mirror_history.py --apply || true     # sync 논문 리스트 (always)

# Mark the week done unless capture hard-aborted (so a later fire retries once
# the operator fixes the schema).
if [ "$crc" -ne 2 ]; then echo "$CUR_WEEK" > "$LAST_FILE"; fi
echo "[p23] $TS ===== Wednesday routine done ($CUR_WEEK) =====" >> "$LOG"
