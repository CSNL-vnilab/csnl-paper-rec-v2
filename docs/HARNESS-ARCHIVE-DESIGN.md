# P13 — Paper archive + per-researcher interview plugin

**Date:** 2026-05-21 (KST)
**Status:** v0 — scaffold complete, awaiting Opus×2 review + codex
adversarial review before any real-environment run.
**Owners:** harness operator (vnilab@gmail.com) — DB apply, ingestion;
researcher — `/paper-interview <init>` on a plugin install.

This layer is *additive* to the existing csnl-paper-rec-v2 harness. It
does not touch the recommendation ledger (`paper_recommendations`,
`feedback_events`, `cycle_state`, `evolution_log`), the Slack delivery
path, or `csnl_research`. Tables are prefixed `archive_` and live in the
same `csnl_paper_rec` schema for now; they can be moved to their own
schema later without code change (the SQL is templated on `__SCHEMA__`).

## Motivation

The weekly recommendation cycle only surfaces *new* papers. The
researchers are largely unaware of classic and prior literature in
their own questions, and recommendations land flat because the system
cannot point out "this is the paper your favourite recent paper is
arguing against." The archive layer pre-builds a per-researcher reading
queue from three independent corpora, runs an interactive triage on it,
and stores the triage signal for future recommendations to cite back
("Cite-and-contrast" connection points).

## Data sources

| Source | Path | Volume | Trust |
| --- | --- | --- | --- |
| Classic SMB archive | `/Volumes/Papers/` (mounted from `//joonoh@147.47.70.15`) | 4,878 PDFs as of 2026-05-21 | High (lab curation) |
| CWLL recommendation log | `/Volumes/CSNL_new-1/Memory/CWLL/logs/metadata_log.csv` | 237 confirmed rows, ~1,306 unique DOIs (7 years) | Mixed — contains non-paper DOIs (books, standards) |
| PI-network publications | `/Users/csnl/Downloads/pi_network_data.json` + OpenAlex | 182 PIs (133 seed + 49 bridge), ~80 papers/PI × 10y window ⇒ ~10K | High (queried via OpenAlex polite pool) |

## Pipeline

```
classics_smb ─┐
              ├──► merge_dedupe_filter.py ──► archive_papers
cwll_rec_log ─┤                       │
              │                       ├──► archive_filter_decisions
pi_network ───┘                       │
                                      ▼
                          compute_embeddings.py
                                      │
                                      ▼
                          archive_paper_embeddings
                                      │
                                      ▼
                          build_researcher_queue.py ──► archive_researcher_queues
                                                            ▲
                                                            │ per (init)
                                                            ▼
                          /paper-interview <init> (plugin)
                                ├─ profile_show / profile_confirm  → archive_interview_sessions, archive_profile_verifications
                                ├─ pick_next + record_choice       → archive_responses
                                └─ meta_review (every 10)          → archive_meta_reviews
```

All ingestion steps default to **dry-run JSONL**. The operator opts into
DB writes with `--apply`.

## Schema

See `state/schema_archive.sql`. Key tables:

- **archive_papers** — canonical merged metadata. `canonical_id =
  sha256(doi)` if DOI is known, else `sha256(norm_title|year)`.
- **archive_paper_sources** — `(canonical_id, source, source_ref)`
  composite key. Holds the raw payload per source for audit.
- **archive_filter_decisions** — `is_textbook / is_draft / is_poster /
  is_lab_relevant` plus `lab_scope_tags` and a JSON `filter_reason`.
- **archive_paper_embeddings** — `(canonical_id, model_name)` composite.
  `embedding_json` is a JSONB float array; pgvector is not required.
- **archive_researcher_queues** — `(researcher_id, canonical_id)`
  primary; `chunk in (recent|mid|classic)`, ranked by similarity.
- **archive_interview_sessions / _profile_verifications / _responses
  / _meta_reviews** — interview-side state.

## Boundaries (unchanged from v2)

- Never write to `csnl_research`.
- Never call Anthropic / OpenAI / OpenRouter / Slack from the cron or
  ingest path. Embedding backend is selectable per env, with `local`
  (sentence-transformers BAAI/bge-m3) being the default.
- The plugin uses the researcher's own machine to talk to Postgres.
  Operator decides whether to issue dedicated, scoped Supabase roles
  per researcher or to share a read-mostly role.
