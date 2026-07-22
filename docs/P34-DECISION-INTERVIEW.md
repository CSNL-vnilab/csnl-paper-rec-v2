# P34 — decision interview (accumulated; ask ALL AT ONCE)

> Operator directive 2026-07-21: *"필요한 결정은 인터뷰 스킬로 정리해뒀다가 내일 모아서
> 한꺼번에 질문해."* — so nothing here is asked mid-run. Each item carries the evidence, the
> options, and a recommendation, so answering should be fast.
>
> Format per item: **what's blocked · why it matters · options · my recommendation.**

> **Re-synced 2026-07-22 after the batch-06 adversarial review (NEEDS-REWORK).** Four changes
> you should know about before reading:
> 1. **A4 and E2 have been rewritten.** Both previously banked designs that the run's own
>    commissioned evidence document rejects. They are now genuine two-option decisions with the
>    counter-argument attached. You were being asked to approve the losing side of an argument
>    you were never shown — that is fixed here.
> 2. **B5 (share the Notion boards) has moved to the front.** It is not one blocked feature
>    among six; it is the single edge every downstream item depends on, it takes ~5 minutes, and
>    it is the lowest-risk item in this document.
> 3. **Three items were deleted, not answered** — the old C2/C3/C4. Reasons at the end of §C.
>    Your attention is the scarcest resource in this run; a question whose own recommendation is
>    "do nothing" should not consume one of your answers.
> 4. **Several premises have been corrected against live data.** In particular: the emails are
>    *already filled* (so B1 arms six recipients rather than unblocking a feature), SYJ has
>    **65** responses rather than 11, and three researchers — not two — are below the precision
>    floor. Details in `docs/P34-EVOLUTION-LOG.md` § Corrections.

---

## 0. Answer these three first — everything else is downstream

**0-A → B5.** Share the two Notion DBs. The delivery pipeline *worked*: 35 pages were created
on 2026-06-01 and every one still exists with a `notion_page_id`. Nobody answered because
nobody can see them. `archive_responses` has not grown since **2026-06-09**. Every learning and
evaluation item in this document consumes labels that can only arrive through that loop.

**0-B → A5.** The weekly board is deadlocked *and was reported as healthy*. It cannot
self-recover, and 35 papers are currently burned.

**0-C → B1 ordering.** Do **not** add SMTP credentials until the materials checker can fail
honestly (§C5). The emails are already in place; SMTP is the only remaining interlock.

---

## A. Identity & who gets recommendations

**A1. SYJ — two registries disagree about whether she is in the lab.**
Previously this was presented as a recommendation. It is a conflict, and you are the only one
who can resolve it.

| source | says |
|---|---|
| behaviour (`csnl_paper_rec`) | **active** — survey, fingerprint, 200-row queue, **65 interview responses**, 73.0 % precision at n=63 (the *second best-served* researcher in the lab) |
| `csnl_ops.researchers` (live) | **not active** — `full_name = 'SYJ (past member)'`, `active = false`, `role = NULL`, `email = drawing987@gmail.com` |
| `docs/CSNL-INFO.md §2` | **no row at all** → `config/researchers.yaml:48 email: ""` → `notify.py` skips her every week |
| `config/nas_catalog.json` | `Sooyoung Jo`, `status: active` |
| NAS | no `MM/SYJ`, no `_by_presenter/SYJ`, no `MetaData/members/SYJ.json` |

*Correction to the earlier version of this item:* it said "11 interview responses… statistically
meaningless". That is wrong by ~6×; the caveat is withdrawn. It also said *"I did not invent an
address"* — correct, but the address was one `SELECT` away in `csnl_ops.researchers`, and the
old fix instruction ("fill from `CSNL-INFO §2`") **provably could not have worked**, because §2
is exactly where she is missing.

*Options:* (a) she is active → correct the `csnl_ops` row (`full_name`, `role`, `active=true`)
and let everything else generate from it; (b) she has left → retire her from the recommendation
cohort and stop building her queue. **Rec: (a)**, on the behavioural evidence — but the DB
currently asserts the opposite, so I will not act on either without your word.

