# csnl-paper-rec

Paper-recommendation system for the CSNL lab (Center for Computational
Social Neuroscience, Korea University).

The day-to-day interface is an **interview**: the researcher launches a
Claude Code plugin, sees one paper at a time from a pre-computed
candidate pool, and picks one of three labels — `읽음 / 읽을 예정 /
관련 없음`. Every 10 answers, the system updates its model of the
researcher's preferences and re-ranks the next batch. All answers are
written to PostgreSQL and never overwritten.

> v2 of `CSNL-vnilab/csnl-paper-rec`. The v2 pivot was from
> external paper-discovery → DM delivery (failure mode: ranking
> noise that researchers had no way to correct) to an in-session
> interview over the lab's accumulated archive (8,674 papers,
> 2,063 with a structured synopsis as of 2026-05-29).

---

## How a researcher experiences it

```
┌─ terminal ────────────────────────────────────────────────────────┐
│  /csnl-paper-archive-interview:paper-interview <init>             │
│   (one of: BHL · BYL · JOP · JYK · MSY · SMJ · SYJ)               │
└────────────────────────────┬──────────────────────────────────────┘
                             │
   ┌─────────────────────────▼────────────────────────────────────┐
   │  Stage 0 — Environment check (doctor.py)                     │
   │    Python · psycopg2 · ~/.csnl-paper-archive/.env · DB       │
   │    reachability · existence of your queue                    │
   └─────────────────────────┬────────────────────────────────────┘
                             ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  Stage 1 — Profile confirmation (asked once per researcher)  │
   │    Shows your active projects extracted from                 │
   │    csnl_research.projects. You say "맞아요" or correct        │
   │    any missing / wrong item. Skipped on subsequent sessions. │
   └─────────────────────────┬────────────────────────────────────┘
                             ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │  Stage 2 — Paper-by-paper labelling (the actual interview)      │
 │                                                                 │
 │   ┌─────────────────────────────────────────────────────────┐   │
 │   │  pick_next.py — selects ONE paper from your queue,      │   │
 │   │  re-ranking the unanswered subset against your current  │   │
 │   │  dim_preferences on every call                          │   │
 │   └────────────────────────┬────────────────────────────────┘   │
 │                            ▼                                    │
 │   ┌─────────────────────────────────────────────────────────┐   │
 │   │  Block 1   APA 7 citation (English, verbatim — copy     │   │
 │   │            directly into a manuscript reference list)   │   │
 │   │                                                         │   │
 │   │  Block 2   2-4 Korean sentences explaining WHY this     │   │
 │   │            paper is being recommended to YOU, grounded  │   │
 │   │            in (i) one specific project of yours,        │   │
 │   │            (ii) one specific contribution of the paper, │   │
 │   │            (iii) one explicit connection clause.        │   │
 │   │            Uses archive_paper_synopses.frameworks +     │   │
 │   │            key_findings as primary source.              │   │
 │   │                                                         │   │
 │   │  Block 3   3-option MCQ                                 │   │
 │   │              1. 저장 (will read)                        │   │
 │   │              2. 관련 없음                               │   │
 │   │              3. 이미 읽음                               │   │
 │   └────────────────────────┬────────────────────────────────┘   │
 │                            ▼                                    │
 │                  archive_responses                              │
 │                  (researcher_id, canonical_id) PK               │
 │                  — never overwritten by any later operation     │
 │                                                                 │
 │   Every 10 answers → belief-updater agent reads recent          │
 │   responses, updates dim_preferences in                         │
 │   archive_profile_verifications. The very next pick_next.py     │
 │   call uses the new preferences. No operator step required.     │
 │                                                                 │
 │   LOOP — until you stop or run out of unanswered papers         │
 └─────────────────────────────────────────────────────────────────┘
```

---

## What is stored (PostgreSQL — `csnl_paper_rec.archive_*` schema)

