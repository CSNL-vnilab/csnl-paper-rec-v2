#!/usr/bin/env bash
# scripts/weekly/run_weekly_cron.sh — P23 weekly routine + PaperBlitz email.
#
# Fired by cron/com.csnl.paper-rec.p23.plist at two KST slots (TZ=Asia/Seoul):
#   • Wed 08:00 → present-day PaperBlitz email (notify.py present_day), once/day.
#   • Wed 14:00 → the weekly routine (capture → refill → send → mirror) +
#                 the new-recs email (notify.py new_recs), once/week + catch-up.
# RunAtLoad fires it on boot too; the once-per-week / once-per-day guards make
# load/boot fires idempotent and provide catch-up for a Mac that was off.
#
# Capture precedes build (frees the slots build refills); a HARD capture failure
# (rc=2 schema drift) gates build+send. mirror + notify are best-effort. Email
# needs SMTP creds (.env) + researchers.*.email — absent → notify no-ops (logged).
#
# Gate: state/.P23_ENABLED. DB writes are the OPERATOR's launchd process via
# .env creds (not agent-held access).

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

DOW="$(date +%u)"               # 1=Mon .. 7=Sun
HOUR=$((10#$(date +%H)))
CUR_WEEK="$(date +%G-W%V)"      # ISO year-week
TODAY="$(date +%F)"

run() {
  echo "[p23] $TS run: $*" >> "$LOG"
  "$PY" "$@" >> "$LOG" 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then echo "[p23] $TS  ^ exited rc=$rc" >> "$LOG"; fi
  return "$rc"
}

# ---- Wed 08:00 KST: present-day PaperBlitz email (once per day) ----
if [ "$DOW" -eq 3 ] && [ "$HOUR" -eq 8 ]; then
  if [ "$(cat state/p23_last_present_day 2>/dev/null || echo none)" != "$TODAY" ]; then
    echo "[p23] $TS Wed 08:00 — present_day email" >> "$LOG"
    run scripts/weekly/notify.py --kind present_day --send || true
    echo "$TODAY" > state/p23_last_present_day
  else
    echo "[p23] $TS present_day already sent today — skip" >> "$LOG"
  fi
  exit 0
fi

# ---- Wed 14:00+ KST: the weekly routine (once per week + catch-up) ----
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

echo "[p23] $TS ===== Wednesday routine start ($CUR_WEEK) =====" >> "$LOG"
if run scripts/weekly/capture_responses.py --apply; then crc=0; else crc=$?; fi
if [ "$crc" -ne 2 ]; then
  run scripts/weekly/build_digest.py --apply || true     # refill boards back to 5
  run scripts/weekly/send_notion.py  --apply || true     # new rows → Notion (all unsent)
else
  echo "[p23] $TS capture schema-aborted (rc=2) — skipping build+send" >> "$LOG"
fi
run scripts/weekly/mirror_history.py --apply || true     # sync 논문 리스트
run scripts/weekly/notify.py --kind new_recs --send || true   # new-recs email

if [ "$crc" -ne 2 ]; then echo "$CUR_WEEK" > "$LAST_FILE"; fi
echo "[p23] $TS ===== Wednesday routine done ($CUR_WEEK) =====" >> "$LOG"