- BANNED_TERMS / signature rules still apply to any researcher-facing
  text. The interview skill enforces Korean-only and ≤ 4 paragraphs
  per message.

## Filter rules (rule-based, conservative)

- **Textbook:** `page_count >= 300` OR title regex
  `(textbook|handbook|encyclopedia|companion to|primer)` OR DOI prefix
  in `(10.1017/cbo, 10.4324, 10.1201, 10.5040, 10.1142, 10.36019,
  10.12987, 10.1787, 10.1109/eeei, 10.21136)` — collected from real
  false-positive DOIs in the CWLL rec log. Preprint DOIs (10.31234)
  are never marked as textbook.
- **Draft:** filename matches `(_draft_|_submitted_|_v\d+)` OR title
  contains `manuscript draft` / `미발표`.
- **Poster:** filename matches `(_poster_|_sfn\d|_vss\d|_hbm\d|_cosyne\d)`
  OR venue contains `poster`.
- **Lab-relevant:** title|abstract|venue substring-matches at least
  one keyword in the `LAB_SCOPE_TAGS` bag (`BDM/NN/fVC/VWM/SD/CG/METH`)
  OR source is `classics_smb` / `pi_network` (presumption of trust).
  Anything else is marked `is_lab_relevant = false`; the row stays in
  the archive but never reaches a researcher queue.

The keyword bag and DOI prefix list are intentionally small and live
in code (`scripts/archive/_common.py`) so they are reviewable.

## Embeddings

Default backend is **BAAI/bge-m3** via sentence-transformers — 1024-dim,
multilingual (English + Korean), open weights, no API key. Lab Python
3.14 may lack a compatible torch wheel; the script documents using a
Python 3.11/3.12 venv for the embedding step. Alternate backends
(Voyage, Jina, OpenAI) are gated behind explicit `CSNL_EMBED_BACKEND`
+ API key env vars; the default policy (no LLM keys) is preserved.

The same model embeds the researcher's interest query at queue-build
time, so the encoder is consistent on both sides of the cosine sim.

## Three age chunks

- **recent**  : `today.year - paper.year ∈ [0, 5)`
- **mid**     : `[5, 10)`
- **classic** : `[10, ∞)` and `year is null`

Within each chunk, papers are ranked by cosine similarity descending and
top-N (default 120) is retained per chunk → 360 papers per researcher.
The interview walks chunks in order recent → mid → classic.

## Marketplace plugin layout

```
plugin/
  .claude-plugin/plugin.json   # manifest
  README.md
  .env.example
  commands/paper-interview.md  # slash command entrypoint
  skills/paper-archive-interview/SKILL.md
  agents/paper-explainer.md
  scripts/
    _pdb.py                 # minimal DB client (psycopg2 + psql fallback)
    profile_show.py         # stage 1: show profile + open/reuse session
    profile_confirm.py      # stage 1: record corrections
    pick_next.py            # stage 2: next paper from queue
    record_choice.py        # stage 2: save MCQ answer + update counters
    meta_review.py          # stage 4: every-10 meta-review writer
    session_close.py        # stage 5: mark session complete
```

The skill drives the flow; the scripts are the only thing that touches
the DB. The explainer agent runs in its own context window so option-4
"tell me more" answers don't bloat the interview thread.

## Operator runbook (apply order)

```
# 1. apply schema (idempotent)
! python scripts/init_db.py

# 2. ingest each source (dry-run first)
! python scripts/archive/ingest_classics.py --no-read-pdf       # 4878 files, ~30s
# install pypdf in a venv and re-run with PDF probe to harvest DOIs/page counts:
! python scripts/archive/ingest_classics.py --apply              # writes archive_papers/_sources
! python scripts/archive/ingest_rec_log.py --enrich --apply      # 1306 DOIs, ~5 min @ OpenAlex polite pool
! python scripts/archive/ingest_pi_network.py --apply            # 182 PIs, ~20–40 min

# 3. merge + dedupe + filter
! python scripts/archive/merge_dedupe_filter.py --apply

# 4. embeddings (operator workstation — needs sentence-transformers in a Py 3.11/3.12 venv)
! python scripts/archive/compute_embeddings.py --apply           # one-time + new papers later via --only-missing
# Remote backend (voyage / jina / openai) is GATED — see "remote-embedding gate" below.

# 5. per-researcher queues (re-runnable whenever the researcher's projects move)
! python scripts/archive/build_researcher_queue.py --all --apply
# or one researcher:
! python scripts/archive/build_researcher_queue.py BHL --apply
```

