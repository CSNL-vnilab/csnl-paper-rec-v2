# Operator decisions & non-negotiables — v3 (2026-05-21)

Supplements v1 (`DECISIONS-2026-05-18.md`) and v2 (`DECISIONS-v2.md`). Every
prior non-negotiable still holds unless overridden here. **Re-opens the
first-external-action gate** because v3 introduces the project's first
unattended (cron-driven) path.

## v3 operator decisions (asked & answered 2026-05-21)

| # | Fork | Decision |
|---|---|---|
| v4 | Cadence | **Weekly automated cron**, Friday 14:00 KST start, complete initial sends by Friday 18:00 KST. Multi-turn DM conversation per recipient continues until each is `decided` / `passed` / `timeout` (≤7 d). Overrides DECISIONS #4 ("manual only until validated"). |
| v5 | LLM in unattended path | **Pure heuristic + template (no LLM in cron).** No Anthropic API/SDK; no Ollama either ("api SDK x" strict read). Hybrid off-ramp: if `state/runs/<RID>/08_dm_drafts.json` exists before Friday 18:00 (operator pre-ran `/paper-rec-orchestrator` for Opus-quality drafts), cron honors it; else deterministic fallback. |
| v6 | Send gate during cron | **Fully unattended** (cron auto-fires sends after pre-flight: dedup OK + tone-lint OK + contract OK + `state/.CRON_ENABLED` token). The operator's deliberate enable is the gate — `launchctl load` + the on-disk `state/.CRON_ENABLED` token (op-created once) authorize all subsequent weekly cycles. |
| v7 | Reply / re-question | **Slack bot (existing `SLACK_BOT_TOKEN`)** — `conversations.history` + `conversations.replies` (no Slack SDK; raw HTTP via `requests`). |
| v8 | Evolution policy | **Rule-based, conservative** (no LLM): thumbs_down/already_read → `exclusion_rules` row + `paper_recommendations_read`; ≥2 consistent thumbs_down on a topic across cycles → drop matching scout query seed; 0-reply streaks across ≥2 cycles → flag (no auto-broaden). All evolutions recorded in `evolution_log`. |

## Boundaries preserved verbatim (immutable)

- **Paper Blitz / CWLL is SMJ's domain — out of scope.** `rules/00`. Cron templates contain zero PB/scheduling text; heuristic classifier does NOT trigger on "paper blitz". A reply that mentions PB is still classified by other cues; our outbound never mentions PB.
- **No signature** (`rules/01`). No `— Claude`, no model name, no AI self-reference. Tone-lint BANNED_TERMS substring hard-abort on every send.
- **Never re-recommend** (`rules/04`). Dedup vs `csnl_paper_rec.paper_recommendations` + `paper_recommendations_read` + reading-DB + `exclusion_rules` before every send.
- **Sequential ≥7 s per recipient** in every batch (existing `deliver.py`).
- **csnl_research read-only**; writes confined to `csnl_paper_rec`.
- **No Anthropic API key / OpenRouter / Ollama** anywhere unattended (v5).

## Acknowledged quality trade-off (REF-C)

The validated 6/6 outcome (2026-05-19) came from **Opus scouts reading full
text**. The deterministic cron path is the predecessor's failed
keyword-API-only mode (1/6) plus title/abstract heuristics — a known
regression risk. Mitigations:

1. The cron fallback heuristic uses **anchor-DOI co-author + project-field
   token** scoring (richer than pure keyword search) and full-text fetch
   when available.
2. The operator off-ramp: a 15-minute `/paper-rec-orchestrator` session
   before Friday 14:00 produces Opus-quality `08_dm_drafts.json` that the
   cron honors. **Recommended for at least the first 2–3 weekly cycles**
   while the heuristic path is being validated against feedback.
3. Per `rules/06 §7` (no rec > a bad one): the cron will SKIP a unit whose
   top heuristic candidate fails minimum thresholds rather than ship
   off-domain noise.

## State machine (per recipient, per cycle)

```
pending_send ─Fri14:00+preflight─→ awaiting_initial_reply
awaiting_initial_reply ─reply─→ classify ─→ ack + next state
                       ─24h─→ reminded ─reply─→ classify
                                       ─24h─→ timeout
classify outcomes:
  thumbs_up                 → ack → decided
  picked_alternate (alt_doi)→ ack + schedule next-cycle re-rec(alt) → decided
  thumbs_down               → ack + exclusion_rules row → passed
  already_read              → ack + paper_recommendations_read → passed
  thinking / thread_reply   → ack ("noted") → awaiting_decision (12h)
awaiting_decision ─more reply─→ re-classify
                  ─12h─→ timeout
```

All transitions logged to `cycle_state` (extended schema). Terminal states:
`decided`, `passed`, `timeout`, `no_rec` (skipped: no candidate ≥ threshold).

## Schedule (KST)

| When | Action | How |
|---|---|---|
| **Friday 14:00 KST** | weekly cycle start: 00_select → 01_extract → dedup_snapshot → build_briefs → scout (Opus-drafts-injected else deterministic) → build_dm_drafts → preflight → SEND (cron auto-fires) | `run_weekly_cron.sh` |
| **Every 4 h** (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 KST) | tick: fetch_replies → classify → apply state transitions → send acks/reminders | `run_tick_cron.sh` |
| **Following Thursday 23:00 KST** | end-of-cycle: aggregate signals → apply rule-based evolution → archive cycle | `run_evolution_cron.sh` |

## Operator enable / disable

Enable (one-time, operator):
```sh
touch state/.CRON_ENABLED
launchctl load ~/Library/LaunchAgents/com.csnl.paper-rec.weekly.plist
launchctl load ~/Library/LaunchAgents/com.csnl.paper-rec.tick.plist
launchctl load ~/Library/LaunchAgents/com.csnl.paper-rec.evolution.plist
```

Pause (any time):
```sh
rm state/.CRON_ENABLED   # tick scripts see this and exit silently
```

Disable / uninstall:
```sh
launchctl unload ~/Library/LaunchAgents/com.csnl.paper-rec.*.plist
rm ~/Library/LaunchAgents/com.csnl.paper-rec.*.plist
```

## Re-opens the gate (binding note)

Per DECISIONS-2026-05-18 ("any change to a row re-opens the first-external
-action gate"), enabling this cron is the operator's deliberate exercise of
that gate for the recurring weekly route. Each weekly send respects all
inner gates (tone-lint, dedup, contract validation, lock); the *outer*
weekly autorun is permitted by the `state/.CRON_ENABLED` token + loaded
launchd job.
