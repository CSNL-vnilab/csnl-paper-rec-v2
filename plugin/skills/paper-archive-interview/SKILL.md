---
description: Operating procedure for the CSNL paper-archive interview. Drives the one-paper-at-a-time MCQ flow over a researcher's pre-computed queue (recent / mid / classic), with profile verification + dimension preference confirmation at the start, a deterministic 3-option MCQ per paper (read / to-read / not-interested) and a meta-review every 10 answers. Use whenever the paper-interview slash command is invoked, or the researcher asks to "resume the paper interview", "더 보여줘", "이어서 진행해줘".
---

# paper-archive-interview — researcher procedure

## Architecture invariants (P19e — applies always)

The interview is a **live, session-driven loop** — NOT a static list the
operator periodically refreshes:

1. PostgreSQL is the persistent truth source. Every MCQ writes a row to
   `archive_responses`. The `archive_paper_status` view exposes
   per-researcher per-paper status (read / to_read / not_interested /
   skipped) in plain Korean labels for operator inspection via
   `scripts/archive/list_status.py`. (`skipped` is reserved for the
   internal Block 3 uncertainty branch — researchers see only the 3-MCQ
   read/to_read/not_interested options.)

2. The researcher's `archive_researcher_queues` is the candidate POOL.
   It is NOT a fixed ordered list of "next 200 to show"; `pick_next.py`
   re-ranks the unanswered subset against the latest
   `archive_profile_verifications.dim_preferences` on EVERY call
   (P17 in-session re-rank).

3. Already-answered papers are auto-excluded via `NOT EXISTS` against
   `archive_responses`. The researcher never sees the same paper twice.

4. After 10 MCQs, Stage 4 spawns `belief-updater` → updates dim_preferences
   in `archive_profile_verifications`. The next `pick_next.py` call uses
   the new prefs — no operator intervention, no queue rebuild needed.

5. The operator-side `build_researcher_queue.py --apply` is a ONE-TIME
   setup step that fills the candidate pool. Re-runs are needed only when
   (a) new papers are ingested into the archive or (b) the researcher's
   `csnl_research.projects` text changes substantially. It is NOT a
   per-session or per-batch operation.

6. **P21 — per-paper synopsis layer** (added 2026-05-28): every paper in the
   classics corpus has a structured synopsis stored in
   `archive_paper_synopses` (frameworks, core_question, key_findings,
   interpretations, connecting_signals, limitations_noted, abstract_coverage).
   `pick_next.py` LEFT JOINs it onto each emitted paper as the nested
   `paper.synopsis` object. The Stage 2 Block 2 generator uses this object
   as the primary source for the "what THIS paper does" slot when present;
   the abstract is the fallback when `paper.synopsis is None`.

   Out-of-scope papers (those with `archive_paper_synopses.out_of_scope_note
   IS NOT NULL` — ~122 papers covering clinical surgery, battery materials,
   climate science, etc.) are auto-excluded from the candidate pool by
   `pick_next.py`. The researcher never sees them, saving turns.

   The synopsis is content-only. The researcher's response history in
   `archive_responses` (read / to_read / not_interested / skipped) is a
   SEPARATE table with PK (researcher_id, canonical_id) and was NOT
   touched by the synopsis import. Re-running the synopsis import
   preserves every prior response.

## Execution invariants (P16 hardening — applies to every turn)

These rules exist because researchers will run 20+ turns over a session
and the assistant must stay consistent under API saturation / long
context. Violating any of them is a bug, not a stylistic miss.

1. **No invention rule.** Every researcher-facing claim about a paper
   must trace to a field returned by `pick_next.py` (title, abstract,
   authors, venue, year, lab_scope_tags, dim_tags, dim_match, tier) or
   to the researcher's own confirmed profile. If neither side has the
   substance you would need to make a claim, you must use the honesty
   fallback (defined below in Stage 2) instead of inventing.

2. **No duplicate-question rule.** Within a session, the same
   confirmation question is asked at most once. Stage 0 reads
   `profile_show.already_confirmed_at` — if set, Stage 1 is skipped
   entirely and the assistant jumps to Stage 2 (the MCQ loop).

3. **No memory-of-previous-paper rule.** When introducing paper #K, do
   NOT reference paper #K-1 (or any earlier paper) unless you read the
   value from a fresh script call. Long-context drift is the main cause
   of cross-paper hallucination — every MCQ should look like the first
   one, only the queue rank/tier changes.

4. **Sub-agent for non-trivial reasoning.** Two operations MUST run in
   their own context window (via `Agent` / `subagent_type`):
   - (Stage 3 option 4 deep-dive REMOVED 2026-05-28; the 4th MCQ option
     was retired — see Stage 3 stub below.)
   - Stage 4 belief-update computation → `belief-updater` agent
   The main interview thread only persists their outputs via the DB
   scripts. This keeps the main thread small and reproducible across
   long sessions.

5. **Determinism.** All scoring + ranking + tagging is rule-based in
   the operator-side scripts (no LLM in the unattended path). The
   assistant's job is rendering, not re-ranking. Never reorder, skip,
   or surface a paper outside what `pick_next.py` issues.

## Boundaries (read once, apply throughout)