**A2. JYK's address is your own — and this is now blocking, not parallel.**
`csnl_ops.researchers` records `JYK → jy061100@gmail.com`, identical to the operator account and
to the git author. So it is the lab's own recorded data, not a copy error introduced by
paper-rec. But if it is the lab/ops account rather than 김정예's, then once B1 lands **every
weekly "김정예 연구원님" mail goes to your inbox, JYK receives nothing, and the logs say
`sent`.** *Options:* (a) confirm it is genuinely hers; (b) supply the correct address.
**Rec: confirm — and treat this as a prerequisite of B1, not a separate question.**

**A3. Four active members get zero recommendations.** `JWL`, `MJC`, `JHR` (and `SK`, who has NAS
material but no queue) are active in the registry yet are not recommendation targets. MJC is the
3rd-most-active Paper Blitz presenter; JHR presented 4 GRMs in 2026.
**Rec changed to: defer this question until the loop demonstrably works with 7.** Adding four
researchers is a 57 % expansion of the target set for a loop that has produced **0 responses in
7 weeks**; it multiplies whatever is broken. Ask again after B5 yields responses. (If you want
to answer now anyway, it is still per-person policy, not a bug.)

**A4. Canonical people registry — REWRITTEN as a two-option decision.**

*What was previously banked (and should not have been):* `csnl_core.people` +
`csnl_core.person_aliases` as the single source, with `parse-mm.ts` sets, both `researchers.yaml`
files and `CSNL-INFO §2` becoming generated reads.

*Why it is being re-opened:* the batch commissioned to design the canon,
`state/archive/_explore/batch04/O3-canon.md`, **explicitly rejects that design**, and the
previous version of this item never surfaced that. `csnl_core.people` comes from
`O1-researchers.md:235` — and O1 was surveying researcher *knowledge*, not designing schemas.

> **O3's counter-argument, verbatim** (`O3-canon.md:362-364`):
> *"**Do not build a `csnl_canon` schema.** A seventh schema with no owner is a new drift
> surface. `csnl_ops` already owns people, and `csnl_ops` already has the FKs."*
>
> `O3-canon.md:273` places CANON-P in `csnl_ops.researchers` (exists) +
> `csnl_ops.researcher_aliases` (new, ~15 rows) + `csnl_ops.projects`, reasoning *"the FKs
> already live here"* and *"zero new infrastructure"*.

*Live evidence, verified 2026-07-22:*

```
SELECT count(*) FROM pg_namespace WHERE nspname='csnl_core';        → 0   (does not exist)
SELECT count(*) FROM information_schema.tables
  WHERE table_schema='csnl_ops' AND table_name='researcher_aliases'; → 0   (does not exist)
csnl_ops.researchers → 24 rows · 12 active · 12 of 12 active rows have an email
                       (incl. SYJ's), plus role and defended_on
```

The question `csnl_core` cannot answer is **who writes it**: `CLAUDE.md` 경계 restricts
paper-rec's writes to `csnl_paper_rec`, and csnl-ops's `AGENTS.md` scopes its writes to `public`
+ `csnl_ops`. A schema with no owning repo has no migration directory.

| | **Option 1 — `csnl_ops` (DEFAULT / recommended)** | Option 2 — `csnl_core` (the original proposal) |
|---|---|---|
| home | `csnl_ops.researchers` (+ ~7 columns) + **new** `csnl_ops.researcher_aliases` | new `csnl_core.people` + `csnl_core.person_aliases` |
| owner | csnl-ops's existing migration pipeline | **undetermined** — neither repo's boundary permits it |
| new infrastructure | none | a seventh schema |
| FKs | already anchored here | would have to be re-anchored |
| emails | already present for all 12 active people | would be migrated |
| how paper-rec reads it | `csnl_paper_rec.v_people` — a read-only projection **paper-rec owns**, so its declared write boundary is untouched | cross-schema read into a schema nobody owns |

