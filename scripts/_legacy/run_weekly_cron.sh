#!/usr/bin/env bash
# scripts/run_weekly_cron.sh — Friday 14:00 KST entry. Initialize a new
# cycle, build the pipeline up to 08_dm_drafts.json, preflight + send,
# then seed cycle_state with awaiting_initial_reply for each recipient.
#
# Gates: state/.CRON_ENABLED must exist. Lockfile prevents concurrent runs.
# Honors the Opus off-ramp: if state/runs/<RID>/08_dm_drafts.json already
# exists (operator pre-ran /paper-rec-orchestrator), the script uses it.
# Otherwise it warns + DMs the operator on Slack + exits without sending
# (no LLM in unattended path; no auto-regression to keyword-API quality).
#
# DECISIONS-v3.md v6: this cron runs unattended; tone-lint + dedup + the
# contract validator remain hard gates on every send.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG="state/cron.log"
echo "[weekly] $(date -u +%Y-%m-%dT%H:%M:%SZ)  start" >> "$LOG"

if [ ! -f "state/.CRON_ENABLED" ]; then
  echo "[weekly] state/.CRON_ENABLED absent — silent exit" >> "$LOG"
  exit 0
fi

LOCK="state/.cron_weekly.lock"
if [ -f "$LOCK" ]; then
  AGE=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
  if [ "$AGE" -lt 7200 ]; then
    echo "[weekly] lock held (age=${AGE}s) — exit" >> "$LOG"
    exit 0
  fi
  rm -f "$LOCK"
fi
echo $$ > "$LOCK"
trap "rm -f $LOCK" EXIT

# Cycle id = YYYYMMDD of this Friday; RID = $CYCLE-1400
CYCLE="$(date -j -v-fri +%Y%m%d 2>/dev/null || date +%Y%m%d)"
RID="${CYCLE}-1400"
echo "[weekly] cycle=$CYCLE  rid=$RID" >> "$LOG"
mkdir -p "state/runs/$RID"

# 1) deterministic head (DB-touching steps; uses .env)
python3 pipeline/00_select_projects.py "$RID" >> "$LOG" 2>&1
python3 pipeline/01_extract_topics.py  "$RID" >> "$LOG" 2>&1
python3 scripts/dedup_snapshot.py      "$RID" >> "$LOG" 2>&1
python3 scripts/build_scout_briefs.py  "$RID" >> "$LOG" 2>&1

# 2) Opus off-ramp: prefer pre-existing 08_dm_drafts.json (operator-produced)
DM_DRAFTS="state/runs/$RID/08_dm_drafts.json"
if [ ! -f "$DM_DRAFTS" ]; then
  echo "[weekly] no Opus drafts at $DM_DRAFTS — notifying operator + exit" >> "$LOG"
  # Notify operator on their DM (Slack bot). Operator init = $MY_INIT from .env.
  MY_INIT="$(awk -F= '/^MY_INIT=/{print $2}' .env 2>/dev/null || true)"
  if [ -z "$MY_INIT" ]; then MY_INIT="JOP"; fi
  python3 - <<PY >> "$LOG" 2>&1 || true
import os,sys,yaml,requests
sys.path.insert(0,'pipeline'); from _db import load_env; load_env()
r=yaml.safe_load(open('config/researchers.yaml'))['researchers'].get(os.environ.get('MY_INIT','$MY_INIT'),{})
dm=r.get('dm_channel'); token=os.environ.get('SLACK_BOT_TOKEN','')
if not (dm and token):
    print('  cannot notify: missing dm_channel or token'); raise SystemExit(0)
text=(f"paper-rec-v2 weekly cron: cycle {os.environ.get('RID','$RID')} 의 "
      "Opus drafts(08_dm_drafts.json)가 없습니다. 이번 주는 추천 발송을 건너뜁니다. "
      "다음 사이클 전에 /paper-rec-orchestrator 세션으로 drafts를 미리 생성해 주십시오.")
requests.post('https://slack.com/api/chat.postMessage',
    headers={'Authorization':f'Bearer {token}','Content-Type':'application/json; charset=utf-8'},
    json={'channel':dm,'text':text}, timeout=20)
print('  operator notified')
PY
  echo "[weekly] no-rec week (operator notified) — exit" >> "$LOG"
  exit 0
fi

# 3) Preflight: dry-run preview the drafts (DB-free) for tone lint visibility
python3 scripts/deliver.py --run-id "$RID" --mode dm --drafts "$DM_DRAFTS" >> "$LOG" 2>&1

# 4) Gate token + SEND (real Slack DMs, sequential ≥7s, ledger written)
touch "state/.APPROVED_$RID"
python3 scripts/deliver.py --run-id "$RID" --mode dm --drafts "$DM_DRAFTS" \
  --send --operator-approved >> "$LOG" 2>&1

# 5) Seed cycle_state with awaiting_initial_reply for each recipient.
python3 - <<PY >> "$LOG" 2>&1
import json,sys,uuid
from datetime import datetime,timezone,timedelta
from pathlib import Path
sys.path.insert(0,'pipeline')
from _db import load_env,exec_many,query_json,ledger_schema
load_env(); s=ledger_schema()
RID="$RID"; CYCLE="$CYCLE"
KST=timezone(timedelta(hours=9)); now=datetime.now(KST).isoformat(timespec='seconds')
next_at=(datetime.now(KST)+timedelta(hours=24)).isoformat(timespec='seconds')
drafts=json.loads(Path('state/runs/'+RID+'/08_dm_drafts.json').read_text())['drafts']
rows=[(CYCLE,d['member_init'],d['unit_id'],'awaiting_initial_reply',RID,
       d['paper_doi'],d['paper_title'],None,0,now,next_at,None,
       'seeded by run_weekly_cron.sh') for d in drafts]
sql=(f"INSERT INTO {s}.cycle_state (cycle_id,member_init,unit_id,state,rid,"
     "paper_doi,paper_title,picked_alt_doi,reply_count,last_action_at,"
     "next_action_at,last_reply_ts,notes) VALUES "
     "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
     "ON CONFLICT (cycle_id,member_init) DO NOTHING")
n=exec_many(sql,rows)
print(f"  cycle_state seeded: {n} row(s) for cycle {CYCLE}")
PY

echo "[weekly] $(date -u +%Y-%m-%dT%H:%M:%SZ)  done  rid=$RID" >> "$LOG"
