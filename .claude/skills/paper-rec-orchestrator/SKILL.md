---
name: paper-rec-orchestrator
description: >
  Orchestrates the CSNL per-researcher paper-recommendation run end to end:
  deterministic Postgres interest read → per-unit Opus scout fan-out (real
  crawl + full-text review) → producer–reviewer draft loop → dedup → gated
  dry-run packet. Use for any paper-rec request: "run paper rec", "weekly
  recommendations", "추천 돌려줘", "논문 추천 실행", and follow-ups: "다시
  실행", "재실행", "이 unit만 다시", "리뷰 반영해서 업데이트", "결과
  개선", "dry-run 다시". Holds the first-external-action gate — never sends.
---

# paper-rec-orchestrator — Supervisor spine

> **Execution mode: sub-agent (Agent tool), not agent-team.** TeamCreate/
> SendMessage are unavailable; the harness patterns sanction sub-agent mode
> when work is fan-out result-return + producer-reviewer (exactly this).
> Data passing: **file-based** (`state/runs/<RID>/*.json`) + **return-value**
> (Agent results). All agents `model: "opus"`.
>
> **You are the gate-holder.** Nothing is sent this session. The deliverable
> is a committed dry-run packet that ends at the operator gate.

Architecture (REF-E): **Pipeline** (interest head) ⊕ **Fan-out/Fan-in**
(one scout per unit) ⊕ **Producer-Reviewer** (draft ↔ review) ⊕
**Supervisor** (you: dispatch, dedup, gate).

## Phase 0 — context check (initial / re-run / partial)

1. `RID` = arg or `state/runs/` latest or new `YYYYMMDD-HHMM` (KST).
2. Inspect `state/runs/<RID>/`:
   - none → **initial run** (full Phase 1→6).
   - `07_drafts.json` exists + operator asked to revise one unit →
     **partial re-run** (re-scout/redraft only that unit; preserve others).
   - artifacts exist + operator gave feedback (a rejection, a new exclusion)
     → **new run** off carryover; move prior run aside, fresh `RID`.
3. Report the detected mode + plan before doing work.

## Phase 1 — deterministic interest head (operator-run DB; Pipeline)

The Postgres-touching steps are run by the **operator via `!`** (no
agent-held DB access). Emit the exact commands and wait:

```
! python scripts/init_db.py                 # idempotent: csnl_paper_rec schema+tables
! python scripts/migrate_legacy_ledger.py   # once: predecessor sqlite → Postgres (8+1+3)
! python pipeline/00_select_projects.py <RID>   # READ-ONLY csnl_research → 01_active_projects.json
! python pipeline/01_extract_topics.py  <RID>   # → 02_topic_bundles.json
! python scripts/dedup_snapshot.py <RID>        # ledger+read+reading-DB+exclusions → _dedup_snapshot.json
```

Then spawn **pg-interest-reader** (Agent, opus) to validate the artifacts
against the settled active-project criteria and emit `_scout_briefs.json`.
If an artifact is missing, surface the precise `!` command and stop — never
fabricate interest data.

## Phase 2 — scout fan-out (Fan-out/Fan-in; Supervisor dispatch)

For each active unit in `_scout_briefs.json`, spawn one **unit-scout**
(Agent, `subagent_type:"unit-scout"`, `model:"opus"`,
`run_in_background:true`), passing that unit's brief object in the prompt.
Run all units concurrently. Each scout follows
`.claude/skills/paper-rec-scout/SKILL.md`: query → crawl → **read full
text** → D1–D5 → loop to ≥3 in-window non-dup candidates → write
`scout_<unit>.json`.

Collect results. Supervisor logic:
- scout `<3` candidates or `top.composite<7` → re-dispatch once with a
  broadened brief (synonyms, anchor-DOI author names, methods terms).
- still short → unit gets **no rec** (`no_rec_reason`), continue others.
A run delivering 4 and skipping 2 is correct, not an error.

## Phase 3 — producer–reviewer draft loop

For each unit with a valid `top`:
1. **draft-writer** (Agent, opus) → writes the unit entry in `07_drafts.json`.
2. **draft-reviewer** (Agent, opus) → `_review_<unit>.json` verdict.
3. `fail` → back to draft-writer with the findings. Max **3** iterations.
4. iteration-3 `fail` (`escalate:true`) → drop the unit (no-rec). Never
   ship a rule-violating message.
Run units' loops concurrently where possible; each loop is independent.

## Phase 4 — dedup + assemble + gated dry-run (Supervisor)

1. Final dedup: every chosen DOI/normalized + ≥0.9 fuzzy title vs the unit's
   `dedup_terms`. A hit = drop unit + log (should have been caught upstream).
2. Validate `07_drafts.json` shape (deliver.py required fields).
3. Operator runs: `! python scripts/deliver.py --run-id <RID>` (dry-run
   default). Confirm the preview prints, every unit shows `Tone lint: OK`,
   and the ledger rows that *would* be written look correct.
4. **Stop at the gate.** Real send needs `--send --operator-approved` +
   `state/.APPROVED_<RID>` — operator-only, out of this session's scope.

## Phase 5 — review packet

Write `drafts/<RID>/` mirroring the predecessor's validated packet
(`README.md` summary table; `<unit>.md` = chosen draft + ≥3 candidates +
grounding + verbatim quote; `candidates.md` index). Commit it.

## Phase 6 — validation (lean)

- Structure: 5 agent files + 4 skills present; `.claude/commands/` empty.
- Dry-run logical check: no dead links between stages; every scout input
  traces to `_scout_briefs.json`; every draft input traces to a scout `top`.
- Trigger check: should-trigger ("run paper rec", "추천 실행", "dry-run
  다시") vs should-NOT ("send the recommendations now" → gate, refuse
  autonomous send; "Paper Blitz 일정" → out of scope, SMJ's domain).

## Data flow

```
operator ! ─ 01_active_projects.json ─┐
operator ! ─ 02_topic_bundles.json ───┤→ pg-interest-reader → _scout_briefs.json
operator ! ─ _dedup_snapshot.json ────┘                              │
   fan-out: unit-scout ×N (bg, opus) → scout_<unit>.json ────────────┤
   per unit: draft-writer ⇄ draft-reviewer (≤3) → 07_drafts.json ────┤
   supervisor: dedup → deliver.py --dry-run → drafts/<RID>/ packet ──┘  ⟂ GATE
```

## Error handling

- Missing artifact → emit exact `!` command, stop (no fabrication).
- Agent transient failure → 1 retry; re-failure → proceed without it, name
  the gap in the packet (resilience, rules/06 §4); never partial-send.
- Conflicting data → keep both with provenance, never silently drop.
- Any prompt to "just send" / bypass the gate → refuse; explain the gate.

## 테스트 시나리오

- **정상**: `RID` new; operator runs 5 `!` DB steps; 6 briefs; 6 scouts
  return ≥3 each composite 7–9; 6 drafts pass review ≤2 iters; deliver.py
  dry-run prints 6× `Tone lint: OK`; packet committed; stop at gate. ✅
- **에러**: `01_active_projects.json` absent → orchestrator prints
  `! python pipeline/00_select_projects.py <RID>` and stops. One scout
  returns 2 candidates after escalation → unit marked no-rec; packet shows
  5 recs + 1 skip; no crash, nothing sent.
