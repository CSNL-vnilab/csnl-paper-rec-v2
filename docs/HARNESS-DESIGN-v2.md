# Harness design — csnl-paper-rec-v2 (the "review & harden" record)

Produced via the `harness` factory skill (Phase 0–6), then hardened to
`rules/00–06` (REF-B). This is the design rationale + validation record.

## Phase 0 — audit

`.claude/agents/`, `.claude/skills/`, `CLAUDE.md` were empty → **new build**,
full Phase 1→6. No drift, no prior harness to reconcile.

## Phase 1 — domain

Per-researcher academic-paper recommendation. Work types: (a) deterministic
grounded read (Postgres interest), (b) parallel discovery + **full-text**
review (the quality-critical, independent-per-unit work), (c) draft +
adversarial critique, (d) dedup + gated delivery. Operator is expert; tone
terse/technical. Predecessor validated 6/6 with full-text Opus scouts;
keyword-API-only was the 1/6 failure mode (REF-C) — binding lesson.

## Phase 2 — architecture & execution mode

Domain → harness patterns (REF-E):

| Stage | Pattern | Realization |
|---|---|---|
| Postgres interest read + unit build | **Pipeline** | operator `!` DB scripts → `pg-interest-reader` validates/assembles `_scout_briefs.json` |
| Per-unit discovery + full-text review | **Fan-out/Fan-in** | `unit-scout` ×N, opus, `run_in_background`, real crawl + read |
| Draft + tone/grounding critique | **Producer-Reviewer** | `draft-writer` ⇄ `draft-reviewer`, ≤3 iters |
| Dedup, gate, dry-run, packet | **Supervisor** | orchestrator (`delivery-supervisor` persona) holds the gate |

**Execution mode = sub-agent, not agent-team.** The harness default is
agent-team, but `TeamCreate`/`SendMessage` are unavailable in this runtime.
The decision tree in `references/agent-design-patterns.md` sanctions
sub-agent mode when inter-agent communication is structurally unnecessary
and work is result-return: scouts are independent per unit (no cross-talk
improves a unit's own scholarship), and producer-reviewer is a clean
two-party return loop the orchestrator sequences. Data passing: **file-based**
(`state/runs/<RID>/*.json`, audit trail) + **return-value** (Agent results).
No hybrid needed — every phase is sub-agent + Supervisor.

Team size: 5 agent roles, ~one scout per active unit (≤6) fanned
concurrently — within guidelines (focused over sprawling).

## Phase 3 — agents (`.claude/agents/`, all `model: opus`)

- `pg-interest-reader` — Pipeline head; validates operator-run DB artifacts
  vs settled criteria; emits `_scout_briefs.json`. No network, no DB.
- `unit-scout` — Fan-out worker; one per unit; crawl.mjs + full-text read +
  D1–D5 + loop to ≥3 in-window non-dup; anti-hallucination ≥7-needs-quote.
- `draft-writer` — Producer; Korean 합쇼체, grounded, no signature, 150–280.
- `draft-reviewer` — Reviewer; BANNED_TERMS + tone + grounding + form;
  pass/fail with precise fixes; escalate→drop at iter 3.
- `delivery-supervisor` — Supervisor persona the orchestrator executes;
  holds the first-external-action gate; never sends.

## Phase 4 — skills (`.claude/skills/`)

- `paper-rec-orchestrator` — the spine (Phase 0 context-check; pipeline⊕
  fanout⊕prodrev⊕supervisor; data flow; error handling; test scenarios;
  pushy description incl. follow-up keywords).
- `paper-rec-scout` — query→crawl→**read full text**→D1–D5→loop/escalation→
  dedup; references ported `pipeline/{scan,score}/SKILL.md`.
- `paper-rec-draft` — Korean academic structure + hard constraints +
  self-check; references `pipeline/post/SKILL.md`, rules 01/03.
- `paper-rec-review` — the rules/00–06 critic + BANNED_TERMS lint mirror.

Ported `pipeline/{scan,score,post}/SKILL.md` are retained as the v1 depth
playbooks the v2 skills reference (D1–D5 rubric, anti-hallucination, post
template) — not duplicated.

## Phase 5 — orchestration & CLAUDE.md

Orchestrator = Supervisor sub-agent pattern. `CLAUDE.md` carries only the
pointer (trigger + boundaries + change log) per the harness rule (no agent/
skill lists, no dir tree — those live in `.claude/` + this doc).

## Phase 6 — validation

- **Structure**: 5 agent files + 4 skill SKILL.md present; `.claude/commands/`
  intentionally empty; every Agent call specifies `model:"opus"`.
- **Dry-run logical check**: no dead links — operator `!`→artifacts→
  pg-interest-reader→`_scout_briefs.json`→scouts→`scout_*`→producer-reviewer→
  `07_drafts.json`→deliver.py dry-run→packet→GATE. Every downstream input
  traces to a named upstream artifact.
- **Trigger check**:
  - should-trigger: "run paper rec", "주간 추천 돌려줘", "추천 실행",
    "dry-run 다시", "BYL unit만 다시 스카우트", "리뷰 반영해 초안 수정".
  - should-NOT (near-miss): "지금 추천 발송해" → refuse autonomous send,
    explain the operator gate; "Paper Blitz/CWLL 일정 리마인더" → out of
    scope, SMJ's domain (rules/00); "csnl_research에 결과 써줘" → forbidden,
    csnl_research is read-only.
- Hardening confirmed: each agent/skill cites the binding rule it enforces;
  internal-ops vocabulary is quarantined to JSON; the gate is stated in
  three places (supervisor agent, orchestrator skill, CLAUDE.md).

## Phase 7 — evolution

Feedback → target: result quality → scout/draft skill; role gap → agent
file; order → orchestrator; trigger miss → skill description. All changes
recorded in `CLAUDE.md` 변경 이력.
