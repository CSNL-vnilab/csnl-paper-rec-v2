# csnl-paper-rec-v2

Per-researcher personalized academic-paper recommendation automation for the
CSNL lab. A **harness-engineered multi-agent system** driven by a **PostgreSQL**
record of each member's *latest* research interests
(`csnl_research.projects`, the researcher-archiver-plugin's source-of-truth),
with a **PostgreSQL recommendation ledger** (`csnl_paper_rec` schema).

v2 of the validated predecessor (`CSNL-vnilab/csnl-paper-rec`): the deterministic
keyword-API discovery (a known failure mode — 1/6 researchers) is replaced by
**per-researcher Opus scouts that crawl and read full text** (validated 6/6),
and the sqlite ledger is migrated to PostgreSQL.

## Architecture (harness patterns)

```
csnl_research.projects (Postgres, READ-ONLY)
  │  Pipeline ── 00_select_projects (active = phase∈{data_collection,
  │              analysis,manuscript_draft} ∧ confidence_avg≥0.7) ──┐
  │              01_extract_topics (per UNIT; SYJ+BHL = one unit)   │
  ▼                                                                 ▼
Fan-out/Fan-in ── N Opus scouts (one per unit, max tokens):
     formulate domain queries → crawl.mjs search
     (OpenAlex/EuropePMC/arXiv/S2) → fetch + READ FULL TEXT
     (EuropePMC OA-XML → Playwright HTML/PDF via pdfjs) →
     D1–D5 grounded in quoted full text → loop until ≥3
     in-window, non-duplicate, genuinely-relevant candidates
  │                          │  dedup vs csnl_paper_rec ledger
  ▼                          ▼
Producer-Reviewer ── drafter ↔ reviewer loop per unit
     (Korean academic, no signature, grounded in a named project
      element + a full-text point) until tone-lint + rules clean
  │
  ▼
Supervisor (orchestrator) ── dedup ledger · dry-run · BANNED_TERMS
     lint · first-external-action gate · sequential delivery
        └─ INIT_claude channel post + ≤2-line DM ping
           (DRY-RUN default; real send hard-gated)
```

`.claude/agents/` + `.claude/skills/` hold the harness-generated team
(see `docs/HARNESS-DESIGN-v2.md`). `CLAUDE.md` registers the orchestrator
pointer.

## Data plane — PostgreSQL only

| Schema | Access | Role |
|---|---|---|
| `csnl_research` | **READ-ONLY** (SELECT) | interest source (latest projects) |
| `csnl_paper_rec` | read-write | recommendation ledger (dedup, feedback, exclusions) |

Same Supabase project (`qjhzjqkrbvsnwlbpilio`); `postgres` pooler role
bypasses RLS. `psql` fallback used where `psycopg2` is absent. No sqlite.
No Anthropic/OpenRouter key in any unattended path. No Ollama.

## Rules (binding — `rules/`)

Academic Korean 합쇼체, **no `— Claude` signature**, no emoji/affect/AI-jargon,
`paradigm`/`framework` ≤1×. Strict recency (journal ≤365 d / preprint ≤90 d;
relaxed only after ≥3 strict zero-hit rounds). Never re-recommend (Postgres
ledger + read + exclusions + reading-DB). Ground every recommendation in a
named project element **plus** one full-text point; fit is *inferred*, not
asserted. Sequential delivery, never batch. A machine-greppable
`BANNED_TERMS` block in `rules/01_tone.md` aborts a unit's send on any hit.

## Run (manual, validation phase)

```sh
cp .env.example .env          # fill SUPABASE_DB_* (+ SLACK_BOT_TOKEN for real send)
npm install                   # playwright + pdfjs-dist (chromium cached)
python scripts/init_db.py     # create csnl_paper_rec schema + tables (idempotent)
python scripts/migrate_legacy_ledger.py   # one-shot: predecessor sqlite → Postgres
python pipeline/00_select_projects.py <RID>   # READ-ONLY csnl_research → 01_active_projects.json
python pipeline/01_extract_topics.py  <RID>   # → 02_topic_bundles.json
# orchestrator launches the Opus scout team (P5) + producer-reviewer (P6),
# writing state/runs/<RID>/{03_candidates,06_scored,07_drafts}.json
python scripts/deliver.py --run-id <RID>      # DRY-RUN preview only (default)
# real send is hard-blocked: needs --send --operator-approved + state/.APPROVED_<RID>
```

## Status

Migration v2. **Nothing sends** until the dry-run preview is reviewed and the
operator creates the approval token. See `docs/DECISIONS-2026-05-18.md`
(carried-over non-negotiables), `docs/DECISIONS-v2.md`, and the committed
review packet under `drafts/`.