**Rec: Option 1**, with the smallest-slice sequencing from `O3 §3.6`, each step independently
valuable: (1) correct the two DB stubs — `SYJ` (A1) and `HJH (unknown)`; (2) add
`researcher_aliases` seeded `MJ/MINJIN→MJC, KY→SK, DG→DGY, LBY→BYL, PI→SL, SHL→SL`, with the
explicit **do-not-merge** list (`MS≠MSY`, `HL≠BHL` — merging would attribute an alumnus's
reading to a member); (3) `parse-mm.ts` takes the roster as a parameter; (4) **`canon_check`** —
a ~40-line drift script per repo; (5) only then generated reads. **Steps 1–2 alone close SYJ's
weekly mail gap and BYL's `LBY_*` GRM-corpus gap.**

Two things the old A4 omitted and you should price in:
- **The drift check is the part that actually prevents recurrence.** `O3 §3.4`: *"This is the
  piece that would have caught JWL, SYJ, LBY and the empty-email drift on the day they appeared.
  Without it, any option below degrades to 'do nothing, slower'."* A4 proposed the canon but not
  the check.
- **The plugin is a release boundary.** Two of the six eligibility-gate copies
  (`plugin/scripts/profile_show.py`, `plugin/scripts/preflight.py`) ship to researcher laptops
  via the marketplace (`plugin.json 0.6.0`), and there is **no version handshake between plugin
  code and DB schema** — P22 solved code/DB skew by force-bump *discipline*, not by a check. So
  repointing them is not a code edit, it is a coordinated release: migration → version bump →
  researchers re-install before their next interview. This is the largest single cost item in the
  canon proposal and it appeared nowhere in the old A4.

**A5. The weekly board is deadlocked, and 35 papers are burned. What do you want done? (NEW)**
`archive_weekly_digests` holds exactly one week — 2026-W23, 35 rows, all with a `notion_page_id`,
**all with `response_choice IS NULL`**, sent 2026-06-01. Those are the P23 smoke-test rows.
`build_digest._active_counts()` has **no week filter**, so seven-week-old rows still count as
"active" → `deficit = 5 − 5 = 0` → `"full, no refill"` for all seven researchers, forever. That
output is what the evolution log recorded as *"dry-run clean for all 7"*. The migration fix was
real; the verification was void.

Two consequences needing your decision:
- The refill can only unwedge when a response arrives, which needs **B5** *and* `.P23_ENABLED`.
- `build_digest.py:118-124` excludes any canonical_id already present in the digest ledger for
  that researcher — so those **35 papers are permanently burned** out of the candidate pool
  despite never having been seen by a human.

*Options:* (a) delete the 35 W23 smoke rows and release those canonical_ids back to the pool —
they were a test, not a delivery; (b) keep them as delivered history and accept the burn;
(c) keep the rows but scope `_active_counts` to the current week only, so they stop wedging the
refill without being erased. **Rec: (a) + the `_active_counts` week scope.** Note this is one of
the few items here that requires a **write to `archive_weekly_digests`**, so I have not touched
it. `archive_responses` is unaffected either way (those rows never produced responses).

---

## B. Credentials & access

**B5 is first because it is the critical path**, not because it is a credential.

| # | Blocked | Needed |
|---|---|---|
| **B5** ⭐ | **Everything.** The 35 delivered pages are unanswerable, so `archive_responses` is frozen, so `validate_drift` literally cannot move, so D2/D4/D5/X3/X4 are all building a mill next to a dammed river | share the digest + history DBs with the 6–7 researchers (**UI-only** — the integration cannot self-invite; `memory: notion-sharing-ui-only`). ~5 minutes |
| **B1** | All email — `notify.py` + `check_materials.py` are complete but cannot send | Gmail **App Password** → `SMTP_USER`/`SMTP_FROM`=vnilab@gmail.com, `SMTP_PASS`. **See the interlock note below — this is no longer a neutral unblock.** |
| **B2** | OpenAlex source skipped every run (409 since 2026-02-13) | free `OPENALEX_API_KEY` → `.env` (`fetch_new_papers.py` now genuinely reads it) |
| **B3** | Semantic Scholar throttled/shared | free `SEMANTIC_SCHOLAR_API_KEY` (optional) |
| **B4** | `crawl.mjs` full-text crawl | **Node 24** install. **Down-ranked**: the Python fetcher already covers S2/arXiv/EuropePMC/PubMed/bioRxiv/medRxiv/Crossref/DOAJ keylessly, so discovery without node is *degraded, not dead*. The "reclaim ~1 GB in `lab-reservation-main`" part is housekeeping, not a feature unblock |
| **B6** | GRM Notion enrich 404s weekly | share the integration into the GRM schedule DB `4088bc86…` |

