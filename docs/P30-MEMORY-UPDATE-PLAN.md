# P30 — Memory update from the completed surveys (grounded plan)

> 2026-06-10. The 6 researchers finished their Notion profile surveys (MSY = the
> 7th, still blank/not-interviewed). The freeze is lifted, so I **read the live
> pages read-only (dry-run, no `--apply`, no Notion write)** and diffed the
> completed answers against the pre-fill baseline + against the existing memory
> (L1 projects, 586 interview responses). This plan is grounded in that diff —
> it says exactly how each completed survey updates each memory layer, what
> conflicts, and the gated sequence to apply it. Nothing was written. It ends at
> the operator-apply gate. Executes P29 §5 with real content; pairs with the P29
> hardening pre-reqs (§4 here).

---

## 1. What actually landed (live dry-run parse, 2026-06-10)

All 6 completed surveys parse cleanly through the live Notion path (0 parse
failures, 0 `needs_review` except the two expected: MSY-blank, SMJ-unconfirmed
negative-PI). The **changes vs pre-fill** (= the memory delta):

| Init | Role (was blank) | Aims | Methods | PIs | Other substantive edits |
|---|---|---|---|---|---|
| **BHL** | 석사과정 | 2 (same) | same | 0 | minimal — pre-fill was accurate |
| **BYL** | 석사과정 | 4 (same) | +ANN (→beh/eye/neural/ann) | 0→3 | mod 3→4 |
| **JOP** | 박사과정생 (+name 박준오) | 3 (same; **P1/P2 anchor rewritten**) | beh → **+neural+ann** | 3→**9** | **summary rewritten**; kw 34→**26** (pruned); mechanisms simplified |
| **JYK** | 석박통합과정 | **2→1** (corrected over-split) | ann → **+beh+neural** (4→10 approaches) | 1 | **summary+infra rewritten**; P1 anchor change |
| **MSY** | — | **0 (blank)** | — | — | not interviewed → cold-start |
| **SMJ** | 연구원 | 3 (same) | +1 | 3→4 | negative-PI still **【확인필요】 (unconfirmed)** |
| **SYJ** | 연구인턴 | **1→2** (added aim) | beh → **+neural+ann** | 5→7 | wants=가끔 |

**Cross-cutting signals:** (a) everyone filled their role; (b) **four researchers
sharply expanded methods** beyond the pre-fill's behavior-only guess
(JOP/JYK/BYL/SYJ added neural/ANN) — the pre-fill materially under-represented
method breadth; (c) PIs expanded (JOP 3→9, BYL 0→3, SYJ 5→7); (d) JOP/JYK
rewrote the authoritative research framing (summary + mechanism).

---

## 2. The existing memory, and how the survey updates each layer (grounded)

| Layer | Store | Current state (measured) | Survey update |
|---|---|---|---|
| **L1 projects** (read-only) | `csnl_research.projects` active | BHL1·BYL1·JOP4·JYK1·MSY2·SMJ1·SYJ1 | NOT overwritten. **Reconcile** with L2. |
| **L2 survey** (NEW authoritative) | `archive_survey_*` | **0 rows — migration not yet applied** | **Ingest the 6 completed surveys** → the new researcher-confirmed truth |
| **L3 dim_preferences** | `archive_profile_verifications` | live belief-update loop (plugin) | **KEEP** (closed-loop learning); update the *method* signal from the new survey methods |
| **L4 fingerprints** | `fingerprints/*.json` | per-researcher BM25 phrases | **Rebuild subordinate to survey** (survey keywords/aims as anchors; polysemy-safe negative drop) |
| **behaviour** | `archive_responses` | **586 rows** (see §3) | Behavioural truth — used to **confirm/contradict survey vetoes** + as eval ground-truth |
| **gate cache** | `archive_relevance_decisions` | P26 verdicts, built on **pre-survey** profiles | **Stale** — re-gate against the new survey memory |

**Key grounded finding — survey ENRICHES, it does not contradict.** Aim counts
(L2) vs active-project counts (L1): the survey is at *aim* grain and is a
superset of L1's *formal-project* grain for every interviewed researcher (JOP 3
aims over 4 projects = the grannmds+granrdt two-lens merge; everyone else L2 ≥
L1). The one apparent "drop" — **JYK 2→1 aims — actually ALIGNS the survey with
L1** (`dynamic_bias`, 1 project); the pre-fill had over-split it. So the P29
precedence worry ("survey vs projects conflict") is mostly moot: the only real
branches are **MSY (blank L2, 2 real L1 projects → projects-only/cold-start)**
and per-field confidence (all interviewed aims parsed `confidence=high`).

---

## 3. Behavioural baseline = the rebuild's success metric (measured)

`archive_responses` precision = save/(save+not_relevant), per researcher:

| | BHL | BYL | JOP | JYK | SMJ | SYJ | MSY |
|---|---|---|---|---|---|---|---|
| precision | **42%** ⚠ | 80% | 64% | **29%** ⚠ | **43%** ⚠ | 73% | — (0) |
| responses | 123 | 140 | 43 | 126 | 89 | 65 | 0 |

