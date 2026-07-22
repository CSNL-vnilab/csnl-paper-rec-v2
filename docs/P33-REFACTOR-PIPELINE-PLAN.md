# P33 — Workspace refactor + ingest-pipeline rebuild (plan)

> Authored 2026-07-20. Operator directive: *"기존 워크스페이스에서 불필요한 정보,
> 스파게티 코드 정리하고 리팩터링 플랜 구축하여 루프로 자율수리해. 그다음 notion DB에
> 각 연구자별 추천에 중복이 없도록, 계속 업로드되는 연구들을 트래킹할 수 있도록 새로운
> 정보를 검색하고 기존 정보(읽음/관심없음 등)을 기반으로 정보를 ingest하는 파이프라인
> 재구축."*
>
> Continues P32 (`state/archive/_tmp/p32_actions.json`, 12/44 done) and executes the
> already-vetted P29 hardening posture (`HARNESS-MEMORY-REBUILD-PLAN.md`): **additive,
> reversible, surgical — NOT a ground-up rewrite** (the operator has declined the big
> rewrite; the review-endorsed move is "commit + harden the P28/P26 work already built").

---

## 0. Boundaries (inviolable, unchanged)

- No researcher-facing sends. `.P23_ENABLED` absent → weekly Notion send is dormant; it stays that way.
- Agents do **not** connect to prod Supabase. All DB writes/dry-runs are operator `!`. My verification = **offline unit tests + fixtures + adversarial review**, never a live DB query.
- `csnl_research` read-only; writes only ever land in `csnl_paper_rec`.
- Physical `DROP` is forbidden here — `init_db.py` + `schema_*.sql` resurrect dropped tables (`CREATE … IF NOT EXISTS`). Removal = comment-deprecate + a coordinated 3-file edit, deferred to an operator-gated destructive pass.
- `archive_responses` + filled surveys = read-only truth. Never overwritten (`ON CONFLICT DO NOTHING`).

**Session runtime constraints:** `node` is **not installed** → `crawl.mjs` (live crawler) cannot run here; the watermark/discovery driver is built + dry-run only. `psycopg2` works but prod is the only DB → no live connection from the agent.

---

## PART 1 — Cleanup / refactor (autonomous, reversible)

### 1A. SAFE-AUTONOMOUS (executed this session, no gate)
| # | Action | Reversible via |
|---|---|---|
| C1 | `rm` 5 zero-byte shell-accident files at root (`#`, `enable`, `soft`, `the`, `token`) | `touch` (0-byte lossless) |
| C2 | `.gitignore` guard for those names + untracked GRM runtime state (`state/.GRM_INGEST_ENABLED`, `state/grm_last_run_week`) | git |
| C3 | `rm -rf` 5 `__pycache__` dirs / 56 `.pyc` (gitignored, incl. 4 orphaned) | regenerated on next run |
| C4 | `git mv` 4 zero-live-ref dead leaves → `scripts/_legacy/`: `propose_feedback_acks.py`, `build_packet.py`, `fanin_check.py`, `mark_read.py` | `git mv` back |
| C5 | Doc fixes: `cron/README.md` (wholly stale v3-cron doc → deprecation banner + point at live P23/GRM plists); `docs/RESEARCHER-GUIDE.md:151` (`apply_evolution.py` now in `_legacy`) | git |
| C6 | Commit the built-but-uncommitted working set (P28/P30/P31 migrations, GRM ingest scripts, `publish_md_page.py`, etc.) so git reflects reality (S1) — on the feature branch, not pushed | git revert |

### 1B. OPERATOR-GATED (planned, NOT executed — needs a decision or a grant)
- **F-SKILLDRIFT** — the 8 skill/agent-wired legacy scripts (`deliver`, `dedup_snapshot`, `apply_feedback`, `fetch_replies`, `classify_feedback`, `propose_followups`, `migrate_legacy_ledger`, `build_dm_drafts`) can't move until the paper-rec skills/agents are modernized off the Slack/DM+Phase-7 flow (CLAUDE.md still routes "paper rec 실행" to them). → **skill-modernization sub-project (gated).**
- **R1/R2** — schema-file table definitions (`init_db.py`, `schema_v3.sql`, `schema_archive.sql`) still resurrect the 4 quarantined dead tables; `HARNESS-MEMORY-REBUILD-PLAN.md:57-58` claims two of them are live "break-glass," contradicting the DB quarantine. → operator decides *v3 retired vs. break-glass*, then a 3-file edit + physical drop (destructive pass).
- **Token rotation** (2 exposed `ntn_` tokens), **launchctl** ghost job cleanup, **~307 MB gitignored build caches** (K1/K2 verified *consumed* → keep) — all operator.