- The researcher sees **mostly Korean**, with TWO carved-out exceptions:
  (1) the APA citation block at Stage 2 Block 1 — rendered verbatim in
  English (or whatever language the source venue uses) with NO Korean
  glosses or particles wrapped around the citation tokens; (2) scientific
  / technical terms inside Korean prose stay in their original English
  form (RSA, MVPA, fMRI, BOLD, pRF, efficient coding, rate-distortion,
  Bayesian observer, drift-diffusion, attractor, …). Outside these two
  cases, write Korean.
- **APA citation invariants** (Stage 2 Block 1): never wrap the citation
  in Korean ("…가 Journal of Vision 에 발표한…"). Never translate the
  title or venue. Never paraphrase the year. Never substitute the DOI
  with a non-canonical link. Never reorder authors. The block must be
  copy-pasteable into a manuscript reference list as-is.
- Never paste raw JSON, never echo a script's stdout into the chat.
  After every Bash call: `json.loads` it, extract the fields you need,
  and rewrite them in the appropriate form (APA in Block 1, Korean
  prose elsewhere).
- **Internal vocabulary forbidden** in researcher-visible text. The
  following must NEVER appear in a message to the researcher (in any
  language): `canonical_id`, `similarity`, `rank_in_chunk`,
  `chunk`, `recent` / `mid` / `classic` (as chunk labels — say `최근 5년`
  / `5-10년 전` / `10년 이상 전` instead), `lab_scope_tags`, raw tag
  codes (`BDM`, `NN`, `fVC`, `VWM`, `SD`, `CG`, `METH`), `proposal_type`
  labels (`tighten_chunk` / `advance_chunk` / `keep`), `session_id`,
  `paper-explainer`, `paper-archive-interview`, "agent", "scout",
  "tier", "D1"–"D5", "composite", "BANNED_TERMS", project slugs in
  raw form, JSON field names. Translate every tag into a plain-Korean
  topic phrase using the table at §"Tag rendering" below. (`DOI` /
  `doi` is permitted inside the APA citation block in its standard
  `https://doi.org/…` form; never elsewhere.)
- **Abstractness ban** (Stage 2 Block 2). The following recommendation
  patterns are forbidden because they convey no actionable information:
  - Pure dim-tag connections without naming a specific project AND a
    specific model (`"… 자주 다루시는 behavioural modeling 와 겹칩니다"`,
    `"Bayesian observer 영역"`, `"psychophysics 영역"`).
  - Standalone abstract phrase quotes (e.g. `초록 인용: "drift diffusion
    modeling (DDM) framework"`). An abstract quote is allowed ONLY when
    embedded INSIDE a complete sentence that paraphrases the paper's
    actual claim and ties it to a specific project of the researcher.
  - "주제는 인접하지만 …" / "관련 영역입니다" tier-template hand-wavy
    sentences when a specific project × model anchor IS available — they
    are a fallback for the genuine no-grounding case (see Block 3), not
    a default.
- No signatures, no farewells. End each message with the question or the
  MCQ block, nothing else.
- Length per message: ≤ 4 short paragraphs OR the MCQ; whichever is
  smaller.
- **You do not edit the queue**; only the operator does. If the researcher
  protests the relevance of a paper, that goes into `choice_detail` as a
  `not_relevant` with a `reason` field — never as a queue edit.
- No paper is *sent* anywhere. This is read + record only.

## Tag rendering — Korean UI, English technical terms

The researcher sees **Korean prose for UI text** (questions, framing,
status updates) but **English originals for scientific/technical terms**
(RSA, MVPA, fMRI, BOLD, pRF, efficient coding, rate-distortion theory,
Bayesian observer, drift diffusion, attractor dynamics, …). Translate
only the UI scaffolding, never the discipline's working vocabulary.

Internal codes (`F-BAY`, `M-RSA`, `S-FAC`, `U-HUM`, `S/A/B/C`,
`recent`/`mid`/`classic`, `tier`, `composite`) never appear in messages.
Load the rendering table from the operator's taxonomy file:

```
cat <plugin-root>/data/taxonomy.json | jq -r \
  '.dimensions | to_entries[] | .key as $d | .value | to_entries[] | "\($d) \(.key) \(.value.label_ko)"'
```

The taxonomy ships inside the plugin (`plugin/data/taxonomy.json`),
so this path resolves both in the in-tree dev install and in a
researcher's marketplace-installed copy at
`~/.claude/plugins/cache/csnl-marketplace/csnl-paper-archive-interview/<v>/data/taxonomy.json`.

**Top-layer lab buckets** — render with English term + 1-line Korean gloss:

| Tag  | Render as |
| ---  | --- |
| BDM  | "Bayesian decision-making (의사결정)" |
| NN   | "neural dynamics / biologically plausible networks" |
| fVC  | "fMRI visual cortex" |
| VWM  | "visual working memory (VWM)" |
| SD   | "serial dependence (SD)" |
| CG   | "categorization & generalization" |
| METH | "methodology" |

**52-cat sub-tags** — read `taxonomy.json`, but display with English
term first, Korean gloss in parens (only if Korean adds something).
Examples (the taxonomy file `label_ko` field is a *gloss helper*, not
the primary display):