| Table | Rows (2026-05-29) | What it holds | Lifecycle |
|---|---:|---|---|
| `archive_papers` | 8,674 | Title, authors, year, venue, abstract, DOI from three sources merged + deduplicated: classics PDFs (4,878), 7-year recommendation log (1,306 DOIs), PI-network publications (~10y, ~182 people). | Refreshed only when archive is re-ingested. |
| `archive_paper_synopses` | 2,063 | Per-paper FLAT JSON: `frameworks[]`, `core_question`, `key_findings[]`, `interpretations[]`, `connecting_signals[]`, `limitations_noted[]`, `abstract_coverage`. ~1,324 in-scope + ~739 out-of-scope. Out-of-scope rows are auto-excluded from interviews. | Updated by operator. `archive_responses` is untouched on import. |
| `archive_researcher_queues` | 1,400 (7 × 200) | Per-researcher candidate pool with tier (S/A/B/C = 10/30/60/100) and composite score from BM25 phrase-fingerprint + SPECTER2 cosine. | Rebuilt by `build_researcher_queue.py --apply` when archive grows or a researcher's `csnl_research.projects` substantively changes. In-session re-rank is dynamic via `pick_next.py`. |
| `archive_responses` | grows monotonically | One row per MCQ answer: `(researcher_id, canonical_id, choice, detail_json, answered_at)`. PK = `(researcher_id, canonical_id)`. | **Never overwritten** by synopsis import, queue rebuild, or plugin update. |
| `archive_profile_verifications` | one active row per researcher | `dim_preferences` (focus / method / subject distribution learned from the researcher's last 10 + answers). | Append-only versions; the most recent row is the live preference vector. |

---

## Setup (researcher side)

You need Python 3.8+, network access to the lab's Supabase, and the
operator-issued `.env` password. Once, in a real terminal (not the
Claude Code chat):

```sh
# 1. Add the marketplace + install the plugin (in Claude Code)
/plugin marketplace add CSNL-vnilab/csnl-paper-rec-v2
/plugin install csnl-paper-archive-interview@csnl-marketplace

# 2. Install runtime dependencies (terminal — password entry needs a real TTY)
python3 -m pip install --user --upgrade -r \
  ~/.claude/plugins/cache/csnl-marketplace/csnl-paper-archive-interview/*/requirements.txt

# 3. Run the setup script (terminal — prompts for your Supabase password)
python3 ~/.claude/plugins/cache/csnl-marketplace/csnl-paper-archive-interview/*/scripts/setup.py
```

Then back in Claude Code, run the interview:

```
/csnl-paper-archive-interview:paper-interview <your-init>
```

Where `<your-init>` is one of `BHL / BYL / JOP / JYK / MSY / SMJ / SYJ`.

Diagnostic:

```sh
python3 ~/.claude/plugins/cache/csnl-marketplace/csnl-paper-archive-interview/*/scripts/doctor.py --init <your-init>
```

Detailed Korean walkthrough: [`docs/RESEARCHER-GUIDE.md`](docs/RESEARCHER-GUIDE.md).

---

## What this system does NOT do

These are deliberate boundaries, not roadmap items:

- **No external messaging.** No Slack, no email, no DM. The interview
  is a researcher-internal loop. (The repo also contains a separate
  operator-triggered DM-delivery path from v2 for periodic weekly
  recommendations — that path is *not* this archive interview and is
  triggered by explicit operator commands documented in `CLAUDE.md`.)
- **No autonomous paper discovery during the interview.** The candidate
  pool is pre-computed by the operator. `pick_next.py` re-ranks within
  that pool but never adds papers.
- **No LLM in the unattended path.** Ranking, tagging, tier
  assignment, OOS exclusion are rule-based Python. The plugin renders
  Korean text from pre-computed fields and records MCQ choices.
- **`archive_responses` is never overwritten.** Synopsis re-imports,
  queue rebuilds, plugin updates, and migrations all leave previous
  answers intact. PK `(researcher_id, canonical_id)` is the contract.
- **`csnl_research` is read-only from this system.** No write path.
  Only `csnl_paper_rec.archive_*` is written.

---

## Operator setup (one-time, then periodic)

Below are the operator-side scripts that fill the four tables. None of
these run during a researcher session.

```sh
# Schema (idempotent)
python3 scripts/init_db.py
python3 scripts/run_migration.py  # apply any pending migrations under state/migrations/

# Ingest the archive (run when new sources arrive)
python3 scripts/archive/ingest_classics.py    # classics_smb PDFs
python3 scripts/archive/ingest_rec_log.py     # 7y recommendation log DOIs
python3 scripts/archive/ingest_pi_network.py  # PI publications (~10y)
python3 scripts/archive/merge_dedupe_filter.py
python3 scripts/archive/compute_embeddings.py # SPECTER2 768-d
python3 scripts/archive/build_corpus_idf.py
python3 scripts/archive/build_fingerprints.py # per-researcher phrase fingerprint

# Synopses (run when new papers are added, or when synopsis_prompt.md changes)
#   — operator-driven LLM extraction; see scripts/archive/synopsis_prompt.md
python3 scripts/archive/import_synopses.py --apply

# Per-researcher queue (run after archive grows or projects change)
python3 scripts/archive/build_researcher_queue.py --apply --all
```

Operator inspection:

```sh
# Per-researcher cumulative response counts
python3 scripts/archive/list_status.py

# MCQ-precision drift monitoring
python3 scripts/archive/validate_drift.py
```

---

## Documentation

- [`docs/RESEARCHER-GUIDE.md`](docs/RESEARCHER-GUIDE.md) — 연구원용 한국어 가이드 (설치 / 인터뷰 진행 / 트러블슈팅)
- [`docs/HARNESS-ALGORITHM-DESIGN.md`](docs/HARNESS-ALGORITHM-DESIGN.md) — fingerprint / IDF / BM25 / rate-distortion ranking design (3-Opus alt-review + codex adversarial)
- [`docs/HARNESS-DESIGN-v2.md`](docs/HARNESS-DESIGN-v2.md) — v2 architectural decisions (carried over from the original repo)
- [`CLAUDE.md`](CLAUDE.md) — phase change-log (P13 archive scaffold → P22c synopsis expansion + codex meta-review)
- [`plugin/skills/paper-archive-interview/SKILL.md`](plugin/skills/paper-archive-interview/SKILL.md) — interview SOP (Korean researcher-facing text rules + Stage invariants)
- [`scripts/archive/synopsis_prompt.md`](scripts/archive/synopsis_prompt.md) — synopsis extraction contract (FLAT schema + framework taxonomy rules)

---

## Status

Plugin v0.5.1 (2026-05-29). The archive interview is the active researcher
interface. The previous external-discovery + DM-delivery path remains in
the repo but is operator-triggered only.
