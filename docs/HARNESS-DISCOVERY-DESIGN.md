# Discovery-Engine Redesign — Design Doc (P26 scaffold)

**Status**: design only · authored 2026-06-04 · ready for new-session implementation.
**Origin**: operator directive (2026-06-04):
> "Macaque, monkey, rodent, rat, clinical, AI 연구더라도 research aim 혹은 발견된 현상이 내 연구와 관련이 있다면 추천받아도 된다. 인간 피험자·행동 실험·fMRI 키워드에만 국한하면 유용한 연구를 놓친다. 단순 인용/semantic similarity 가 아니라, deep-research 기반 scientific reasoning 으로 꼬리에 꼬리를 물고 질문하여 논문을 탐사·기각하는 workflow. 최근 5년·최신 연구는 기존 DB archive 에 의존하지 말고 검색 기반을 더 깊이 다진다."

---

## 1. 문제 (why redesign)

The current archive engine ranks by **semantic similarity + dim/fingerprint over a FROZEN 2,063-paper corpus** (`build_researcher_queue.py`: composite = 0.55·cosine + 0.30·dim_score + fingerprint-BM25). Three structural limits:

1. **Surface-metadata gating.** Out-of-scope is decided by domain keywords (`prefilter_oos.py`) + dim weights on species/method/subject codes. This **excludes aim-relevant animal / clinical / AI work** (e.g. a rodent attractor-dynamics paper, an AI efficient-coding model) that a researcher *would* want. P24's per-researcher species/clinical OOS proposals are the same mistake — **explicitly superseded by this doc.**
2. **Similarity ≠ relevance.** Cosine + citation proximity surface textually-near papers, not aim/phenomenon-related ones. Confirmed empirically: for BHL/JYK the rejected and saved papers share the same connecting-signals — similarity can't separate them; only *reasoning over the aim/phenomenon* can (P24 finding).
3. **Frozen corpus.** Recent ≤5y / emerging work isn't in the archive, so it's never surfaced.

Goal: an **aim / phenomenon / mechanism-based, reasoning-driven, live-search** discovery engine.

---

## 2. 관련성 계약 (the relevance contract) — the core

A paper P is **relevant** to researcher R iff **at least one** of:

- **(A) Aim connection** — P's research question/goal connects to one of R's research aims.
- **(B) Phenomenon connection** — P's *discovered phenomenon/effect* is shared with, generalizes, contradicts, or bears on a phenomenon R studies — **even in a different species, modality, or population** (e.g. serial dependence in macaque ↔ in humans; attractor dynamics in rodent PFC ↔ in human WM).
- **(C) Shared mechanism / computational theory** — P and R share a computational principle, model, or theory (efficient coding, Bayesian inference, attractor dynamics, rate-distortion, drift-diffusion, …).

