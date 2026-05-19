# Operator decisions & non-negotiables — 2026-05-18

Source of this build. Any change to a row here re-opens the first-external-action gate.

## Operator decisions (asked & answered 2026-05-18)

| # | Fork | Decision |
|---|---|---|
| 1 | Repo strategy | **New repo + port portable assets.** New `csnl-paper-rec`; port `paper_rec_verifier.py`, `paper-scout-embed.py`, scan/score/post SKILL playbooks, `signal_configs.py`, `feedback_daemon.py`; rewire topic source to `csnl_research.projects`. |
| 2 | Delivery channel | **Channel + DM ping.** Full recommendation posted to the unit members' INIT_claude channel(s); a short DM ping points to the channel post. **No `— Claude` signature.** |
| 3 | "Active project" | **phase + confidence.** `phase ∈ {data_collection, analysis, manuscript_draft}` AND `confidence_avg ≥ 0.7`. deprecated_stub / conf=0 excluded. SYJ+BHL merged to one unit. |
| 4 | Runtime / cadence | **Manual only until validated.** No Ollama cron, no GitHub Actions yet. Rank/draft performed by the **in-session Opus agent team** (operator-driven). Cadence intent = weekly, but triggered manually for now. |

## Non-negotiables (applied without asking; from lab rules/memory)

- **No signature** — `— Claude` and any model name forbidden (2026-05-13 routing rule + plugin `rules/01_tone.md` + `agents/archiver.md` supersede the 2026-05-08 template that still shows it).
- **First-external-action gate** — first real send requires dry-run preview + explicit operator approval. Paper-rec is a NEW route, NOT covered by the `memev_autofire` standing approval. `deliver.py` defaults to `--dry-run`; real send hard-blocked behind an explicit approval flag + on-disk approval token.
- **Sequential pacing** — one researcher unit at a time, ≥5–10 s gap, ledger row + Slack permalink verified between sends. Never batch.
- **Date filter** — strict: journal ≤ 365 d, preprint ≤ 90 d. Relaxed (journal ≤ 2 y, preprint ≤ 6 m) ONLY after ≥3 strict queries returned zero topical match. Beyond relaxed = rejected. Output declares `tier`.
- **Never re-recommend** — dedup vs ledger `paper_recommendations` + `paper_recommendations_read` + reading-DB + `exclusion_rules`. Rejected topic drops matching carry-over.
- **SYJ+BHL = one unit** — merged for selection/topic; delivered to both members' channels + DM pings.
- **deprecated_stub excluded**; topic facts are *inferred-fit, not confirmed-fit* until researcher feedback (skepticism rule).
- **PB/CWLL boundary** — this sends recommendation *content*; PB/CWLL *schedule reminders* remain SMJ's domain, untouched.
- **No Anthropic API key in any unattended path.** Validation phase uses in-session Opus agents (operator-driven, not cron). Future cron LLM = local Ollama only.

## Resolved contradictions (legacy memory conflicts)

- Signature `— Claude` (2026-05-08 template) vs forbidden (2026-05-13 + plugin) → **forbidden** wins (later rule).
- DM (2026-05-08 paper-rec template) vs INIT_claude-only (2026-05-13) → **channel + DM ping** (operator decision #2).
- `paradigm` / `framework` → allowed **≤1 occurrence per message** (later strict-tone rule).

## Open assumption (confirm at dry-run)

- SYJ+BHL merged unit → one recommendation delivered to **both** INIT_claude channels + both DM pings. Operator may instead choose a single shared channel.
