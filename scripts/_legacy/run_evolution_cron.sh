#!/usr/bin/env bash
# scripts/run_evolution_cron.sh — Thursday 23:00 KST (before the next
# Friday cycle). Applies rule-based evolution to the latest cycle and
# logs everything to evolution_log. Idempotent.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ ! -f "state/.CRON_ENABLED" ]; then exit 0; fi
mkdir -p state
echo "[evolution] $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> state/cron.log
python3 scripts/apply_evolution.py >> state/cron.log 2>&1