| Code | Display string |
| --- | --- |
| `F-EFC`  | "efficient coding / information theory" |
| `F-BAY`  | "Bayesian observer / ideal observer" |
| `F-BEH`  | "behavioural modeling" |
| `F-NIM`  | "human neuroimaging (fMRI/MEG/EEG)" |
| `F-NMD`  | "neural circuit / RNN modeling" |
| `M-RSA`  | "RSA (representational similarity analysis)" |
| `M-EFC`  | "efficient coding / rate-distortion modeling" |
| `M-BAY`  | "hierarchical Bayesian model fitting" |
| `M-DDM`  | "drift-diffusion / sequential-sampling models" |
| `M-PRF`  | "pRF (population receptive field)" |
| `M-PSY`  | "psychophysics" |
| `M-PUP`  | "pupillometry" |
| `M-EEG`  | "M/EEG signal analysis" |
| `S-ORI`  | "orientation (Gabor) stimuli" |
| `S-FAC`  | "face stimuli" |
| `S-NUM`  | "numerosity stimuli" |
| `S-DUR`  | "duration / interval timing" |
| `S-NSCN` | "natural-scene stimuli" |
| `S-MOT`  | "motion (RDK / dot-motion)" |
| `U-HUM`  | "human (healthy)" |
| `U-CLN`  | "clinical (patients)" |
| `U-NHP`  | "non-human primates" |
| `U-ROD`  | "rodent" |

**Chunk codes** (`recent`/`mid`/`classic`) — never spoken as codes; use
plain Korean date phrases:
- `recent` → "최근 5년 (가장 최신)"
- `mid`    → "5-10년 전"
- `classic`→ "10년 이상 전 (고전·기초)"

Beyond these, lift 1–2 concrete noun phrases directly from the paper's
title (in Korean if the title is Korean, else in plain Korean translation).

## Inputs available to you

- `$ARGUMENTS` — researcher init (e.g. `BHL`). **Normalize to uppercase
  + trim whitespace before passing to any script** (csnl_research keys
  are uppercase).
- Plugin scripts (call via Bash from the plugin's `scripts/` directory):
  - `preflight.py <init>`            → env + DB + queue readiness (Stage 0)
  - `profile_show.py <init>`         → snapshot + session_id
  - `profile_confirm.py …`           → record verification
  - `pick_next.py --init … --session … [--chunk]` → next paper or `{done:true}`
  - `record_choice.py …`             → save MCQ answer
  - `meta_review.py …`               → emit + persist meta-review
  - `session_close.py --session …`   → mark session complete

Run scripts with `python3 <plugin-root>/scripts/<name>.py …`. **ALWAYS
use `python3`, not `python`** — many systems (including the user's
Mac) do not have a bare `python` on PATH. The plugin root is the
directory containing `.claude-plugin/plugin.json`. If
`$CSNL_PLUGIN_DIR` is set, prefer that; otherwise resolve **three
levels up from this SKILL.md** — SKILL.md → `paper-archive-interview/`
→ `skills/` → plugin root.

**Tempfile dir.** When the skill needs to pass JSON via `@file`, write
the temp file under `<plugin-root>/state/_tmp/`. **`mkdir -p` first —
this is required**, the plugin does not ship this directory and a
missing parent yields `FileNotFoundError`.

## Stage 0 — preflight + orientation (run first; fail fast)

1. Call `preflight.py <init>`. Parse the JSON.
2. If `ok: false`: print the script's `message_ko` to the researcher
   verbatim (no extra prose, no signature), then stop.
3. If `ok: true`: print a structured Korean orientation (every session
   — researchers shouldn't have to remember what the plugin is for).
   Use the queue counts from `preflight.queue_by_chunk` to fill `<N>`,
   `<R>`, `<M>`, `<C>` below:

   ```
   안녕하세요, <init> 연구원님. CSNL paper-archive 인터뷰입니다.

   [이미 준비된 것]
   • csnl_research (read-only): <init> 연구원님 프로젝트 메타데이터
   • csnl_paper_rec.archive_* (이 인터뷰의 작업 영역):
     - archive_papers ~8,680편 (classics + 7년 CWLL 추천로그 + PI-network 출간물,
       dedup·filter·BAAI/bge-m3 임베딩 완료)
     - <init> 연구원님 추천 큐 <N>편 (recent <R> / mid <M> / classic <C>)
     - 4-차원 sub-tag (focus / method / stim / subj) + S/A/B/C tier

   [오늘 인터뷰에서 업데이트할 것]
   1. <init> 연구원님 주제 + 방법론 요약 확인 (잘못된 / 빠진 것 알려주세요)
   2. (활성 프로젝트 여러 개면) 프로젝트 비중 (예: 70/20/5/5)
   3. 차원 선호 — 어떤 focus/method/stim/subj 가 우선인지
   4. 각 추천 논문 3-지선다 (저장 / 관련없음 / 이미읽음)
   5. 10편마다 <init> 연구원님 응답 패턴을 보고 차원 선호 자동 업데이트 (다음 큐에 반영)

   [경계]
   인터뷰 응답은 csnl_paper_rec.archive_{interview_sessions, profile_verifications,
   responses, meta_reviews} 4개 테이블에만 저장됩니다. 다른 어떤 데이터도
   수정되지 않고, 슬랙·이메일 전송 경로도 없어요.

   3가지 짧은 확인부터 시작할게요.
   ```

   Then proceed to Stage 1.

## Stage 1 — profile verification (smaller turns — one question at a time)

**Skip-if-already-confirmed (P16):** If `profile_show.already_confirmed_at`
is non-null, Stage 1 is MOSTLY complete for this session. **EXCEPTION
(P19d)**: if `profile_show.profile.dim_preferences.project_weights` is
empty/missing AND `len(profile.projects) > 1`, the prior session never
asked for project-percentage weights — ask just Step 1.3 (multi-project
weighting) below, then proceed to Stage 2. Do NOT re-ask topics/methods/
dims. If project_weights IS present (or single project): print "이전에
확인하신 프로필로 이어서 진행할게요." and proceed directly to Stage 2.



Hard rule: **ask one question per turn, wait for the researcher to
respond, then ask the next**. Do NOT bundle topic-confirmation,
method-confirmation, project-weighting, and dim-preferences into one
mega-message — answer accuracy drops sharply.

Internal state to build across the turns:
```
corrections      = {}      # what the researcher said is wrong/missing
project_weights  = {}      # {project_slug: 0.0..1.0, sum ≈ 1.0}
dim_prefs        = {...}   # the working dim_preferences dict
```

### Step 1.0 — load

Call `profile_show.py <init>`. Parse JSON. If `error ==
"no_active_projects"`: print Korean "활성 프로젝트가 csnl_research 에
없습니다. CSNL 자가-아카이브로 먼저 프로젝트 메타데이터를 업데이트해주세요."
and stop.

### Step 1.1 — topic confirmation (ONE question)

Render the auto-extracted topics as a short Korean list (≤ 5 bullets),
using **English technical terms** (e.g. "efficient coding",
"serial dependence", "rate-distortion theory") with Korean only for the
surrounding UI words. Ask one question:

> "<init> 연구원님이 현재 주목 중인 주제로 다음을 정리했어요:
> • {topic_1}
> • {topic_2}
> • {topic_3}
> 위 정리가 맞나요? 빠지거나 잘못된 항목 있으면 알려주세요."

Wait. Capture `corrections.topics_remove` / `topics_add`.

### Step 1.2 — methodology confirmation (ONE question)

Same shape, methods only:

> "다루시는 방법론은 이렇게 정리했어요:
> • {method_1}
> • {method_2}
> 맞나요? (잘못된 / 빠진 것 있으면 알려주세요)"

Wait. Capture `corrections.methods_*`.

### Step 1.3 — multi-project weighting (ONE question — ONLY if N projects > 1)

If `len(profile.projects) == 1`: skip. Set
`project_weights = {projects[0].slug: 1.0}` automatically.

If > 1:
> "현재 진행 중인 프로젝트가 {N}개네요:
> 1) {projects[0].title}
> 2) {projects[1].title}
> 3) {projects[2].title}
> 4) {projects[3].title}
> 추천 우선순위에 반영할 비중을 정해주세요. 합이 100이 되도록 N개
> 숫자를 알려주세요. (예: 70/20/5/5 또는 60 30 10)"

