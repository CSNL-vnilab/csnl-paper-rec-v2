# Integration verification — v2 run 20260519-1539

End-to-end verification of the harness-engineered v2. The deterministic head
ran against the **live** `csnl_research` (read-only); the Opus scout
fan-out + producer–reviewer + delivery dry-run ran in-session; **nothing was
sent** (operator gate held).

## Pipeline executed (operator-run DB via `!`, agent everything else)

| Stage | Result |
|---|---|
| `init_db.py` | `csnl_paper_rec` schema + 5 tables created (idempotent) |
| `migrate_legacy_ledger.py` | predecessor sqlite → Postgres: **8 rec / 1 read / 3 JOP excl** (verified in-ledger) |
| `00_select_projects.py` | live `csnl_research` (read-only) → **10 active projects** |
| `01_extract_topics.py` | **6 units** (JOP/BYL/MSY/SMJ/JYK/SYJ+BHL); SYJ+BHL merged |
| `dedup_snapshot.py` | ledger + 1069 reading-DB + exclusions → local snapshot |
| `build_scout_briefs.py` | 6 per-unit briefs; 0 criteria-flagged rows |
| scout fan-out (6 Opus, bg) | **6/6 units ≥3 in-window non-dup full-text-reviewed candidates** |
| `fanin_check.py` | PASS — composite=max(D1..D5); every ≥7 dim carries a verbatim full-text quote; final dedup clean; all strict-tier |
| producer–reviewer (6 Opus) | 6/6 rule-clean drafts; iterations 1–3 |
| `build_packet.py` | `07_drafts.json` + committed `drafts/20260519-1539/`; contract OK |
| `deliver.py --dry-run` | full preview; **`Tone lint: OK` ×6**; gate hard-blocks `--send` without `state/.APPROVED_20260519-1539` |

## Result (6/6 — matches/exceeds predecessor 20260519-1408)

| unit | 채택 #1 | comp | best_dim | #cand | 추천근거자 | iters |
|---|---|---|---|---|---|---|
| JOP | Endogenous precision of the number sense (eLife 2026) | 8 | D1 | 10 | 260 | 1 |
| BYL | Process dynamics of serial biases… (Psych Bull Rev 2025) | 8 | D1 | 7 | 273 | 1 |
| MSY | History bias… macaque PFC (J Physiol 2026) | 7 | D1 | 10 | 268 | 2 |
| SMJ | Saccades to spatially extended objects (J Vision 2026) | 9 | D1 | 4 | 275 | 1 |
| JYK | Transitions in dynamical regime… (Nature 2025) | 8 | D3 | 3 | 270 | 2 |
| SYJ+BHL | Process dynamics of serial biases… (Psych Bull Rev 2025) | 8 | D3 | 7 | 274 | 3 |

Full text genuinely read (Europe PMC OA-XML / Playwright HTML/PDF via pdfjs;
50k–150k chars/paper); abstract-only candidates correctly capped at
composite 6. Real grounding (e.g. SMJ's COA↔concentricity LCI prior; BYL's
duration-dependent repulsion→attraction reversal; JYK's trained-RNN line
attractor; JOP's efficient-coding cost-function model selection).

## Acceptance criteria (REF-F)

1. ✅ harness `.claude/agents/`(5) + `.claude/skills/`(4) + `CLAUDE.md`, hardened to rules/00–06.
2. ✅ Interests from Postgres `csnl_research` (read-only); ledger in Postgres `csnl_paper_rec`.
3. ✅ Every unit ≥3 in-window, non-dup, full-text-reviewed candidates + one grounded lint-clean draft (추천 근거 260–275, in 150–280).
4. ✅ `deliver.py` dry-run prints full preview + `Tone lint: OK` for all units; **zero sends** without `--send --operator-approved` + on-disk token (verified blocked).
5. ✅ Committed review packet `drafts/20260519-1539/` (per-unit ≥3 candidates + chosen draft + grounding + verbatim quote).
6. ✅ Reproducible (documented commands; no secrets tracked — `.env`/keys/tokens absent from git; `node_modules`+`state/runs/` gitignored). Private GitHub repo: pending operator push confirmation.

## Notes for the operator (not defects)

- **BYL and SYJ+BHL both top-ranked Park HB 2025** (`10.3758/s13423-025-02714-5`). Valid: different units, per-unit dedup. Each unit's `.md` lists ≥3 alternates for a swap if you want title diversity across the two.
- **MSY composite 7** (thinnest unit) — clears the ≥7 bar honestly; the scout did not launder a weaker fit. Its iScience same-lab match was abstract-only and correctly capped at 6.
- Execution-mode constraint: custom `.claude/agents/*.md` types are not spawnable `subagent_type` in this runtime; realized via built-in `general-purpose` (`model: opus`) with role+skill in-prompt (DECISIONS-v2). Agent files remain the binding contracts.
- DB safety architecture: every prod-Supabase statement was **operator-run via `!`**; the agent held no prod-DB access (DECISIONS-v2 §v3).

## Not done (operator gate — by design)

Real send. Requires operator review of this packet, then
`touch state/.APPROVED_20260519-1539` + `python scripts/deliver.py
--run-id 20260519-1539 --send --operator-approved` (sequential, one unit at
a time, ≥7 s gap). Out of this session's scope.