**B1 is a safety interlock, not a convenience blocker.** Two premises changed since this was
written:
- Commit `ab524ff` **already filled 6 of 7 addresses** in `config/researchers.yaml`. Verified in
  `.env`: `SMTP_HOST` set; `SMTP_USER`/`SMTP_PASS`/`SMTP_FROM` empty. So supplying the app
  password **arms six live recipients in one step**, including the JYK address in A2.
- `check_materials.py --send` is a **new researcher-facing SMTP egress added by this run**, and
  its accusation logic is not yet trustworthy (§C5). The only reason it has never mailed BYL
  about 33 fabricated missing decks is that SMTP is empty.

**B5 carries one hazard worth knowing before you click.** Notion sharing grants schema-edit
rights. The response channel keys on hardcoded property names (`Title` · `저자` · `APA` ·
`읽음`), and `capture_responses.py:114-118` handles drift by hard **ABORT**, which the cron
treats as an rc=2 hard gate. So one researcher renaming a column stops the entire weekly routine
for all seven — and re-running `provision_notion.py` to repair it will DROP properties a
researcher added. Not a reason to delay B5; a reason to tell the researchers not to rename
columns, and to fix §D7 first.

**B7. Credential rotation (security).** A **live `SLACK_BOT_TOKEN`** (58 chars) is in `.env`, and
two `ntn_` Notion tokens were exposed in chat earlier. The stale `.APPROVED` token was disarmed,
but **I will not delete or rotate a credential** — that's yours. **Rec: rotate all three.**
Narrowing an earlier claim honestly: disarming the token closed one old RID, **not the gate** —
`scripts/_legacy/run_weekly_cron.sh:82` still self-mints `touch state/.APPROVED_$RID` for
whatever RID it creates. It is dormant (`state/.CRON_ENABLED` absent; `launchctl` shows only
`com.csnl.paper-rec.grm` from this repo), and the `touch` line should simply be deleted.

**B8. What database credential do the researchers actually hold? (NEW — highest security value)**
Live: `current_user = postgres`; `has_table_privilege(current_user, 'csnl_research.projects',
'UPDATE') = true`; `has_table_privilege(current_user, 'csnl_ops.researchers', 'UPDATE') = true`;
`SELECT count(*) FROM pg_roles WHERE rolname='csnl_archive_user'` → **0**. The `csnl_research`
read-only boundary is **client-side prose**. The least-privilege role designed for exactly this
— `state/provision/csnl_archive_user.sql`, which grants only `SELECT ON csnl_research.projects`
— **was written and never provisioned**. This compounds with distribution: `plugin/scripts/_pdb.py`
loads the same `SUPABASE_DB_*` contract from a researcher's laptop, behind a client-side regex
whitelist. **If the credential handed to researchers is the shared `postgres` role, six laptops
hold write access to the entire instance** — including lab-reservation's `public` schema.
*Ask:* what did the researchers receive? **Rec: provision `csnl_archive_user` and re-issue.**
It is already written, it is grants-only, and it also makes A4's "who writes the canon?" question
decidable rather than rhetorical.

---

## C. Deletion / retirement

**C1. F-SKILLDRIFT tier-2.** `dedup_snapshot`, `apply_feedback`, `fetch_replies`,
`migrate_legacy_ledger` retire via a **4-file, ~8-line** `.claude` contract edit. Their tables
have been dormant since 2026-05. Tier-1 (4 scripts, zero refs) is already retired, along with
`send_survey_invite` and `archive/__init__.py`. **Rec: approve.**
*(One correction: `build_review_packet.py` was on the orphan list and has been kept — it is
referenced by `docs/DISCOVERY-RUNBOOK.md:41` as the adversarial-review packet builder.)*