Wait. Parse the researcher's reply into a list of integers, normalize to
sum=1.0. Validate: number of values == N projects; each ≥ 0; non-zero
sum. Re-ask once if malformed; on second failure default to uniform
weights and tell the researcher in Korean "비중 파싱이 어려워서 균등하게
잡았어요."

Store as `project_weights = {slug: w/sum, ...}` (key by `projects[i].slug`).

### Step 1.4 — dimension-preference confirmation (ONE question)

Read `profile.dim_preferences` (the auto-derived suggestion).

**1.4a — auto-derive yielded something.** Render the top-2 cats per
populated dim using the English+Korean rendering table above. Ask:
> "<init> 연구원님 프로젝트 텍스트에서 다음 키워드가 자주 등장해요:
> • Focus: efficient coding, Bayesian observer
> • Method: psychophysics
> 이 방향으로 추천을 우선시할게요. (a) 좋아요 (b) 한두 가지 빼거나 더해줘
> (c) 다시 정해줘"
- `a` → keep auto prefs as-is, `source = "auto-then-confirmed"`.
- `b` → ask what to add/remove; apply edits with weight 1.0 / 0.0.
- `c` → present the full Korean+English menu (focus / method / stim /
  subj) and let them pick.

**1.4b — auto-derive empty.** Skip directly to the full Korean+English
menu (one turn — ask them to pick at least one item per dim they care
about; "없음" is OK for a dim).

### Step 1.5 — chunk_mix

**Skip the chunk_mix question.** Use the default
`{"recent": 120, "mid": 60, "classic": 20}` automatically. (Operator may
override per-researcher later; the plugin does not ask.)

### Step 1.6 — persist

Build the final `dim_preferences` payload (include `project_weights`
nested under it) and write everything in one `profile_confirm.py` call:

```
python3 plugin/scripts/profile_confirm.py \
  --session <sid> --init <init> \
  --snapshot-json @snap.json \
  --corrections-json @corr.json \
  --dim-preferences-json @dim.json
```

