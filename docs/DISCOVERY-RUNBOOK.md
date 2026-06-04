# Discovery-Engine Runbook (P26) — operator-facing

How to run a **per-researcher deep-discovery cycle** and integrate the results.
This is the *operational* counterpart to `docs/HARNESS-DISCOVERY-DESIGN.md` (design)
and the CLAUDE.md P26 change-log (history). Cadence: **monthly** (operator-run);
the unattended weekly Wed cron stays LLM-free and only slices the pre-built pool.

## What the engine produces
- `csnl_paper_rec.archive_relevance_decisions` — per (researcher, paper) A/B/C/none
  with a one-line reason (the auditable reasoning gate). `source` = archive | live.
- Live (≤5y, not-in-corpus) accepts → ingested into `archive_papers`
  (`source='live_search'`) + a synopsis + an embedding, so they flow through the
  interview (`pick_next.py`) and weekly digest (`build_digest.py`) like archive papers.

## Boundaries (unchanged)
- `csnl_research` READ-ONLY; writes only to `csnl_paper_rec`.
- `archive_responses` is the held-out truth — NEVER written by the discovery layer.
- No researcher-facing sends here (digest send gated by `state/.P23_ENABLED`).
- Reversible: `DELETE FROM csnl_paper_rec.archive_papers WHERE source='live_search';`
  (+ matching `archive_paper_synopses` / `archive_paper_embeddings` / queue rows).

## A. Profiles (once per researcher, refresh when interests change)
Per researcher build `state/archive/profiles/<INIT>.json` = `{aims, phenomena,
mechanisms_theories, open_questions, known_negatives}`, Opus-extracted from
`csnl_research.projects` + that researcher's `archive_responses` reasons.

## B. Discovery (per researcher, Opus scout — the reasoning gate)
SOP: `state/archive/discovery_run/scout_prompt.md` (HARDENED — A/B/C contract,
C-guard model-name≠mechanism + same-computational-job, profile-anchor, no
title-only accepts, off-field⇒none). Each scout:
1. generates aim/phenomenon/mechanism search threads (NOT human/fMRI keywords);
2. `node pipeline/crawl.mjs search --query "<thread>" --since-journal <5y-ago> --since-preprint <5y-ago> --limit 25` (slow; one at a time);
3. dedups by DOI, skips decided (`archive_relevance_decisions[INIT]` + `archive_responses`);
4. judges A/B/C from title+abstract (fulltext for borderline); 꼬리-에-꼬리 loop-until-dry;
5. writes via `scripts/archive/record_relevance.py` (stdin JSON batch) → DB + `found/<INIT>.jsonl`.
Re-audit pass (optional, recommended): `state/archive/discovery_run/reaudit_prompt.md`
re-judges existing accepts under the hardened rules (fixes over-broad C; fills
missing abstracts via `scripts/archive/backfill_abstracts.py <INIT>`).

## C. Adversarial milestone review (per round)
Build a packet (`scripts/archive/build_review_packet.py`) and have codex
(`codex:codex-rescue`) — or an Opus skeptic if codex's model is unavailable —
audit a risk-weighted sample (C/mechanism + cross-species accepts) for
false positives. Apply the SOP fixes it finds before integrating.

## D. Symmetric archive gating (so archive classics aren't buried)
The gate must cover BOTH live and high-cosine ARCHIVE candidates, else the
queue bonus unfairly favors gated-live over never-judged-archive classics. Per
researcher, gate-judge the top ungated archive candidates (stored abstracts, no
crawl) → `archive_relevance_decisions(source='archive')`. (See P26e: top-70/
researcher done; extend to deeper ranks as needed.)

## E. Integrate (make discoveries reachable)
1. **Migration once**: `python3 scripts/run_migration.py state/migrations/2026-06-04_p26d_live_ingest.sql` (adds `archive_papers.source`).
2. **Ingest live papers**: `python3 scripts/archive/ingest_live_papers.py` (dry-run) → `--apply` (embeds+backfills authors/pub_date, source='live_search').
3. **Synopses**: write `state/archive/synopses/<cid>.json` per new paper (SOP `scripts/archive/synopsis_prompt.md`; for gate-passed papers do NOT field-OOS) → `python3 scripts/archive/import_synopses.py --apply`.
4. **Wire into queues**: `python3 scripts/archive/wire_live_to_queue_inputs.py --apply` (embed + append to the JSONL mirrors + DB) then `python3 scripts/archive/build_researcher_queue.py --all --apply`.
   - The queue builder applies the reasoning-gate `_RELEVANCE_BONUS` (A:.04/B,C:.03) + COS_FLOOR exemption to A/B/C papers, and cross-chunk title_norm dedup. `pick_next.py` (plugin 0.6.0+) mirrors the bonus so the interview order matches.

## F. Verify
- tier×source + chunk×source per researcher (live shouldn't flood S; archive
  classics should hold S).
- `archive_responses` row counts UNCHANGED (무손상).
- 0 title_norm dupes in the queues.
- Spot-check a few researchers' top-30 (live + archive interleave sensibly).

## Known follow-ups (2026-06-04)
- Full-corpus symmetric gating (only top-70/researcher gated so far).
- MSY first interview (queue ready).
- codex model-access restore (Opus skeptic used as fallback throughout P26).
- Go-live (operator): share Notion DBs + `state/.P23_ENABLED` + SMTP creds.