**C5. Keep `check_materials.py --send` off until its audit can actually fail. (NEW)**
The BYL-33 bug you caught was the *date-parsing* branch. The review found three more in the same
family, all still live:
- The **folder-missing** branch was never touched. `nas_catalog.json` records a per-person
  `dirs_absent` list — and for BHL even carries the adjudication *"genuinely does not exist; a
  TRUE gap, not a rule bug"* — but `check_materials` **never reads `dirs_absent`**. Worse, **SK
  has 28 `milestone_meetings` rows (six of them in nine days) against exactly one file in
  `MM/SK`**, so any `--send` with a >4-week window accuses 김성제 of ~27 missing decks.
- **`--audit`, the control meant to catch this, is a tautology.** It derives "real meeting days"
  from the filenames in the same folder, then checks whether a file with that date exists there.
  It always does — `new%` is **always 100 %**, whatever the matcher does. The catalog itself
  flags the load-bearing assumption as `"unverified"` with `tol_days: 0`; a systematic ±1-day
  naming convention would yield 100 % false gaps *and* a PASS.
- The **GRM/PB half is a silent no-op**: blank-presenter rows are skipped *before* the counter
  and with no warning, and live `paper_blitz` is **0/52** presenters. The report prints `GRM=0`,
  indistinguishable from "all present" — the mirror image of a false accusation.

**Rec: approve keeping `--send` off**, and require, before it is ever enabled: consult
`dirs_absent`; give `audit()` a calendar-anchored arm (join `csnl_ops.milestone_meetings`, report
matched/total + a Δdays histogram — that is the number that would have set `tol_days` honestly);
warn rather than drop on blank-presenter events; wrap `send_mail` in `try/except` so a mid-list
SMTP refusal doesn't re-mail everyone next run.

**Deleted from this interview — recorded so they are not re-opened:**
- ~~C2 "physically drop the 4 `*_dead` tables?"~~ — the question stated its own answer: 0 rows,
  112 kB, `init_db` can no longer resurrect them, recommendation was "do nothing". **Decision
  recorded: leave them renamed indefinitely.** No answer needed.
- ~~C3 `scripts/timeexp/backup-to-nas.plist`~~ and ~~C4 TimeExpOnline data integrity~~ — both
  live in `lab-reservation`, unrelated to paper-rec's thesis, and C4 is already disabled and
  stable. **Routed to that repo's backlog.** Carrying them here only dilutes the ask.

---

## D. Recommender policy

**D1. MSY cold-start — RE-SCOPED. The original premise was false.**
The old item said ops holds *"the only coherent profile that exists anywhere"* for MSY. Live:

```
MSY | cat_mag_main    | analysis        | conf 0.86 | updated 2026-05-14 | purpose 659 chars
MSY | face_cond_ver10 | data_collection | conf 0.75 | updated 2026-05-14 | purpose 640 chars
archive_researcher_queues WHERE researcher_id='MSY' → 200 rows
```

MSY has **two eligible, grounded project rows** (with `_grounding` arrays pointing at
`nas:MSY/Data/cat_mag_main/README.md`) **and a full queue**. The ops summary is, by O1's own
provenance chain, a ~60-char-truncated rendering of *those same rows* — importing it adds
provenance noise for zero information gain, and re-stamps May values with a July date.

MSY's actual gap is narrower and different: **0 interview responses** and a blank
`archive_survey_*` layer, so P28 falls back to legacy. *Options:* (a) onboard MSY for real (one
interview); (b) derive `archive_survey_aims` from `csnl_research.projects` **directly,
in-pipeline, with no cross-repo copy**; (c) the original ops import. **Rec: (a), with (b) as the
interim** — and **not (c)**. Note MSY is also the worst person in the lab to hand an incoherent
recommendation to (see the corpus item in the log, `b06-CORPUS`: a corrupted row is currently
**A-tier for MSY**), and he cannot be helped at all by the behaviour classifier (0 labels).

**D2. Load the starved P28 signals — approve, but it is a no-op unless you also name a consumer.**
`archive_survey_negatives` / `_pis` / `keywords.operational_def` are empty — in fact **all seven
`archive_survey_*` tables are 0 rows** — so definition-subtraction is dead code and the PI rerank
is inert. But the reason it is inert is deeper than "no data":