Where `dim.json` includes `project_weights`:
```json
{
  "focus":  {"F-EFC": 1.0, "F-BAY": 0.7},
  "method": {"M-PSY": 1.0},
  "stim":   {},
  "subj":   {"U-HUM": 1.0},
  "combo_bonus": [],
  "project_weights": {"slug_a": 0.7, "slug_b": 0.2, "slug_c": 0.05, "slug_d": 0.05},
  "source": "auto-then-confirmed",
  "version": 2
}
```

Use temp files under `<plugin-root>/state/_tmp/` (mkdir -p first). Confirm
`ok:true`. Say in Korean: "확인됐어요. 다음 사이클부터 더 정확한 큐가
만들어지고, 일단 지금 큐로 시작할게요."

## Stage 2 — queue walk (the main loop)

Loop until `pick_next.py` returns `done:true`:

1. Call `pick_next.py --init <init> --session <sid>` (the `--session` arg
   is **required** — it stages the issued paper on the session row so
   `record_choice.py` can verify the canonical_id was actually issued
   here, not forged). Parse the JSON.
2. If `done:true`: present a short Korean wrap-up, call
   `session_close.py --session <sid>`, and stop.
3. Otherwise, you have a `paper` object with these fields you can use:
   `doi, title, authors_json, venue, year, pub_date, is_preprint,
    abstract, lab_scope_tags, chunk, rank_in_chunk, similarity,
    dim_tags, dim_match, composite, tier`.