---

## PART 2 — Ingest-pipeline rebuild (the four requested properties)

> **⚠ SUPERSEDED — read "PART 2 — REVISED (1차 리뷰 반영)" at the end of this doc.**
> The 5-lens adversarial 1st review (2026-07-20, `SHIP-WITH-FIXES / converged=false`)
> found the R-BUILDER and R-BEHAVIOUR specs below **broken as written** (MF-1, MF-2)
> plus 11 more must-fixes. The revised section is authoritative; this original is kept
> for the review trail. The atomic plan (`state/archive/_tmp/p33_actions.json`) encodes
> the REVISED spec.

Mapping the request to the confirmed gaps (all verified in code this session):

| Requested property | Current gap (file:line) | Fix |
|---|---|---|
| **중복 없음** (no dup per researcher) | dedup is `canonical_id`-only in the weekly path; `title_norm` collapse exists **only** at `build_researcher_queue.py:1008-1030` and *skips blank title_norm*; preprint (`arxiv:<id>` synthetic DOI) vs published never collide (`_common.py:95-109`) | **R-DEDUP**: one shared `title_norm`+DOI dedup helper in `_common.py`, applied at every queue-write and at digest-select; blank-`title_norm` fallback; a `title_norm` index |
| **계속 업로드 트래킹** (track new uploads) | **no watermark** — `--since-*` is per-invocation, never persisted; nothing schedules `crawl.mjs`; new papers reach a board only after a manual `build_researcher_queue --apply` (`build_digest` slices a frozen queue) | **R-WATERMARK**: `archive_discovery_watermark` table (per source/query: max `pub_date`, `last_run_at`) + a driver that fetches only-newer and advances the cursor; a queue-refresh hook so ingested papers actually flow to boards |
| **기존 정보 기반 ingest** (read/not-interested) | `recommend.py` reads **neither** `archive_responses` nor `dim_preferences` (behaviour-blind; only downstream `build_digest` excludes) | **R-BEHAVIOUR**: `recommend.py` excludes answered papers at candidate-gen + behavioural veto-confirm/boost (P29 §3 optional, highest-leverage quality lever) |
| **새로운 정보 검색** (search new) | `crawl.mjs` works but is unwired/uncursored (above) | folded into R-WATERMARK (driver + profile→query) |

Plus the two correctness bugs that destabilize recommendations:
- **R-BUILDER**: two builders prune `archive_researcher_queues` by `build_token` with no discriminator → last `--apply` wipes the other. Add `builder` column; prune-by-builder; reader picks the owning builder.
- **R-TIER**: `recommend.py:744` hardcodes `tier='B'` → collapses `build_digest`'s S/A/B solver to `tier_relaxed`. Derive a real tier from connection strength (specB-strong→S, specB→A, gated→B).
- **R-DEADCODE**: remove/annotate `recommend.py` dead symbols (`MAX_QUEUE`, unreferenced `GATE_ENGINE`/`GATE_VERSION`, inert `models`/`metric`/`condition`/`direction`/`hypothesis`/`background`/`seed_paper`/`Aim.label`/`cos_only`).

### Deliverables (code + migration + offline verification; apply is operator `!`)
1. `_common.py` — shared `title_norm`/DOI dedup helper + unit tests (offline).
2. `state/migrations/2026-07-20_p33_pipeline.sql` — idempotent: `archive_discovery_watermark`, `builder` column on `archive_researcher_queues`, `title_norm` index on `archive_papers`. Reversible (`DROP`/`ALTER … DROP COLUMN` of new objects only).
3. `recommend.py` — behaviour-aware exclusion + veto-confirm, real tier, builder-scoped prune, dead-code removed.
4. `build_digest.py` — `title_norm`-aware exclusion (hard no-dup guarantee across preprint/published/cross-source).
5. `scripts/archive/discovery_watermark.py` (new) — watermark read/advance + profile→query driver; dry-run without node.
6. Offline verification harness + an **adversarial review workflow** (Opus skeptics — codex is account-blocked) over R-DEDUP/R-WATERMARK/R-BEHAVIOUR/boundary-safety/idempotency; iterate to clean.