> `build_researcher_queue.py` — the `builder='brq'` producer of **all 1400 live queue rows**, and
> the only builder `build_digest.py` accepts — contains **zero** references to `archive_survey_*`
> **and zero references to `archive_responses`.** The only consumer is the parked `recommend.py`
> (`builder='p28'`, **0 rows in the DB**). 586 human labels, 100 % reason-annotated on the
> negative side, and not one line of the live path reads them.

So as previously specified, D2 loads data into a parked consumer and **changes nothing a
researcher sees.** *Options:* (a) route `archive_survey_negatives` + `keywords.operational_def`
into `brq` as deprioritise-priors — no engine swap needed; (b) promote `recommend.py` (see D3);
(c) load anyway and accept it is decorative until (a) or (b).
**Rec: (a)**, keeping D2's own guidance that these are **deprioritise-priors, not hard vetoes**
(P24 proved blind vetoes misfire — save and reject `connecting_signal`s overlap heavily).
**Ordering, which the old item did not state: the researchers' own survey rows must land BEFORE
any ops-derived `confidence='low'` rows**, or the low-confidence import becomes the incumbent and
the high-confidence survey arrives as an upsert conflict.

**D3. Promote the P28 connection recommender? Keep parked — and fix the key before you shadow it.**
**Rec unchanged: keep parked** until the non-circular eval (D4) exists. **New precondition:**
`archive_researcher_queues`'s primary key is `(researcher_id, canonical_id)` — **`builder` is not
in it** — and both writers guard with `… DO UPDATE … WHERE queues.builder = EXCLUDED.builder`.
So whichever builder inserts a row first **owns it permanently**, and the loser's write is a
silent no-op with no error and no row-count signal. Under that key, a p28 shadow run can only
score the papers `brq` did *not* pick — comparing a challenger on the incumbent's rejects is not
an A/B test, it is a guaranteed under-report. **Add `builder` to the PK (or write shadow
rankings to a separate table) before D3 runs.**