**Not sufficient alone (rejected):**
- **Method/tool/metadata overlap** — same method (fMRI, RSA, EEG), same species, same subject-type, same stimulus. A *method-tag-only* match is rejected (matches researchers' own reasons, e.g. JOP "RSA 방법 태그로만 매칭된 off-domain", BHL "encoding-level … decision/WM-driven bias 아님"). Operator confirmed: method/tool transferability is **not** a primary inclusion criterion.

**Never a disqualifier:** species (macaque/monkey/rodent/rat), clinical population, AI/ML system, subject-type, or method. These are descriptive, not gating.

**Every decision carries a one-line scientific reason** naming which of A/B/C it satisfies (or, for a reject, why none — and explicitly not "wrong species/method"). These reasons are the auditable truth + the calibration signal (cf. `archive_responses.choice_detail.reason`, currently 100%-covered for not_relevant).

---

## 3. 아키텍처 — three stages per researcher

### 3-1. Research profile (reasoning-extracted, not keywords)
Build a structured profile from:
- `csnl_research.projects` (aim/hypothesis/manipulation/background fields — already read by `build_researcher_queue._interest_text_from_row`),
- the researcher's **save/read/reject history with reasons** (`archive_responses` + `choice_detail.reason`) — what they *actually* engage with and why.

Output (Opus-extracted): `{aims:[…], phenomena:[…], mechanisms_theories:[…], open_questions:[…], known_negatives:[…]}` — substantive scientific descriptors, not tag codes.

### 3-2. Candidate generation (broad · multi-source)
- **(a) Archive recall** — embedding/fingerprint over the 2,063 corpus for breadth (free, reuse `build_researcher_queue` machinery as a *candidate generator*, not a ranker).
- **(b) Live scholarly search** *(the new pillar)* — `pipeline/crawl.mjs` over keyless APIs (OpenAlex, Semantic Scholar, Crossref, bioRxiv/medRxiv). Queries generated from the profile's **aims / phenomena / mechanisms** (not human/fMRI keywords). Target **recent ≤5y + landmark**. **꼬리에 꼬리**: seed queries → follow each hit's references/related-work/phenomena into new queries. NOT limited to the archive.
- Pool + dedupe by DOI / `canonical_id` (`_common.canonical_id`).

### 3-3. Reasoning relevance gate + iterative explore (the core change)
A per-researcher Opus **scout** (fan-out, one per researcher — adapt the existing `unit-scout` agent):
1. For each candidate, fetch enough text (abstract; full text via `crawl.mjs` when borderline) and judge **A/B/C** relevance grounded in quoted text → accept/reject **with reason**.
2. **Iterate (꼬리에 꼬리, loop-until-dry):** from accepted papers, extract NEW phenomena/mechanisms/leads → new searches (3-2b) → re-judge → repeat until K rounds add nothing new (the `deep-research` / `unit-scout` pattern).
3. **Adversarial verify:** a skeptic pass kills method-tag-only / weak matches (default-to-reject on uncertainty), so the contract's "not sufficient alone" rule is enforced.
4. Output: ranked relevant set, each with `{relevance_type:A|B|C, reason, recency, source:archive|live, evidence_quote}`.

---

## 4. 통합 (with the existing P21–P25 system)

- The scout's reasoned set **feeds the queue** (`archive_researcher_queues`) / the weekly digest pool. The embedding/fingerprint composite is demoted to an **ordering prior within the relevant set**, not a gate.
- **Replace** the domain-OOS prefilter + species/clinical exclusion with the **reasoning gate** (inclusion by A/B/C). Keep OOS only for *genuinely* off-field corpora (materials/climate/etc.) — and even then by aim-irrelevance, not keyword.
- **Live-discovered papers not in `archive_papers`** are ingested (extend `merge_dedupe_filter.py` + a synopsis pass) so they flow through digest/interview like archive papers.
- **Cadence:** a **monthly per-researcher deep-scout** (operator-run Opus pass, like the P24 analysts / `unit-scout`) refreshes the reasoned pool; the **weekly Wed digest draws from that pool**. The unattended Wed cron stays **LLM-free** (DECISIONS-v3 preserved) — it only slices the pre-reasoned pool.

---

## 5. 재사용 인프라
- **`unit-scout`** agent — the explore/reject loop (query→crawl→read→score→reformulate). Adapt its scoring to the A/B/C contract.
- **`pipeline/crawl.mjs`** — vetted keyless scholarly full-text crawler (playwright + pdfjs). The live-search transport.
- **`deep-research`** skill — fan-out → fetch → adversarially-verify → synthesize pattern.
- **`archive_paper_synopses` / `_embeddings` / `fingerprints`** — candidate recall + priors.
- **`archive_evolution_proposals`** — record the relevance-policy change + per-researcher learned inclusions/exclusions.

---

## 6. 데이터/스키마 변경
- **NEW `archive_relevance_decisions`** `{researcher_id, canonical_id, relevance_type(A/B/C/none), reason, source(archive|live), confidence, decided_at, scout_version}` — auditable + **cached** (don't re-judge a paper already decided; only re-judge on profile change). UNIQUE(researcher_id, canonical_id).
- **Live-ingest**: live-search results → `archive_papers` (+ source `live_search`) + a synopsis row, via an extended ingest path; store `pub_date` (recency).
- `archive_responses` (reasons) stays the held-out calibration truth.

---

## 7. 경계 / 비용
- The deep-scout is an **operator-run Opus pass** (monthly), never the unattended cron (DECISIONS-v3 / rules/00). Keyless scholarly APIs only via `crawl.mjs` — no paid-LLM-in-cron.
- `csnl_research` read-only; writes to `csnl_paper_rec`.
- Token cost is the main constraint → (i) cache relevance decisions, (ii) archive-recall pre-filters before the LLM gate, (iii) loop-until-dry caps rounds, (iv) monthly (not weekly) cadence.

---

## 8. 검증 (non-circular)
1. **Calibration**: re-judge the existing `save_later` vs `not_relevant` set with the new A/B/C gate (reasons held out) → agreement with researchers' actual reasons? Target: rejects the method-only matches, keeps the aim/phenomenon ones.
2. **Pilot (BHL + JYK, below-floor)**: does aim-based + live-search surface more reason-aligned accepts and fewer false surfacings (the rodent-decision / affect papers that leaked into JYK's P24 rebuild)?
3. **Forward**: `validate_drift` precision over the next interview cycle.

---

## 9. 단계적 구현 (phases)
- **P26a** — relevance contract + profile extractor + `archive_relevance_decisions` table; re-judge existing responses (calibration; no live search yet).
- **P26b** — live-search candidate generation (`crawl.mjs`, ≤5y) + live-result ingest.
- **P26c** — reasoning gate + iterative explore loop (adapt `unit-scout`; adversarial verify).
- **P26d** — integration into queue/digest + monthly cadence + cost controls.
- **P26e** — pilot (BHL+JYK) → eval (§8) → rollout to 7.

---

## 10. 새 세션 입력 (implementation brief)
> "P26 implement. Read `docs/HARNESS-DISCOVERY-DESIGN.md` + the P24/P25 CLAUDE.md entries. Build the aim/phenomenon/mechanism-based deep-research discovery engine per §3–§9, starting with P26a (relevance contract + profile + calibration re-judge). Relevance = aim OR phenomenon OR shared-mechanism connection; species/method/subject are never disqualifiers; method-tag-only is rejected. Live search (crawl.mjs, ≤5y) replaces archive-only recall for recent work. Operator-run Opus deep-scout (monthly); the unattended Wed cron stays LLM-free. codex adversarial review before commit."

---

## 11. Acceptance criteria
- [ ] `archive_relevance_decisions` table + idempotent migration.
- [ ] Profile extractor produces `{aims/phenomena/mechanisms/open_questions/known_negatives}` per researcher.
- [ ] Re-judge of existing responses agrees with held-out reasons ≥ target (e.g. ≥80% on a sampled audit), and rejects method-tag-only matches.
- [ ] Live search (crawl.mjs) returns ≤5y candidates NOT in the archive for ≥1 pilot researcher.
- [ ] Reasoning gate emits per-paper A/B/C + reason; iterative loop terminates (loop-until-dry).
- [ ] Pilot (BHL+JYK) shows fewer reason-contradicting surfacings than the P24 rebuild.
- [ ] Unattended Wed cron remains LLM-free; deep-scout is operator-run.
- [ ] codex adversarial review passed; CLAUDE.md P26 entry.

---

## 12. Open decisions / risks
- **Live-search source priority** (OpenAlex vs Semantic Scholar vs Crossref) + rate limits via crawl.mjs.
- **Ingest scope** for live papers (full synopsis vs lightweight record) — cost trade-off.
- **Re-judge cost** at scale (cache + archive-recall pre-filter mitigate).
- **Augment vs replace** the embedding ranker — this doc proposes *augment* (embedding as candidate-gen + prior, reasoning as the gate); revisit after the pilot.
- **archive_responses 무손상** preserved throughout (read-only truth source).

---

**문서 종료.** 새 세션은 이 문서 + P24/P25 CLAUDE.md 항목만 읽고 §9 순서대로 구현 가능.
