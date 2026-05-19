# MIGRATION PROMPT — Personalized Paper-Recommendation Automation v2 (harness-engineered)

> Hand this whole file to a **fresh Claude Code session (Opus, max token budget)**
> opened at the root of a **new, empty repo**. The session must read every
> `[REF-*]` section, then execute `[PROMPT]`. This is an engineering handoff,
> not a sketch — the predecessor project (`csnl-paper-rec`,
> github.com/CSNL-vnilab/csnl-paper-rec, private) is validated and its lessons
> are binding. Do not re-derive what is already settled here.

---

## [PROMPT]  ← paste this block as the session's first instruction

You are the orchestrator (Opus, max tokens) of a new project: a **per-researcher
personalized academic-paper recommendation automation** for the CSNL lab, driven
by a **PostgreSQL** record of each member's *latest* research interests.

Build it as a **professionally harness-engineered multi-agent system**, not a
script with cosmetic "review". Concretely:

1. **Install and use the harness plugin** (team-architecture factory):
   - `/plugin marketplace add revfactory/harness`
   - `/plugin install harness@harness`
   - Enable agent teams: `export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`
   - Invoke it: *"Design an agent team for: per-researcher paper recommendation
     from a Postgres interest DB — Postgres reader → per-researcher fan-out
     scouts that crawl and read full text → producer–reviewer draft loop →
     gated delivery, with a dedup ledger and tone/rules enforcement."*
   - Map domain → harness patterns (see `[REF-E]`): **Pipeline** (stage spine)
     ⊕ **Fan-out/Fan-in** (one scout per researcher unit) ⊕ **Producer-Reviewer**
     (draft ↔ critic loop until rule-clean) ⊕ **Supervisor** (orchestrator gates
     delivery). Let harness generate `.claude/agents/` + `.claude/skills/`; then
     review and harden them against `[REF-B]` rules.

