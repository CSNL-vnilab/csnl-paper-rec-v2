#!/usr/bin/env bash
# scripts/run_tick_cron.sh — every-4-hour state machine tick. Idempotent.
# Gated by state/.CRON_ENABLED. Pure dispatch — cron_tick.py owns the logic.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ ! -f "state/.CRON_ENABLED" ]; then exit 0; fi
mkdir -p state
python3 scripts/cron_tick.py >> state/cron.log 2>&1