**D4. Non-circular evaluation — approve, but one cheap thing must come first.**
Today's only metric is circular (computed over papers the policy itself surfaced). The proposal
(temporal split + `asreview-insights` + ground truth from researchers' own reference lists)
stands. **But `archive_responses` records only `(researcher_id, canonical_id, session_id, choice,
choice_detail, responded_at)` — no tier, no rank, no composite, no build_token, no source at time
of showing.** Only **247 of 534** save/reject labels join to any current queue row, and
pre-2026-06-04 rows join against a *different* queue, silently. **Every offline eval in this
document is built on that broken join.** A `shown_context_jsonb` written at capture time costs
~20 lines, is additive and reversible, and unblocks temporal splits, per-tier calibration, source
priors **and** D5's propensity slot. **Rec: approve D4, with the capture column first.**

**D5. Log an exploration slot.** Reserving 1 of the 5 weekly slots as randomised makes off-policy
(IPS/SNIPS) evaluation possible; never logging it makes that permanently impossible. Costs ~1
slot. **Rec: approve — and land it in the same change that restarts delivery**, since it is the
one item whose cost strictly increases with delay (every week without it is a week of
permanently un-analysable logs). It is now folded into the same capture change as D4.

**D6. JYK's aim rewrite — approve the diagnosis, gate the intervention. (NEW)**
JYK is at **28.8 %** precision (34 save / 84 not_relevant), the worst figure this system has ever
recorded — **and it degraded after the targeted P24 repair** (32.6 % before, 13.0 % on the 23
responses since). Batch-04 proposes another intervention of exactly that class (rewrite his aims
from the ops record). His 84 rejections all carry Korean reason text, and they show why lexical
fixes keep failing:

```
Robust working memory in a two-dimensional continuous attractor network
  → neural field 기반 분석 모델이라 task-optimized RNN 접근과는 결이 다름
Corvids optimize working memory by categorizing continuous stimuli
  → corvid 종 비교 … task-optimized RNN 프로젝트와는 결이 다름
Stochastic attractor models of visual working memory
  → multi-item swap error 가 초점이라 관련 적음
```

The rejected papers are **lexically on target** — working memory, attractor, RNN, efficient
coding, serial dependence. The discriminator is a **stance** axis (task-optimized network with a
manipulable loss vs. a hand-built dynamical/analysis model of the same phenomenon) plus a
**scope** axis (single-item representational geometry, not multi-item/distractor/serial
dependence). Bag-of-words cannot represent that: he has also *saved* a serial-dependence paper,
so the term carries no polarity for him, and a classifier trained on his labels puts serial
dependence back in his top-8.

*Options:* (a) rewrite his aim tuple from the ops record as proposed; (b) **ask JYK directly** and
encode the stance axis as `archive_survey_negatives` from his own reason text; (c) the mechanical
mitigation only. **Rec: (b), with (c) shipped immediately regardless.** The mechanical part is
measured and cheap: precision by paper provenance splits **`live_search` → JYK 13.6 % vs SMJ
60 %**, yet P26 live papers occupy 81–92 of every researcher's 200 queue rows *uniformly*. A
per-researcher source prior (or simply throttling `live_search` for JYK until his negatives are
encoded) is ~10 lines with a directly measured effect. **Rewriting someone's research aim needs
their word** — that is why (a) is not the recommendation.

**D7. The live channel has no "not relevant" control, so the rational way to dismiss a paper is
to lie about having read it. (NEW — fix before B5)**
The digest DB gives the researcher exactly two actionable properties: `읽음` (checkbox) and
`발표 예정` (checkbox). A `읽음` tick writes a permanent `already_read` row
(`ON CONFLICT DO NOTHING`, never overwritten, never deletable — and `expire_pending.py` was
deleted in the P23 rolling redesign, so an unwanted row never expires either). Consequences of a
false `already_read`: the paper is excluded from that researcher **for ever**; it counts toward
the 10-multiple belief-update trigger, so belief updates fire on fabricated evidence; and under
`--use-behaviour` (`pos = save/read`) a dumped paper becomes a **positive interest signal**.
Meanwhile `not_relevant` — the lab's single most valuable signal (BHL 69, JYK 84, SMJ 46 rows) —
came from the retired plugin interview and **cannot be produced through the live channel at all**,
which is precisely the signal the two below-floor researchers most need to give.
*Also:* `발표 예정` is written by `send_notion.py:107` and **read by nothing**, while
`notify.py:109,121` instructs researchers to tick it every week.
**Rec: add a 관련 없음 affordance to the digest DB before sharing it; until then, treat
`already_read` arriving from the weekly channel as low-confidence.**

---

## E. Architecture

**E1. Repo merge — I recommend NO** (unchanged conclusion, better reasons). The previously
stated reasons — live Vercel deploy, 86 uncommitted changes, two toolchains — are the *weakest*
available; they are transient. The durable ones are structural: (a) the NAS is LAN-only and
csnl-ops's own `AGENTS.md` forbids NAS access from Vercel/CI, so the two systems have physically
disjoint execution environments; (b) paper-rec ships a **versioned plugin to researcher laptops**
— a release surface csnl-ops has no business inheriting; (c) the schemas are already separated
with FKs in the right places. Merging solves none of the duplications the canon survey found; a
table plus a drift check solves all of them. **Rec: keep repos separate.**

**E2. The cross-repo eligibility contract — REWRITTEN as a two-option decision.**

*The problem is real and is firing today.* `build_researcher_queue.py:107`,
`build_fingerprints.py:126,147`, `plugin/scripts/profile_show.py:28`,
`plugin/scripts/preflight.py:92` and `pipeline/00_select_projects.py:32` — **six copies** — gate
on `phase IN ('data_collection','analysis','manuscript_draft') AND confidence_avg >= 0.7`, two
fields **only csnl-ops writes**, with no contract and no alarm. (A seventh definition,
`csnl_assistant.dim_researcher`, has no gate at all.)

*But three of the old proposal's premises are wrong, verified live:*

1. **"Extend the enum" has no referent.** `csnl_research.projects.phase` is **nullable free text
   with no CHECK and no enum type**; the table's only constraint is its primary key. Live values
   already include the archiver-invented `'mapping (Stage 1 broad map)'` and `'deprecated_stub'`.
   The gate is a **closed whitelist against an open vocabulary the other repo mints at will** —
   adding two allowed values just moves the next silent drop further out.
2. **The decay failure has never fired.** 11 of 14 project rows are eligible and **no active
   project has ever decayed below 0.7.** Meanwhile the two *correct* exclusions are correct **by
   accident**: `bhl_sk_organization` is a file-tidying admin task (`purpose: organization_aim`)
   excluded only because ops happened to write an unrecognised phase string; `syj_jsl_sd_onboarding`
   is titled `[DEPRECATED STUB] … actually misidentified`. Two accidental hits, one confirmed
   miss — not a filter worth a cross-repo dependency.
3. **A different failure IS firing.** `SMJ / visual_search`: `phase = NULL`,
   `confidence_avg = 0.95`, `last_updated_at = 2026-06-02` — **the freshest row of all 14**.
   `NULL IN (...)` evaluates to `NULL`, so it fails `WHERE` at **all six sites**. SMJ's entire
   interest text is therefore built from `concentricity` alone, whose `background_jsonb` and
   `connected_graph_jsonb` are both NULL — and SMJ is at **43.2 %**, below the floor. A
   visual-search project vanishing from a concentricity/eye-movement researcher's profile is not
   a rounding error. *(This is a distinct, previously unreported defect — not the P27 "SMJ
   Project-3" incident, which was about a project absent from the DB. This one is present, fresh,
   and dropped by NULL semantics.)*

| | **Option 1 — sever the control coupling (DEFAULT / recommended)** | Option 2 — document the contract (the original proposal) |
|---|---|---|
| mechanism | new `csnl_paper_rec.archive_project_eligibility(init, project_slug, include bool DEFAULT true, reason, decided_at)`; all six sites join it with **no `phase`/`confidence_avg` predicate at all** | publish `csnl_core.v_paper_rec_eligible_projects`, extend the enum, raise on empty |
| seed | today's 11 eligible rows + the 2 editorial exclusions with real reasons ("admin/organisation task, not research"; "deprecated stub") | — |
| who owns the policy | **paper-rec** — a table it may write | csnl-ops, in a schema that does not exist |
| new project appears | `include = true` by default → **noisily included** (correct direction for a recommender) | silently excluded if ops invents a new phase string |
| `phase` vocabulary dependency | **removed** | retained |
| adoption risk | one migration, six one-line call-site edits (two of which are a plugin release — see A4) | a view six sites don't adopt becomes an **eighth** definition of "researcher" |

**Rec: Option 1.** It is strictly smaller (no new schema, no cross-repo view, no enum migration
in a schema paper-rec doesn't own) and strictly stronger. Ship the **one-line NULL fix**
(`AND (phase IS NULL OR phase IN (...))` at all six sites) immediately either way — it restores
SMJ's freshest project today.

**Two amendments to the alarm, whichever option you pick:**
- **Alarm on *one* eligible project, not only zero.** BYL and JYK each hang on a **single** row;
  BHL and SMJ have one eligible of two. One decay step empties four researchers' queues.
- **The real failure mode is *stale*, not *empty*.** A researcher with no eligible projects is
  `print`ed and `continue`d, so they never enter `rids`, so the per-rid prune never runs, so
  **their previous 200 queue rows survive untouched indefinitely** and `build_digest` reads a
  full, healthy-looking queue built from a profile that no longer exists. Empty is loud; stale is
  invisible. The rows already carry `built_at` — assert freshness in the weekly routine.

**E3. Move scheduled work to GitHub Actions?** paper-rec's local launchd has failed silently for
weeks; csnl-ops's GH Actions crons work. Constraint: anything touching the NAS is LAN-only and
**cannot** move. **Rec changed to: defer to go-live.** paper-rec's cron is *deliberately* dormant
(`.P23_ENABLED` absent) and the launchd failures have a known cause (TCC / Full Disk Access, per
your own memory note). Migrating the scheduler of a system that is intentionally not running yet
is premature; do it as part of turning delivery on, alongside B5.
