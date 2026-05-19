# Operator decisions & non-negotiables — v2 (2026-05-19)

Supplements (does not replace) `docs/DECISIONS-2026-05-18.md`. Every
non-negotiable there still holds. This records what is **new in v2** and the
safety-driven execution architecture. Any change to a row here re-opens the
first-external-action gate.

## v2 operator decisions (asked & answered 2026-05-19)

| # | Fork | Decision |
|---|---|---|
| v1 | Ledger store | **New isolated PostgreSQL schema `csnl_paper_rec`** in the same Supabase project (`qjhzjqkrbvsnwlbpilio`). `csnl_research` stays strictly READ-ONLY. sqlite retired; predecessor ledger migrated (8 rec + 1 read + 3 JOP excl). |
| v2 | GitHub repo | **New private repo `CSNL-vnilab/csnl-paper-rec-v2`.** Predecessor untouched. Local commits throughout; push only after operator review of the dry-run packet. |
| v3 | DB access model | **All prod-DB touches are operator-run via `!`** (init_db, migrate, 00_select_projects, dedup_snapshot, deliver). The agent never connects to the production database. |

## Why decision v3 (safety architecture — binding)

The Claude Code auto-mode classifier blocks agent-inferred production-DB
access. An attempt to scope a wrapper (`scripts/psqlx`) around it was
correctly hard-blocked as a safety-check bypass. Resolution, which also
**strengthens** this project's operator-gate philosophy:

- The agent builds 100% of the system and runs all non-DB work (harness,
  scouts, drafting, packet) from local `state/runs/<RID>/*.json` artifacts.
- Every statement that reaches the production Supabase is executed by the
  **operator** via the `!` prefix — fully visible, human-initiated, using
  the lab's own psycopg2/`psql` pattern. `csnl_research` is SELECT-only;
  writes are confined to `csnl_paper_rec`.
- No agent-built pass-through wrapper exists; `scripts/psqlx` was deleted.
- This mirrors the first-external-action gate: humans hold the keys to
  every side-effecting boundary (prod DB writes, Slack sends).

## Harness execution mode (new)

- Pattern map unchanged: Pipeline ⊕ Fan-out/Fan-in ⊕ Producer-Reviewer ⊕
  Supervisor (REF-E).
- **Execution mode = sub-agent**, not agent-team: `TeamCreate`/`SendMessage`
  are unavailable in this runtime; the harness decision tree sanctions
  sub-agent mode for fan-out result-return + producer-reviewer.
- Custom `.claude/agents/*.md` types are not exposed as spawnable
  `subagent_type` mid-session in this runtime. They remain the binding role
  contracts (acceptance criterion 1) and are **realized via the built-in
  `general-purpose` type** (full tools, `model: opus`) with the role + skill
  referenced in-prompt — the harness-documented built-in-type realization.
  (CLAUDE.md change log records this; revisit if a future runtime exposes
  project agent types.)

## Unchanged binding items (carried verbatim from 2026-05-18)

No signature; first-external-action gate (`--send --operator-approved` +
`state/.APPROVED_<RID>`); sequential never-batch ≥7 s; strict date filter
(journal ≤365 d / preprint ≤90 d, relaxed only after ≥3 strict zero-hit
rounds); never re-recommend (Postgres ledger + read + reading-DB +
exclusions); SYJ+BHL = one unit; deprecated_stub excluded; inferred-fit not
confirmed-fit; PB/CWLL schedule reminders are SMJ's domain, out of scope;
no Anthropic/OpenRouter key in any unattended path, no Ollama; active =
`phase ∈ {data_collection, analysis, manuscript_draft} ∧ confidence_avg ≥
0.7` (operator decision 2026-05-18 "a", `lit_review_post_null` not active —
SYJ covered indirectly via BHL).

## Open assumption (confirm at dry-run, unchanged)

SYJ+BHL merged unit → one recommendation to **both** INIT_claude channels +
both DM pings. Operator may choose a single shared channel instead.