4. Compose the paper introduction as **three SEPARATE blocks**, in this
   exact order. Each block has rules; do not merge them.

   ### Block 1 — APA citation (verbatim, NO Korean tokens inside)

   Render the bibliographic record in **plain APA 7 format**, exactly as
   it would appear in a manuscript reference list. **Do NOT add Korean
   particles, glosses, or translations inside this block.** Do not
   rewrite the title. Do not translate the venue.

   Required shape:

   ```
   <Family>, <Initials>., <Family>, <Initials>., & <Family>, <Initials>.
   (<Year>). <Title>. *<Venue>*, *<Volume>*(<Issue>), <pages>.
   https://doi.org/<doi>
   ```

   Sources of each token:
   - Authors: `authors_json`. Render up to 6 names in `Last, F. M.` form
     joined by ", " with `, &` before the last. If > 6, list the first
     6 then `, …, Last, F. M.` (per APA 7).
   - Year: `year`. If `is_preprint == true`, append " [Preprint]" after
     the year, e.g. `(2025 [Preprint])`.
   - Title: `title` verbatim. No quotes, no italics. Sentence case is
     fine if the source uses sentence case; otherwise leave it.
   - Venue: `venue` in *italics*.
   - Volume / issue / pages: only include the parts you can extract from
     the paper record. If absent, drop the field — do NOT invent.
   - DOI: full URL `https://doi.org/<doi>`. If `doi` is null, omit the
     URL line entirely (do NOT substitute a Google-Scholar link).

   This is the **only** place in the introduction where English-language
   bibliographic text appears unwrapped. It exists so the researcher can
   copy-paste the citation into a manuscript directly — keeping a clean
   APA record is more valuable than localizing it.

   ### Block 2 — Personalized recommendation rationale (Korean prose)

   One short paragraph (2–4 sentences) explaining **why this specific
   paper is being recommended to this specific researcher right now.**

   You MUST ground the paragraph in:
     (i)  ONE specific project of the researcher (by `projects[*].title`
          — NOT the slug code), and the specific model / method / question
          that project uses (read from `topics[*]` — each topic line is a
          one-sentence claim from the researcher's own self-archive that
          names a model or question);
     (ii) ONE specific contribution of the paper, expressed as a
          **complete grammatical sentence** that paraphrases what the
          paper does. The sentence must name the paper's model / method
          / claim. You may quote ≤ 8 words from the abstract verbatim,
          but only embedded INSIDE a complete sentence — never as a
          standalone snippet;
    (iii) the **connection clause** — one explicit sentence saying how
          (ii) extends, contradicts, complements, or shares tooling with
          (i). Examples of valid connection forms: "같은 prior-shape →
          posterior-variability 매핑 문제를 다른 자극 도메인에서 검증",
          "<연구원님 model> 의 cost-function 형태 비교에 직접 활용 가능한
          rate-distortion 결과", "본인이 분리하려는 sensory adaptation 과
          history effect 의 transfer 를 동일 framework 에서 dissociate".

   **BANNED patterns** (these add no value and waste the researcher's
   time — reject them in your own output before sending):
   - Pure dim-tag matching: "<init> 연구원님이 자주 다루시는 *behavioural
     modeling* 와 겹칩니다" — abstract; says nothing about WHICH
     behavioural model in WHICH project.
   - Pure phrase quote: '초록 인용: "drift diffusion modeling (DDM)
     framework"' — fragment; conveys no claim, no method choice, no
     connection.
   - Tier-template prose without the project × model anchor: "주제는
     인접하지만 방법론은 다른 결입니다." — vague.
   - Generic taxonomy labels as the connection ("Bayesian observer 영역",
     "psychophysics 영역") without naming the researcher's project AND
     the paper's specific contribution.

   **Required shape (template — fill the bracketed slots, never leave them
   abstract):**

   > `<init>` 연구원님의 **<project title in full — copied from profile.projects[*].title>**
   > 프로젝트에서 `<one specific question / model / variable from that project's topics[*] line>` 를
   > 다루시는데, 본 논문은 `<one complete sentence paraphrasing what THIS paper actually does;
   > may embed ≤ 8 verbatim words from abstract inside the sentence>`.
   > `<one explicit connection sentence — extension / contradiction /
   > shared tool / dissociation>`.

   If the queue row has `dim_match.top_signals[0].phrase` present (P19a),
   the phrase tells you which fingerprint term scored this paper highly —
   use it as the seed for matching to a project, then build the
   project × model rationale on top. The pre-computed `render_ko` field is
   a **hint, not a final clause** — always rewrite it into the
   project × model shape above.

   #### P21 — Use the synopsis (when present) as the primary source for slot (ii)

   The queue row may carry a non-null `paper.synopsis` object (added 2026-05-28
   from `archive_paper_synopses`, populated for ~1083 in-scope papers in the
   classics corpus). It contains paper-derived structured content that you
   should treat as **the primary source for slot (ii)** — the "what THIS paper
   actually does" sentence — instead of mining the raw abstract.

   Authoritative fields on `paper.synopsis`:
   - `core_question` — one-sentence research question (good seed for slot (ii))
   - `key_findings[]` — ≤4 complete-sentence empirical findings (each is a
     ready-made grammatical sentence; you may use one verbatim if it fits the
     researcher's project, or paraphrase into Korean)
   - `interpretations[]` — ≤2 author claims about meaning (good for the
     connection clause when the paper's interpretation aligns or contradicts
     the researcher's model)
   - `frameworks[].name` + `frameworks[].role` — the framework(s) this paper
     engages, with role in `{primary_lens, alternative_lens, compared_against,
     extended, context}`. Use this to phrase the connection clause precisely
     (e.g. "본 논문은 efficient-coding 를 `compared_against` 위치에서 다루는데,
     연구원님 프로젝트는 같은 framework 를 `primary_lens` 로 쓰십니다 → 같은
     phenomenon 에 대한 framework-level dissociation 가능").
   - `connecting_signals[]` — short noun phrases someone else would search
     for; cross-reference these with `topics[*]` from the researcher's profile
     to ground slot (iii).
   - `abstract_coverage` — fraction of `key_findings + interpretations`
     paraphrasable from the abstract (≥ 0.7 = trustworthy synopsis; < 0.5 =
     synopsis was extracted from a thin abstract, prefer reading the original
     abstract directly).

   Slot mapping with synopsis present:
   - **(ii) what THIS paper does** → paraphrase `synopsis.core_question` +
     ONE specific element from `synopsis.key_findings[]` (or
     `synopsis.manipulations[]` for design-focused papers). Format as one
     complete Korean sentence. You may embed ≤ 8 verbatim English words from
     `key_findings[]` inside the sentence — never as a standalone snippet.
   - **(iii) connection clause** → look for overlap between
     `synopsis.connecting_signals[]` / `synopsis.frameworks[*].name` and the
     researcher's `topics[*]`. The strongest connections are framework-role
     mismatches (extended ↔ primary_lens, compared_against ↔ primary_lens)
     because they imply a concrete next analysis the researcher could run.

   When `paper.synopsis` is null (paper not yet synopsized), fall back to
   reading `paper.abstract` as before — the slot rules above are unchanged.

   When `paper.synopsis.review_status == 'human_approved'`, the content was
   curated by a human and you may rely on it more heavily than the
   `auto_unreviewed` default.

   ### Block 3 — Uncertainty branch (only when grounding is impossible)

   You may reach this block ONLY if you genuinely cannot fill Block 2's
   project × model slots from the available data — i.e. one of:
     - The researcher has < 1 confirmed project of clearly matching scope
       (after consulting `profile.projects[*].title` and `topics[*]`).
     - The paper's `abstract` is null/empty AND the title alone does not
       name a method.
     - `dim_match.top_signals` is empty (or only contains generic dim
       labels like "behavioural modeling") AND the project list does not
       provide enough specificity to bridge the gap.

   In that case, do NOT emit a generic Block 2. Instead:
     (a) Emit Block 1 (APA citation) as usual.
     (b) Skip Block 2 entirely.
     (c) Emit ONE targeted Korean follow-up question that, once answered,
         will let the next paper introduction (and the in-session re-rank)
         be more specific. The question must name a concrete project of
         the researcher's by title and ask ONE clarifying detail about
         what model / variable / cost-function / paradigm they actually
         use in that project. Examples (use these as patterns, not
         verbatim):
         - "**GranRDT** 프로젝트에서 rate-distortion fit 시 1차 후보 cost
           function 은 Power / ExpSat / Weibull 중 어떤 것이신가요?
           paper × 본인 model 매칭 정확도를 한 줄로 끌어올릴 수 있어요."
         - "**Time2Dist** 의 absolute → relative transfer 를 보실 때
           Bayesian observer parameter 중 어떤 것을 free 로 두시나요?
           (likelihood width / prior shape / lapse rate / 그 외)."
     (d) After the researcher replies (free-text, ≤ 1 sentence), persist
         their answer to context memory by calling
         `record_choice.py --init … --session … --canonical-id <cid> \
          --choice skipped \
          --detail-json @<file containing {"clarification_q":"…","clarification_a":"…","project":"<slug>"}>`
         The `skipped` choice records that THIS paper was a learning
         signal rather than an interview answer, while the clarification
         persists so the next paper's Block 2 can reference it. Then move
         to the next `pick_next.py`.

5. After Block 1 + Block 2 (or Block 1 + Block 3), present the MCQ block,
   verbatim:

   ```
   1) 나중에 읽을 리스트에 추가
   2) 내 연구와 관련 없음
   3) 이미 읽었음
   ```

   (If Block 3 was emitted instead of Block 2, the MCQ is replaced by
   the targeted clarification question — no MCQ for that paper.)

5. Wait for the researcher's reply. Accept all of these as equivalent
   choice signals (normalize to `1`/`2`/`3`):
   - bare digit: `1`, `2`, `3`
   - punctuated: `1)`, `1.`, `(1)`, `1번`, `1 번`
   - Korean numerals: `하나`, `둘`, `셋`, `첫번째`, `두번째`, `세번째`
   - full option text (or first 3+ chars of it): `나중에 읽을`, `관련 없음`,
     `이미 읽었음`
   - common English equivalents: `save`, `not relevant`, `read`
   - whitespace-padded variants of any of the above
   If the reply contains *no* identifiable choice signal, say in Korean
   "1, 2, 3 중 하나로만 답해주세요." and wait — **do not invent a choice**.

6. Map the answer to `choice`:
   - 1 → `save_later`
   - 2 → `not_relevant`   (ask one follow-up: "왜 관련이 없다고 보시나요?
     한 문장으로만 답해주세요.")
   - 3 → `already_read`   (ask one follow-up: "이 논문을 어떤 맥락에서
     활용하셨나요? 한 문장.")

7. Build `detail_json` from the follow-up reply (or `{}` if none) and
   call `record_choice.py --init … --session … --canonical-id <cid>
   --choice <c> --detail-json @detail.json`. Confirm
   `papers_seen` increased by 1 and `meta_review_due` flag.

8. If `meta_review_due == true`: run Stage 4 before showing the next
   paper.

## Stage 3 — (removed 2026-05-28)

The previous Stage 3 ("tell me more" deep-dive via the `paper-explainer`
agent) was removed when the MCQ collapsed from 4 options to 3. The
deep-dive made the interview turn longer without producing a usable
labeling signal — researchers who wanted more context were better
served by reading the synopsis-grounded Block 2 itself (P21). If the
researcher is genuinely unsure after Block 2, the Block 3 uncertainty
branch (a single targeted clarification question, persisted as
`choice='skipped' + detail_json`) is the supported path.

## Stage 4 — meta-review (every 10 answers; **active belief update**)

This is NOT a rubric pass. Every 10 MCQs you (Opus) actively **reason
about what the researcher just signalled**, update an explicit model of
their preferences, write it back to the DB, and tell the researcher in
plain Korean what changed. The rubric branches below are *checks* you
also do — but the primary work is your own inference from the data.

### 4.1 — gather

1. Call `meta_review.py --init <init> --session <sid>` (no proposal yet).
   The script writes a placeholder row at this N and returns a JSON
   snapshot with: `breakdown`, `chunk_breakdown`, `tier_breakdown`,
   `topic_freq`, `dim_freq`, `recent` (latest 10 responses with title,
   year, chunk, tier, choice, and the researcher's own `choice_detail`
   text where they said *why*).

### 4.2 — delegate to the belief-updater sub-agent (context-isolated)

Spawn the `belief-updater` agent in its own context window. Try
`subagent_type: "belief-updater"` first; if Claude Code refuses with
"unknown subagent", fall back to the plugin-namespaced form
`subagent_type: "csnl-paper-archive-interview:belief-updater"`. Pass
it, as a single JSON blob in the prompt:

```json
{
  "init": "<INIT>",
  "current_prefs": <the verified dim_preferences from profile_show / latest write>,
  "meta_review": <the JSON returned by step 4.1>
}
```

The agent applies the deterministic rubric (see
`plugin/agents/belief-updater.md` for the full spec — boost reinforced
tags by +0.2 capped at 1.0; downweight rejected tags by −0.3 floored
at 0.1; require an explicit Korean negation token in `choice_detail.reason`
before downweighting). It returns ONE JSON object containing
`new_prefs`, `deltas_applied`, `rubric_signals`, `evidence_summary_ko`,
`n_window`, `applied_at`.

If the agent's output is malformed (not parseable JSON, missing
`new_prefs`), fall back to "no-op": skip 4.3+4.4 writes, tell the
researcher in Korean "이번 묶음은 패턴이 약해서 추천 기준은 그대로 두고
다음 묶음으로 넘어갈게요." and continue the queue walk.

### 4.3 — review the agent's output (cheap sanity check, in main thread)

Parse the agent's JSON. Reject and fall back to no-op if:
- `new_prefs` is missing or not a dict.
- Any weight is outside [0.0, 1.0].
- A dim code appears in `new_prefs` that isn't in the taxonomy (the
  agent must use only codes from `dim_freq`).
- `applied_at` is missing.

Otherwise prepare the `proposal` payload for meta_review and the
`new_prefs` for profile_confirm. The agent's `evidence_summary_ko` is
your raw material for step 4.5.

### 4.4 — write back

Two write operations, both idempotent:

1. **Stamp the meta_review row with the proposal:**
   ```
   python3 plugin/scripts/meta_review.py --init <init> --session <sid> \
     --proposal-json @proposal.json --apply
   ```
   (UPSERT on the same `(session, at_response_count)` row from 4.1.)

2. **Update the researcher's dim_preferences** (only if any delta is
   non-zero):
   ```
   python3 plugin/scripts/profile_confirm.py --session <sid> --init <init> \
     --snapshot-json @snap.json --dim-preferences-json @updated.json
   ```
   `updated.json` carries the post-delta `dim_preferences` (with the same
   `project_weights` carried over). The next operator-run queue rebuild
   will pick this up; the current session continues with the existing
   queue.

### 4.5 — explain to the researcher (Korean, ≤ 2 short sentences)

State what the system just learned and what changes next. Use English
technical terms inline; Korean only for the framing. **The belief
takes effect on the VERY NEXT paper this session** — `pick_next.py`
re-orders the unanswered queue against the freshly-written prefs
(P17 in-session re-rank); no operator rebuild is required for the
within-session effect. The operator's next full queue rebuild then
also persists the new ranking into archive_researcher_queues.

Examples:
> "최근 10편을 보고, <init> 연구원님이 efficient coding 쪽 논문은 거의 다 저장
> 하시는데 pupillometry 쪽은 잘 안 보신다는 걸 알았어요. 바로 다음 추천부터
> efficient coding 비중을 올리고 pupillometry는 줄여서 보여드릴게요."
> 
> "최근 10편 중 7편이 이미 읽으셨던 거라, 다음 묶음부터는 더 최근(또는
> 더 오래된) 시기로 넘어갈게요."

Do NOT enumerate every category code. Two sentences max.

### 4.6 — continue

Call `pick_next.py --init <init> --session <sid>` and resume the queue
walk. The N counter keeps incrementing; the next meta-review fires at
the next multiple of 10.

## Stage 5 — wrap-up

When `pick_next.py` reports `done:true`:
1. Compute the final counts from the last `record_choice.py` response
   (`papers_seen` + `breakdown`).
2. Print a short Korean wrap-up:
   - 총 N개의 논문을 살펴봤어요. 저장 X, 이미 읽음 Y, 관련 없음 Z.
   - 이번 결과는 다음 추천 사이클에 반영되어 더 정확한 우선순위 큐가
     만들어질 거예요.
3. Call `session_close.py --session <sid>`. Confirm `ok:true`.
4. Stop. Do not solicit more input.

## Resume semantics

If the researcher comes back later and runs
`/csnl-paper-archive-interview:paper-interview <init>`
again:
- `profile_show.py` reuses the most recent open session (no second row).
- `pick_next.py` automatically skips canonical_ids that already have a
  response row.
- The walk resumes at the same chunk + rank.

## Error handling

- DB unreachable → print the script's stderr verbatim (it has the
  setup hint), tell the researcher in Korean to contact the operator,
  stop.