2. **Data plane = PostgreSQL only.**
   - *Interest source* (read-only): Supabase `csnl_research.projects` — the
     researcher-archiver plugin's source-of-truth of each member's current
     project(s), updated as they self-archive. "Latest interest" =
     active projects (`[REF-A]`) + `last_updated_at` recency + `confidence_avg`.
   - *Recommendation ledger* (read-write): a **Postgres** schema (NOT sqlite —
     migrate the predecessor's sqlite ledger; DDL in `[REF-A]`). Tracks per
     researcher: recommended, marked-read, excluded, feedback. Drives
     never-re-recommend dedup.

3. **Real discovery + real review (no theater).** Each per-researcher scout
   (Opus, max tokens) must: formulate domain-specific scholarly queries from the
   researcher's actual project fields; search keyless scholarly APIs
   (OpenAlex title/abstract + relevance, Europe PMC, arXiv, Semantic Scholar);
   **fetch and actually read full text** (Europe PMC OA-XML → Playwright HTML/PDF
   via pdfjs — port `pipeline/crawl.mjs` from the predecessor); score D1–D5
   against the project grounded in *quoted full text*; **loop** (reformulate +
   re-crawl) until **≥3 in-window, non-duplicate, genuinely-relevant candidates
   per researcher**. Abstract-only ≠ reviewed. Keyword-API-only discovery is a
   known failure mode (`[REF-C]`) — it is forbidden as the sole method.

4. **Rules are code-enforced, not vibes** (`[REF-B]`): academic-Korean tone with
   a machine-greppable `BANNED_TERMS` lint blocking delivery on any hit; **no
   signature**; strict date windows (journal ≤365 d, preprint ≤90 d; relaxed
   only after ≥3 strict zero-hit rounds); grounding in a named project element +
   a full-text point; SYJ+BHL = one unit (two channels); never re-recommend;
   first-external-action gate (dry-run + explicit operator approval + on-disk
   token before any Slack send).

5. **Constraints:** Opus only — **no OpenRouter, no Anthropic API key in any
   unattended path, no Ollama**. Browser via Playwright (chromium cached at
   `$HOME/Library/Caches/ms-playwright`); Chrome MCP unavailable. Nothing is
   sent until the operator approves a dry-run.

**Definition of done** (`[REF-F]`): harness-generated team in `.claude/`;
Postgres interest read + Postgres ledger; per researcher ≥3 full-text-grounded
candidates + a lint-clean grounded draft; dry-run preview + tone-lint pass;
zero sends without the gate; a committed review packet; reproducible.

Work top-down: read all `[REF-*]`, install harness, let it design the team,
harden to the rules, port `crawl.mjs` + the rules + the ledger (→Postgres),
run the Opus scout team, produce the packet, stop at the operator gate.

---

## [REF-A] PostgreSQL data plane

**Interest source (read-only):** Supabase project `qjhzjqkrbvsnwlbpilio`,
schema `csnl_research`, table `projects` (PK `(init, project_slug)`). Credentials
in `~/.claude/csnl-archive/.env` (`SUPABASE_DB_HOST/PORT/USER/PASSWORD`); the
`postgres` pooler role **bypasses RLS** for read-only use. `psycopg2` is often
absent — use a `psql` fallback (`PGPASSWORD=… psql -h … -tAc`, dbname always
`postgres`). "Latest interest" signal fields per row: `purpose`
(`research_question`, `hypothesis`, `scientific_aim`), `manipulation_variables`,
`connected_graph`, `background.prior_studies[].doi`, `modalities`,
`last_updated_at` (recency), `confidence_avg`.

**Active-project criteria (settled — operator decision 2026-05-18 "a", do not
re-litigate):** `phase ∈ {data_collection, analysis, manuscript_draft} AND
confidence_avg ≥ 0.7`. Excludes deprecated stubs / low-confidence.
`lit_review_post_null` is intentionally NOT active (covered indirectly).

**Units / delivery registry:** JOP 박준오 · BYL 이보연 · MSY 여민수 · SMJ 정새미
· JYK 김정예 · **SYJ+BHL = ONE unit** (조수영 · 이보현, JSL follow-up cowork;
one recommendation → both INIT_claude channels + both DM pings). INIT_claude
channels: JOP C0B3FTHAVR8 · BYL C0B3DRPBP9C · MSY C0B4A6WAGNL · SMJ C0B39GQK067
· JYK C0B3FTKE4HY · BHL C0B39GVLKCK · SYJ C0B3FTNR00J. DM channels: JOP
D0AMRACTLBH · BYL D0AN6PMLWCS · MSY D0AP128V9DE · SMJ D0AN0CHTJP5 · JYK
D0AN3B8K0CD · BHL D0AN6PXAESE · SYJ D0AN4N0278E. (Re-verify against
researcher-archiver-plugin/config/researchers.yaml before any send.)

**Recommendation ledger → migrate sqlite to PostgreSQL.** Predecessor schema
(state/schema.sql + scripts/migrate_legacy_ledger.py) holds 8 prior
recommendations + 1 read + 3 JOP exclusions already imported from the legacy
harness. Recreate as Postgres tables (same columns, add `unit_id`,
`member_init`): `paper_recommendations(unit_id,member_init,paper_doi PK-ish,
paper_title,paper_date,tier,channel_id,slack_ts,posted_at)`,
`paper_recommendations_read`, `recommendation_messages`,
`feedback_events(signal CHECK …)`, `exclusion_rules`. Dedup = DOI-normalized or
≥0.9 fuzzy-title match against all of these for any unit member.

## [REF-B] Non-negotiable rules (carry over verbatim from csnl-paper-rec/rules/)

- **Tone (`rules/01_tone.md`)**: Korean academic 합쇼체; greeting
  `<이름> 연구원께,`; bare labels `논문:/저자:/발행:/DOI:/추천 근거:/활용:`;
  **no signature** (`— Claude`/model names forbidden — 2026-05-13 rule); no
  emoji/exclamation/superlative/AI-self-reference; `paradigm`/`framework` ≤1
  each. A fenced ```` ```BANNED_TERMS ```` block (curated, substring-safe — must
  NOT false-positive a paper titled "GPT…" or an author "Claude X") parsed at
  delivery; any case-insensitive substring hit aborts that unit's send.
- **Date filter (`rules/02_date_filters.md`)**: strict journal ≤365 d / preprint
  ≤90 d; relaxed (2 y / 6 m) only after ≥3 strict queries with zero topical hit;
  beyond relaxed = reject; every candidate carries `tier`.
- **Grounding (`rules/03_grounding.md`)**: `추천 근거` cites a *named* element
  of the researcher's actual project (manipulation variable, connected_graph
  paradigm, a `background.prior_studies` DOI, or research-question phrase) **plus
  one concrete full-text point**. Inferred-fit, not asserted-certain.
- **Dedup/feedback (`rules/04_dedup_feedback.md`)**: never re-recommend; reply →
  classify → update exclusions → re-run; carryover ranks 2–5.
- **Delivery (`rules/05_delivery.md`)**: INIT_claude channel post + ≤2-line DM
  ping with `{permalink}`; sequential one unit at a time, ≥7 s gap, verify Slack
  ok + permalink, then write ledger; **first-external-action gate** (dry-run is
  default; real send needs `--send --operator-approved` + `state/.APPROVED_<RID>`).
- **Philosophy (`rules/06_philosophy.md`)**: grounded over abstract; no
  recommendation > a bad one; topic fit inferred until researcher feedback;
  PB/CWLL schedule reminders are SMJ's domain — out of scope.

## [REF-C] Hard-won lessons (do not repeat)

- **Keyless keyword-API discovery returns biomedical/off-domain noise.** A run
  using generic keywords over metadata APIs yielded **1/6 researchers** with any
  recommendation. The Opus scoring team correctly refused to launder false
  positives (anti-hallucination, composite ≥7 specificity gate). Diagnosis: low
  discovery precision, not bad scoring.
- **Fix that worked (validated 2026-05-19): Opus scouts + real full-text
  crawl.** OpenAlex `title_and_abstract.search` + relevance (NOT date-desc),
  Europe PMC OA full-text XML, arXiv relevance, Semantic Scholar; round-robin
  merge; Playwright HTML/PDF (pdfjs) for full text; each scout reads 40k–125k
  chars/paper and loops queries. Result: **6/6 units, ≥3 candidates each,
  composite 8–9**, with grounding that matched researchers' exact phenomena
  (e.g. a paper reproducing BYL's 50 ms→500 ms bias reversal; a paper using the
  identical DoG fit for SYJ+BHL's reference→target structure).
- **Sub-agent self-reports are not trust-but-verify.** One workstream reported
  creating `rules/01_tone.md`; it had not. Always verify artifacts on disk.
- **Drafts run long** (700–950 c) when grounding is dense; a dedicated Opus
  tighten/critic pass brings 추천 근거 to 150–280 c without losing the named
  grounding or the load-bearing quote. Build this as the Producer-Reviewer loop.
- macOS has no `timeout`/`pdftotext`/`psycopg2` by default; use Node fetch +
  pdfjs + `psql`. Chrome MCP needs a running Chrome (absent) and is unfit for
  parallel crawlers — Playwright headless is the stack.

## [REF-D] Reusable assets (port; don't rebuild)

From `github.com/CSNL-vnilab/csnl-paper-rec` (clone read-only for reference):
`pipeline/crawl.mjs` (the full-text crawler — the crown jewel), `rules/00–06`,
`pipeline/_util.py`, `pipeline/00_select_projects.py` (with psql fallback),
`pipeline/01_extract_topics.py` (unit/SYJ+BHL merge), `scripts/init_db.py` +
`state/schema.sql` (port to Postgres), `scripts/migrate_legacy_ledger.py`,
`scripts/deliver.py` (dry-run/gate + BANNED_TERMS lint),
`pipeline/{scan,score,post}/SKILL.md`, `docs/DECISIONS-2026-05-18.md`. The
predecessor's `drafts/20260519-1408/` packet shows the target output quality.

## [REF-E] Harness-pattern mapping

| Stage | Harness pattern | Agent role |
|---|---|---|
| Postgres interest read + unit build | Pipeline | reader (deterministic) |
| Per-researcher discovery + full-text review loop | **Fan-out/Fan-in** | N Opus scouts (one per unit), max tokens, real crawl |
| Draft creation + tone/grounding critique loop | **Producer-Reviewer** | drafter ↔ reviewer until lint+rules clean |
| Dedup, gate, dry-run, ledger write | Supervisor | orchestrator (you) — holds the send gate |

Validate per harness: trigger checks, dry-run, with/without-skill comparison.

## [REF-F] Acceptance criteria & operator gate

1. Harness-generated `.claude/agents/` + `.claude/skills/`, hardened to `[REF-B]`.
2. Reads interests from Postgres (`csnl_research.projects`), ledger in Postgres.
3. Every researcher unit: **≥3 in-window, non-dup, full-text-reviewed
   candidates** + one grounded, lint-clean draft (추천 근거 150–280 c).
4. `deliver.py`-equivalent dry-run prints full preview + `Tone lint: OK` for all
   units; **zero sends** without `--send --operator-approved` + on-disk token.
5. Committed review packet (per-unit ≥3 candidates + chosen draft + grounding).
6. Reproducible (documented commands; no secrets committed; node_modules + state
   gitignored). Private GitHub repo.

**Stop at the gate.** Present the dry-run packet to the operator; do not send
until explicitly approved (and even then, sequential, one unit at a time).