### R-SOURCES — free discovery sources + tooling (the "search new info" pillar)

Investigated 2026-07-20 (web-verified). Runtime is **Python-only (no node)** and **keyless-by-default** — it runs with no credentials at all; two *free* keys (OpenAlex, Semantic Scholar) each unlock one extra source / rate tier. No paid keys anywhere. See "Credentials" below for exactly which variables the code reads.

**Two-tier discovery:**
- **Unattended weekly cron (pure Python, node-free, runs keyless)** — new `scripts/archive/fetch_new_papers.py`:
  - **Behaviour-based core = Semantic Scholar Recommendations API** (`POST /recommendations/v1/papers/`, keyless). Feed **positive = each researcher's `save_later`+`already_read` canonical→S2 IDs, negative = `not_relevant` IDs** → returns new (~≤60d) relevant papers. This *is* the "기존 정보(읽음/관심없음) 기반 새 논문 검색" requirement, natively.
  - **Breadth = direct free APIs** (arXiv, Europe PMC, PubMed, bioRxiv/medRxiv, Crossref, DOAJ — plus OpenAlex **when `OPENALEX_API_KEY` is set**) — reuse the vetted per-source clients in `paper_search_mcp.academic_platforms.*` (imported, not reimplemented), filtered incrementally by the **R-WATERMARK** cursor.
  - Every hit → title_norm/DOI dedup (**R-DEDUP**) → `archive_relevance_decisions`/queue-input.
- **Attended operator deep-scout** — `paper-search-mcp` (installed `pip install --user paper-search-mcp`; wired in `.mcp.json`; 27 sources incl. PubMed/bioRxiv/medRxiv/Crossref/DOAJ that `crawl.mjs` lacks) + `crawl.mjs` full-text (needs node).

**Verdicts on the named tools:** **alphaXiv** = OAuth-gated MCP only, no RSS/REST → *not* viable for keyless cron (substitute: arXiv native category RSS + S2 ranking). **SciSpace** = enterprise-only, no free API → dropped.

#### Credentials — what the code actually reads (G7, corrected 2026-07-21)

An earlier revision of this line claimed "the Python fetcher + `crawl.mjs` read `OPENALEX_API_KEY`". That was **false on both counts** — a repo-wide grep found the string only in this document (`state/archive/_explore/batch01/D-gaps.md` §2.3). Half of it is now true; the state below is verified, not aspirational.