**JYK (29%, 84 not-relevant with reason text), BHL (42%), SMJ (43%)** are the
low-precision researchers the rebuild must improve. JYK is the strongest case:
its 84 `not_relevant` reasons are exactly the behavioural signal to confirm its
16 survey negatives, and its now-corrected single clean aim should sharply
re-target. **These percentages are the objective before/after gate** in §4 step 6.

---

## 4. The update sequence (gated; operator-run `!` for every DB write)

**Pre-reqs (P29 hardening — do BEFORE the rebuild so it doesn't break delivery):**
P0a disarm the live v3 Slack cron (`docs/HARNESS-MEMORY-REBUILD-PLAN.md` §1);
P0b commit the built+reviewed P28; P0c fix `recommend.py`'s `tier='B'` hardcode +
the two-builder race (prune-by-`builder` + reader-filter) so rebuilt queues feed
`build_digest` correctly.

**The memory rebuild:**
1. **Apply the P28 migration** — `! python3 scripts/run_migration.py state/migrations/2026-06-08_p28_survey_memory.sql` (creates `archive_survey_*`; currently absent).
2. **Ingest the completed surveys** — `! python3 scripts/archive/ingest_survey.py --source notion --apply` → L2 for the 6 (MSY blank → no rows). Re-confirm 0 unexpected `needs_review`.
3. **Reconcile (operator review, dry-run report)** — the grounded conflict list (§5). Decide MSY=projects-only, SMJ-negPI confirm/drop, and confirm the method-expansion + summary/mechanism rewrites are intended (they are researcher-confirmed, `confidence=high`).
4. **Rebuild fingerprints subordinate to survey** — anchor L4 on the new survey keywords + aim phrases; **polysemy-safe** drop only (never auto-drop a phrase a positive aim *or* a `save_later` response also claims — the JYK `attractor` trap). Operator-gated.
5. **Build queues to a SHADOW** — `recommend.py` reading L2 survey **+ `archive_responses` (behavioural veto-confirm/boost) + L3 dim_preferences** → `builder='p28'` shadow rows, NOT the live delivery queue. MSY → legacy/projects path.
6. **Eval gate** — extend `validate_drift.py`: P28-shadow vs legacy precision/recall on held-out `archive_responses`, per researcher (the §3 baselines are the bar; JYK 29%→? is the headline). Guardrail not sole gate (circularity — see P29 §5.3).
7. **Promote + connection-gate** — flip the builder-owner to P28 only for researchers where P28 ≥ legacy; run the Opus same-job gate (`--gate-mode emit` → fan-out → `cached`) with the **cache-miss alarm** (P29 §5.5) so freshly-ingested live papers aren't silently dropped.
8. **Validate + record** — CLAUDE.md P30 row; re-gate `archive_relevance_decisions` against the new profiles (the P26 cache is stale).

---

## 5. Per-researcher specifics + conflicts needing an operator decision

- **MSY — cold-start.** Blank L2, 2 real L1 projects (`cat_mag_main`, `face_cond_ver10`). → projects-only via the legacy builder; do NOT fabricate aims. Onboard via the interview plugin later. *(Decision: confirm projects-only.)*
- **JYK — highest-value rebuild.** 29% precision, 84 reason-bearing `not_relevant`. Now 1 clean aim (`dynamic_bias`, aligned with L1) + 16 negatives + expanded methods. → strongest candidate for behavioural veto-confirmation; the eval headline.
- **SMJ — unconfirmed negative-PI.** Survey negative-PI is a 【확인필요】 "환영/제외 여부 미확정" note → the ingester correctly routed it to `needs_review`, NOT a veto. *(Decision: operator/researcher confirms before it can deprioritize anyone.)*
- **SYJ (+aim), JOP/JYK (rewritten summary/mechanism)** → their `archive_relevance_decisions` (P26, pre-survey) are stale; re-gate.
- **Method expansion (JOP/JYK/BYL/SYJ +neural/ann)** → propagate to the method-rerank signal and to L3; do NOT keep the behavior-only assumption that under-served them.
- **All interviewed aims `confidence=high`** → the P29 confidence-gated precedence is satisfied (no low-confidence survey field overriding a high-confidence L1 project this round).

**Open decisions for the operator:** (Q1) MSY projects-only — confirm? (Q2) SMJ
negative-PI — confirm as veto or drop? (Q3) build the behavioural-confirmation
(`recommend.py` reads `archive_responses`) for this rebuild — yes, given JYK/BHL/SMJ
low precision it's the highest-leverage quality lever? (Q4) re-gate the full P26
relevance cache, or only the rewritten researchers (JOP/JYK/SYJ)?

---

## 6. Boundaries · rollback
Live Notion read **read-only** (dry-run parse only — 0 writes); all DB writes are
operator `!`/grant and idempotent/additive; `archive_responses` + the filled
surveys untouched; MSY/cold-start handled explicitly; rebuild lands in a **shadow**
and is promoted only behind the eval gate; rollback = the legacy builder still
owns the live queue until the flip. The two `ntn_` tokens remain exposed in past
chat — rotation still recommended.
```