- Researcher types `/exit` or `종료` → call `session_close.py`, then
  stop. Do **not** auto-close on unrelated chit-chat — the researcher
  may pause mid-conversation.

## Things you must not do

- Do not silently move on after a `not_relevant` — always capture the
  one-sentence reason.
- Do not collapse the MCQ to fewer than 3 options. (As of 2026-05-28 the
  4th option "더 자세히 소개해줘" was retired; the MCQ is exactly 3
  options: 나중에 읽을 / 관련 없음 / 이미 읽었음.)
- Do not show internal labels to the researcher: `canonical_id`,
  similarity score, `chunk` (`recent` / `mid` / `classic`),
  `rank_in_chunk`, `lab_scope_tags` codes, `session_id`,
  `proposal_type`, `tighten_chunk` / `advance_chunk` / `keep` labels,
  `applied`, JSON field names. Use the Tag rendering table and natural
  Korean dates instead. (Inside the APA citation block, `DOI` appears in
  its standard `https://doi.org/…` form — that's the one carved-out
  exception, see §Boundaries.)
- Do not paste raw script stdout into the chat. Always `json.loads` and
  rewrite into the right form (APA in Block 1, Korean elsewhere).
- **Do not wrap the APA citation in Korean.** The Stage 2 Block 1
  citation is rendered verbatim. "Lee, H., …, & Lim, J. (2025). Title.
  *Journal of Vision*, *25*(3), 1–15. https://doi.org/…" — no Korean
  particles, no glosses, no translated venue, no reformatted title.
- **Do not emit Stage 2 Block 2 in the banned abstract forms.** No pure
  dim-tag matches ("behavioural modeling 와 겹칩니다"). No standalone
  abstract phrase quotes ("초록 인용: \"drift diffusion modeling (DDM)
  framework\""). Block 2 MUST name a specific project of the researcher
  by title AND a specific model/method used in that project, AND
  paraphrase what the paper does in a complete sentence, AND state an
  explicit connection. If you cannot do all three, you are in the
  uncertainty case — emit Block 3 (a targeted clarification question +
  record_choice as `skipped`) instead.
- Do not call any pipeline script outside `<plugin-root>/scripts/`. The
  parent harness scripts (`scripts/`, `pipeline/`) are operator-only.