| Variable | Read by | Absent → |
|---|---|---|
| `OPENALEX_API_KEY` | ✅ `fetch_new_papers.py` (`OPENALEX_KEY_ENV` → `resolve_api_keys()` → `import_searchers()` gate → `apply_api_key()` sets the session's `api_key` query param) | source `openalex` **skipped**, one explanatory log line per run; the other 8 sources run unchanged |
| `OPENALEX_API_KEY` | ❌ **`pipeline/crawl.mjs` still does NOT read it** (`srcOpenAlex` sends `&mailto=` only, `crawl.mjs:74-77`) — and `mailto` is no longer a polite-pool lever. Its OpenAlex path will 409 once the free credits run out. Node is not installed, so `crawl.mjs` is dormant; wiring it is a separate unit. | 409 after ~100 requests (see below) |
| `SEMANTIC_SCHOLAR_API_KEY` (alias `S2_API_KEY`) | ✅ `fetch_new_papers.py` — `s2_recommend(api_key=None)` resolves it from the env and sends `x-api-key`; the breadth `semantic` source is keyed too, because `paper_search_mcp`'s `SemanticSearcher.get_api_key()` reads the same variable from `os.environ` and `load_dotenv_once()` puts the repo `.env` there | unauthenticated, lower rate limit, warned once — **not** fatal |
| `CSNL_POLITE_MAILTO` / `CROSSREF_MAILTO` / `OPENALEX_MAILTO` | ✅ `fetch_new_papers.py` `--mailto` default (`resolve_mailto()`) | falls back to the placeholder `lab@example.org`, which the dry-run flags in-line |

**⚠ Operator action:** **OpenAlex has required a free API key since 2026-02-13** (the `mailto` polite pool was retired; an unkeyed caller gets ~100 free credits and *then* a **terminal** HTTP 409 — it does not fail on request 1, so an unkeyed cron fails silently-late). Get the free key at <https://openalex.org/settings/api> and add `OPENALEX_API_KEY=…` to `.env`. Until then the fetcher runs on its keyless sources (S2/arXiv/EuropePMC/PubMed/bioRxiv/medRxiv/Crossref/DOAJ) and simply omits `openalex` — degraded, never broken.

**Key handling rules (enforced in code):**
- Keys are read from the **environment only** — `os.environ`, with the repo `.env` loaded as a no-overwrite fallback. There is deliberately **no `--openalex-key` / `--s2-api-key` CLI flag**: argv is world-readable via `ps` and persists in shell history.
- Key **values are never printed**; status is reported as `present` / `ABSENT` by `ApiKeys.status_lines()`, in both dry-run and `--apply`.
- A source that is skipped for want of a key is recorded in `no_advance[...] = 'no_key'`, exactly like a failed source: **it must not advance its watermark or stamp `last_success_at`** (it never ran).
- `--require-keys` turns the graceful degradation off — exit `2` unless both keys are set, for an operator who wants a hard failure instead of a quiet partial fetch.
- The vendored clients swallow a non-200 and return `[]`; a keyed source that fetches 0 rows therefore also prints a "re-check the key — an exhausted/invalid key returns 409, reported as empty" hint.

**Still open (not this unit):** `--s2` only prints the recommendation *plan* — `s2_recommend()` is not yet called from `_apply()`, so the behaviour-based S2 core above is built and egress-guarded but not wired into the fetch loop. `pipeline/crawl.mjs` needs the same one-line `api_key` wiring (and Node installed) before its OpenAlex path is usable.

### Go-live (operator `!`, gated — hand-off)
```
! python3 scripts/run_migration.py state/migrations/2026-07-20_p33_pipeline.sql
! node --version   # install Node 24 first (crawl.mjs), OR skip live crawl and use the 308 ingested live papers
! python3 scripts/archive/discovery_watermark.py --apply         # advance cursor + ingest only-newer
! python3 scripts/archive/build_researcher_queue.py --all --apply # OR recommend.py --apply (builder-scoped now)
! python3 scripts/weekly/build_digest.py --apply                  # title_norm-aware, dup-free
# .P23_ENABLED stays absent → nothing sends until the operator opts in
```

---

## Acceptance
- [ ] Part 1 safe set executed + committed; nothing gated touched.
- [ ] Migration idempotent; only additive/reversible objects.
- [ ] Offline unit tests: `title_norm` dedup collapses preprint↔published + blank-title_norm handled; watermark advances monotonically.
- [ ] `recommend.py` excludes `archive_responses` at candidate-gen; emits real tiers; prunes only its own builder rows.
- [ ] `build_digest` cannot stage two records of the same work to one researcher (title_norm-level), across weeks and sources.
- [ ] Adversarial review passes (boundary-safety: no prod write from the agent; archive_responses untouched).
- [ ] CLAUDE.md P33 row; go-live handed off as operator `!` commands.

---

## PART 2 — REVISED (1차 리뷰 반영, MF-1..MF-13) — AUTHORITATIVE

The 1st adversarial review (`SHIP-WITH-FIXES / converged=false`) reshaped Part 2 into
**two tracks**: a small **offline-verifiable hardening core** (LIVE path) and an
**operator-gated fetch-only discovery** track that *reuses* the existing P26 ingest
(no parallel stack). Decompose **by file-owner**, not by property; migration + `_common.py`
land first; consumers next; adversarial-review loop **once at integration**.

### Track A — hardening core (additive, offline-verifiable, LIVE path)

- **A1 · migration** `state/migrations/2026-07-20_p33_pipeline.sql` — **idempotent, every
  statement `csnl_paper_rec.`-qualified, wrapped in one BEGIN/COMMIT, no `SET search_path`,
  no DROP, no bare identifiers** (MF-11). Objects: `archive_discovery_watermark`;
  `ALTER TABLE archive_researcher_queues ADD COLUMN IF NOT EXISTS builder text` **+ an
  idempotent backfill** `UPDATE … SET builder='brq' WHERE builder IS NULL` (a static
  DEFAULT is insufficient, MF-1); `CREATE INDEX (NON-UNIQUE) IF NOT EXISTS … ON
  archive_papers(title_norm)` — **UNIQUE forbidden** (title_norm is intentionally shared, MF-6).

- **A2 · `_common.py` same-work dedup helper** (R-DEDUP, MF-6) — collapse **only when
  provably the same work**: identical normalized DOI, OR one arXiv-synthetic + one real
  DOI passing a `rapidfuzz` token-set-ratio title threshold. **Never** merge on
  `title_norm`+year alone (drops legit distinct papers). **Blank title_norm → keep both,
  no dedup.** Leave `build_researcher_queue.py:1008-1030`'s working collapse UNTOUCHED;
  add the helper only at the missing call sites. Pure function → fully unit-testable offline.

- **A3 · `build_digest.py`** (R-DEDUP + R-BEHAVIOUR-exclusion, MF-2 — the real property-4 fix):
  make the `archive_responses` exclusion **title_norm-aware**: exclude any candidate whose
  `title_norm` matches a `title_norm` the researcher has *answered* (JOIN
  `archive_responses → archive_papers` on `title_norm`), not just `canonical_id`; same
  title_norm bridge on the `archive_weekly_digests` (past-digest) clause. Add
  `AND q.builder='brq'` to `_candidates` so it reads only the authoritative builder (MF-1).
  Keep the fix as SQL the operator runs; the agent only verifies the query-shape offline.

- **A4 · `recommend.py`** (R-BUILDER + R-TIER + R-DEADCODE) — **PARKED builder** (P28 never
  applied → not live): (i) `--apply` writes `builder='p28'` and prunes
  `WHERE builder='p28' AND (build_token IS NULL OR build_token<>%s)`; both UPSERTs add
  `builder=EXCLUDED.builder` (MF-1). (ii) **R-TIER**: derive a real tier from connection
  strength (specB-strong→S / specB→A / gated→B) on the **same percentile scale**
  `build_digest`'s solver expects — but this only matters once P28 is promoted (P30), so
  ship it but keep `recommend --apply` operator-gated/parked. (iii) **R-DEADCODE**:
  symbol-delete `GATE_ENGINE`, `GATE_VERSION`, `cos_only`, `Aim.label`, and inert aim fields
  (`metric/condition/direction/hypothesis/background/seed_paper`). For **MAX_QUEUE**: it
  backs a LIVE guard (`recommend.py:683-685`) — delete the def **and** the guard **together**
  and **retain an explicit hard cap** (the enlarged pool still needs one) (MF-12).

- **A5 · `recommend.py` behaviour veto/boost** (R-BEHAVIOUR, MF-7) — **EXTEND** the existing
  `_veto`/`_def_penalty`, do not build a parallel engine: feed `archive_responses`
  `not_relevant` as an additional `negatives` source and `save_later`/`already_read` as a
  boost INPUT to the existing rerank weights. **Never veto on a shared `connecting_signal`
  alone** (the P24 landmine: save/reject signals overlap) — require reason-text / negative-topic
  anchoring. **DEFER/GATE the boost behind an offline precision eval vs `validate_drift`**
  before it lands (it changes rankings). Lands with A4 but flag as gated.

### Track B — discovery: FETCH-ONLY, reuses existing ingest (operator-gated, MF-10b/MF-4)

- **B1 · watermark** — `archive_discovery_watermark {source, query_hash, last_event_date,
  last_index_date, last_success_at, updated_at}`. Cursor keys on **ingest/index date** where
  the source exposes it (Crossref `from-index-date`, arXiv `submittedDate`, OpenAlex
  `from_created_date`) with a **lookback-overlap window**; store both event- and ingest-time (MF-3).

- **B2 · `scripts/archive/fetch_new_papers.py`** — **FETCH-ONLY**, pure-Python, keyless.
  Emits the **`state/archive/discovery_run/found/<INIT>.jsonl`** format that
  `ingest_live_papers.py` **already consumes** — reuse `ingest_live_papers` +
  `wire_live_to_queue_inputs` + `record_relevance` **UNCHANGED**; add the watermark cursor to
  that existing path (NO new parallel stack, MF-10b). Design:
  - **Adapter layer** (MF-8) — never call `paper_search_mcp` clients raw: bounded socket
    timeout, per-source try/except isolation (one failure logged+skipped, never fatal), a
    single throttle/politeness funnel (real `mailto`/`User-Agent` for Crossref/EuropePMC/NCBI/
    OpenAlex; browser UA for DOAJ's Cloudflare); capture `pub_date` at fetch time so an
    OpenAlex 409 never yields a dateless row; **bioRxiv/medRxiv = firehose-then-local-filter**
    (fetch a capped date window, match profile locally — never pass the query as a category).
  - **Tri-state fetch** (MF-3): every adapter returns `ok+papers` / `ok+empty` /
    `FAILED(raises)` — **never `[]` on error**. Advance the cursor via
    `GREATEST(stored, batch-max)` **only on a non-exception run**; commit-then-advance;
    inclusive `>=` boundary + dedup so same-day late arrivals aren't dropped.
  - **Sources** (agent-A verified, keyless, deduped on normalized DOI): existing OpenAlex
    (needs key), Europe PMC, arXiv, Semantic Scholar (+key) **+ new Crossref
    (`from-index-date`), bioRxiv/medRxiv (date-range path), PubMed (`edat`), DOAJ (optional)**.
    Reuse `paper_search_mcp.academic_platforms.*` clients through the adapter.
  - **S2 Recommendations** (MF-5): map `archive_papers.doi → 'DOI:<doi>'`, synthetic
    `'arxiv:<id>' → 'ARXIV:<id>'`; resolve via `/paper/batch` with a **cached canonical→S2-ID
    map** + 429/`Retry-After` backoff; no-DOI papers skip or title-resolve; **cap/prioritize**
    the pos(save/read)/neg(not_relevant) lists. **Recency is NOT native to the POST endpoint**
    → post-filter by `pub_date` (folds into B1) and/or drive recency via
    `forpaper?from=recent` seeded by top saves. **Egress rule**: the HTTP body carries **only
    public paper IDs** — never `researcher_id`/name/email/any `.env` value; best-effort with
    automatic fallback to the direct-API breadth path on 429/mapping failure.

- **B3 · deps + wiring** — pin `paper-search-mcp` + transitive (`bs4/feedparser/httpx/lxml/
  rapidfuzz`) in a `requirements-discovery.txt` with an **import guard + smoke test** asserting
  each expected `*Searcher` class + `.search` signature exists (MF-8). `.mcp.json` (done) is
  for the **attended** scout only.

- **B4 · cron interlock** (MF-4) — Track B is **operator-`!` ONLY**. **FORBID** adding
  `fetch_new_papers`/`discovery_watermark`/`build_researcher_queue`/`recommend` to
  `run_weekly_cron.sh`. Property-2 "continuous tracking" is **operator-cadenced**, not
  cron-automated. Acceptance gate: `grep` of those 4 names in `run_weekly_cron.sh` == 0.

### Verification (MF-9) — the agent's scope this session
**Pure-function offline unit tests ONLY**, never a prod-touching dry-run: (a) same-work dedup
helper (drift collapses / same-title-different-DOI kept / blank-title keeps both); (b) watermark
tri-state + `GREATEST` advance (late-index still fetched / simulated 429 leaves cursor unmoved);
(c) S2 request-body builder over fixture rows (DOI:/ARXIV: mapping, no-DOI skip, **body carries
no researcher identity/env value**); (d) migration lint (all `csnl_paper_rec.`-qualified, single
BEGIN/COMMIT, no DROP/search_path). The harness must **never** shell out to a `_db`-touching
script. Everything that opens prod (`build_digest`, `recommend`, `discovery_watermark`,
`ingest_live_papers`) is **operator-run**.

### REVISED Acceptance (adds the hard half of every property — MF-13)
- [ ] Migration idempotent + lint-clean (qualified / single-txn / additive / non-unique index).
- [ ] Fixture: read/`not_relevant` **preprint ⇒ its published twin suppressed** (MF-2).
- [ ] Fixture: **drifted-title pair collapses; same-title-different-DOI kept**; index non-unique (MF-6).
- [ ] Fixture: **late-index paper still fetched** AND **429/timeout leaves the cursor unmoved** (MF-3).
- [ ] Fixture: canonical→`DOI:`/`ARXIV:` mapping, no-DOI skip, **request body carries no researcher identity/env** (MF-5).
- [ ] `grep run_weekly_cron.sh` for the 4 discovery scripts **== 0** (MF-4).
- [ ] `builder` backfilled; each builder prunes only its own rows; `build_digest` reads `builder='brq'` (MF-1).
- [ ] `recommend --apply` remains parked/gated; behaviour boost deferred behind the eval (MF-5/MF-7).
- [ ] Adversarial-review loop at integration → converged (no CRITICAL/HIGH).
- [ ] CLAUDE.md P33 row; go-live handed off as operator `!` commands (incl. OpenAlex/S2 free-key adds).