### Remote-embedding gate

`compute_embeddings.py` and `build_researcher_queue.py` default to
`CSNL_EMBED_BACKEND=local` (BAAI/bge-m3 via sentence-transformers, on
the operator workstation). The three remote backends — `voyage`, `jina`,
`openai` — send paper text and the researcher's interest text to a
third-party API, which violates the lab's default no-LLM-key policy
(CLAUDE.md + rules/00). To use them, the operator must:

1. `touch state/.ARCHIVE_EMBED_APPROVED` (token file; contents ignored)
2. Pass `--operator-approved-remote-embed` on the CLI

Both are required. Either alone refuses to run.

Researcher install (one-time):

```
/plugin install /Users/csnl/Documents/claude/csnl-paper-rec/plugin
cp <plugin-dir>/.env.example <plugin-dir>/.env  # fill in DB creds
/paper-interview BHL
```

## Open questions for review

1. Should `archive_*` move into a dedicated `csnl_paper_archive` schema
   instead of co-existing with the rec ledger in `csnl_paper_rec`? Pros:
   cleaner permission boundary for researcher-issued roles. Cons: extra
   migration step, two-schema GRANTs.
2. The lab-relevance keyword bag is hand-curated. Should we instead let
   each researcher's queue-build pass *re-score* every paper against the
   researcher's individual focus and bypass the keyword-bag flag for
   their queue? Trade-off: 100% recall vs build time.
3. Option-4 explainer assumes `pipeline/crawl.mjs` is reachable from the
   plugin's runtime. On a researcher's laptop without the parent repo,
   the explainer falls back to the abstract — is that acceptable, or do
   we want to bundle a thin Node helper inside the plugin?
4. Meta-review proposals are deterministic (rule-based). Should we also
   capture a free-text comment from the researcher when the proposal
   says `tighten_chunk`, so the operator has a richer signal when
   re-running `build_researcher_queue.py`?
5. The current schema has no per-researcher review of the meta-review
   itself (we record proposal + applied flag, but not whether the
   researcher accepted/rejected). Worth adding `researcher_decision`
   ('accepted'|'rejected'|'modified') as a column?

## Verification plan

- Schema apply on a sandbox DB → confirm 9 new tables present, idempotent
  re-apply.
- Smoke ingest: `ingest_classics.py --limit 50`, `ingest_rec_log.py
  --limit-dois 20 --enrich`, `ingest_pi_network.py --limit-pis 3`.
- Merge dry-run, inspect `merge_report.json`.
- Embedding dry-run with `--limit 50` in a Py 3.11 venv.
- Queue build for one researcher (smallest active init), inspect JSONL
  for sanity.
- Plugin install on a fresh laptop, run `/paper-interview <init>` for
  the first 5 MCQs, verify rows land in DB.
- 20-MCQ session by the user (assigned post-review).

## Change log

| Date | Change | Files |
| --- | --- | --- |
| 2026-05-21 | P13 scaffold — schema + 3 ingestors + merge + embed + queue + marketplace plugin tree | state/schema_archive.sql, scripts/archive/*, plugin/* |
| 2026-05-21 | P13 reviewer round (Opus×2 + codex adversarial). 11 critical issues patched: abstract-overwrite UPSERT (COALESCE + longer-wins guard), safer psql substitution (token-split), fuzz-collapse orphan cleanup (transactional), build-queue race (build_token UUID), DB write allowlist + multi-statement guard in plugin _pdb.py, raw-JSON-leak prevention + Korean tag rendering in SKILL.md, explainer crawler degraded-abstract mode + quote-preservation, MCQ tolerant matcher, meta-review idempotent upsert (UNIQUE INDEX migration), session-staged paper verification in pick_next/record_choice, remote-embedding operator-approval gate. | state/schema_archive.sql, scripts/archive/{_common,merge_dedupe_filter,ingest_rec_log,ingest_pi_network,compute_embeddings,build_researcher_queue}.py, plugin/scripts/{_pdb,pick_next,record_choice,meta_review,profile_show}.py, plugin/skills/paper-archive-interview/SKILL.md, plugin/agents/paper-explainer.md, plugin/README.md, plugin/.env.example |
