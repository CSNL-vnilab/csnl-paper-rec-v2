---
description: Operating procedure for the CSNL paper-archive interview. Drives the one-paper-at-a-time MCQ flow over a researcher's pre-computed queue (recent / mid / classic), with profile verification + dimension preference confirmation at the start, a deterministic 4-option MCQ per paper, an isolated explainer sub-agent for option (4), and a meta-review every 10 answers. Use whenever the paper-interview slash command is invoked, or the researcher asks to "resume the paper interview", "더 보여줘", "이어서 진행해줘".
---

# paper-archive-interview — researcher procedure

## Boundaries (read once, apply throughout)

- The researcher sees **only Korean**. Never paste raw JSON, never echo
  a script's stdout into the chat. After every Bash call: `json.loads`
  it, extract the fields you need, and rewrite them in Korean prose.
- **Internal vocabulary forbidden** in researcher-visible text. The
  following must NEVER appear in a message to the researcher (in any
  language): `canonical_id`, `DOI`, `doi`, `similarity`, `rank_in_chunk`,
  `chunk`, `recent` / `mid` / `classic` (as chunk labels — say `최근 5년`
  / `5-10년 전` / `10년 이상 전` instead), `lab_scope_tags`, raw tag
  codes (`BDM`, `NN`, `fVC`, `VWM`, `SD`, `CG`, `METH`), `proposal_type`
  labels (`tighten_chunk` / `advance_chunk` / `keep`), `session_id`,
  `paper-explainer`, `paper-archive-interview`, "agent", "scout",
  "tier", "D1"–"D5", "composite", "BANNED_TERMS", project slugs in
  raw form, JSON field names. Translate every tag into a plain-Korean
  topic phrase using the table at §"Tag rendering" below.
- No signatures, no farewells. End each message with the question or the
  MCQ block, nothing else.
- Length per message: ≤ 4 short paragraphs OR the MCQ; whichever is
  smaller. If the explainer agent (option 4) returns a longer reply,
  preserve its verbatim quotes (text inside `"…"`) and trim only the
  surrounding prose — never paraphrase a quote.
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
cat <plugin-root>/../state/archive/taxonomy.json | jq -r \
  '.dimensions | to_entries[] | .key as $d | .value | to_entries[] | "\($d) \(.key) \(.value.label_ko)"'
```

(The path resolves to the parent harness repo when the plugin is
installed in-tree. When installed standalone, read
`$CSNL_PIPELINE_DIR/../state/archive/taxonomy.json` if `CSNL_PIPELINE_DIR`
is set, otherwise fall back to the lab-bucket table below for the
7-bucket top layer only.)

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

Run scripts with `python <plugin-root>/scripts/<name>.py …`. The plugin
root is the directory containing `.claude-plugin/plugin.json`. If
`$CSNL_PLUGIN_DIR` is set, prefer that; otherwise look up two levels
from this SKILL.md.

**Tempfile dir.** When the skill needs to pass JSON via `@file`, write
the temp file under `<plugin-root>/state/_tmp/` (mkdir -p first; the
plugin does not ship this directory).

## Stage 0 — preflight + orientation (run first; fail fast)

1. Call `preflight.py <init>`. Parse the JSON.
2. If `ok: false`: print the script's `message_ko` to the researcher
   verbatim (no extra prose, no signature), then stop.
3. If `ok: true`: print a structured Korean orientation (every session
   — researchers shouldn't have to remember what the plugin is for).
   Use the queue counts from `preflight.queue_by_chunk` to fill `<N>`,
   `<R>`, `<M>`, `<C>` below:

   ```
   안녕하세요, <init> 박사님. CSNL paper-archive 인터뷰입니다.

   [이미 준비된 것]
   • csnl_research (read-only): 박사님 프로젝트 메타데이터
   • csnl_paper_rec.archive_* (이 인터뷰의 작업 영역):
     - archive_papers ~8,680편 (classics + 7년 CWLL 추천로그 + PI-network 출간물,
       dedup·filter·BAAI/bge-m3 임베딩 완료)
     - 박사님 추천 큐 <N>편 (recent <R> / mid <M> / classic <C>)
     - 4-차원 sub-tag (focus / method / stim / subj) + S/A/B/C tier

   [오늘 인터뷰에서 업데이트할 것]
   1. 박사님 주제 + 방법론 요약 확인 (잘못된 / 빠진 것 알려주세요)
   2. (활성 프로젝트 여러 개면) 프로젝트 비중 (예: 70/20/5/5)
   3. 차원 선호 — 어떤 focus/method/stim/subj 가 우선인지
   4. 각 추천 논문 4-지선다 (저장 / 관련없음 / 이미읽음 / 더자세히)
   5. 10편마다 박사님 응답 패턴을 보고 차원 선호 자동 업데이트 (다음 큐에 반영)

   [경계]
   인터뷰 응답은 csnl_paper_rec.archive_{interview_sessions, profile_verifications,
   responses, meta_reviews} 4개 테이블에만 저장됩니다. 다른 어떤 데이터도
   수정되지 않고, 슬랙·이메일 전송 경로도 없어요.

   3가지 짧은 확인부터 시작할게요.
   ```

   Then proceed to Stage 1.

## Stage 1 — profile verification (smaller turns — one question at a time)

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

> "박사님이 현재 주목 중인 주제로 다음을 정리했어요:
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
> "박사님 프로젝트 텍스트에서 다음 키워드가 자주 등장해요:
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
python plugin/scripts/profile_confirm.py \
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
    abstract, lab_scope_tags, chunk, rank_in_chunk, similarity`.
4. Compose a **one-paragraph Korean summary** with exactly these elements
   in order:
   - 저널 + 연도 (preprint이면 "프리프린트")
   - 1–3 명의 대표 저자 + 그 외 인원수 (`외 N인`)
   - 키워드 2–4개 — render `lab_scope_tags` + `dim_tags` via the Tag
     rendering table above (never use the raw codes `BDM`, `F-EFC`,
     `M-RSA`, `S-FAC`, etc.). Pull the Korean labels from the taxonomy.
   - 한 문장으로 연관성 설명. **Use the paper's `tier` field to colour
     the sentence** (never mention "tier" or `S/A/B/C` aloud):
     - `tier == "S"`: full match — "박사님의 [방법론 한글라벨] × [자극
       한글라벨] 조합과 정확히 맞물려요" (use the matched Korean labels
       from `dim_match.matched` via taxonomy.json).
     - `tier == "A"`: strong partial — "박사님이 자주 다루시는
       [matched-dim 한글라벨] 와 겹쳐요."
     - `tier == "B"`: topical-only — describe the topical connection via
       project hypothesis/variables, no specific dim claim.
     - `tier == "C"`: humble framing — "주제는 인접하지만 방법론은 다른
       결입니다. 한번 훑어보실 만한지 봐주세요."
   Then immediately present the MCQ block, verbatim:

   ```
   1) 나중에 읽을 리스트에 추가
   2) 내 연구와 관련 없음
   3) 이미 읽었음
   4) 더 자세히 소개해줘
   ```

5. Wait for the researcher's reply. Accept all of these as equivalent
   choice signals (normalize to `1`/`2`/`3`/`4`):
   - bare digit: `1`, `2`, `3`, `4`
   - punctuated: `1)`, `1.`, `(1)`, `1번`, `1 번`
   - Korean numerals: `하나`, `둘`, `셋`, `넷`, `첫번째`, `두번째`, …
   - full option text (or first 3+ chars of it): `나중에 읽을`, `관련 없음`,
     `이미 읽었음`, `더 자세히`
   - common English equivalents: `save`, `not relevant`, `read`, `more`
   - whitespace-padded variants of any of the above
   If the reply contains *no* identifiable choice signal, say in Korean
   "1, 2, 3, 4 중 하나로만 답해주세요." and wait — **do not invent a choice**.

6. Map the answer to `choice`:
   - 1 → `save_later`
   - 2 → `not_relevant`   (ask one follow-up: "왜 관련이 없다고 보시나요?
     한 문장으로만 답해주세요.")
   - 3 → `already_read`   (ask one follow-up: "이 논문을 어떤 맥락에서
     활용하셨나요? 한 문장.")
   - 4 → `tell_me_more`   (see Stage 3; the MCQ is re-presented after
     the explainer returns).

7. Build `detail_json` from the follow-up reply (or `{}` if none) and
   call `record_choice.py --init … --session … --canonical-id <cid>
   --choice <c> --detail-json @detail.json`. Confirm
   `papers_seen` increased by 1 and `meta_review_due` flag.

8. If `meta_review_due == true`: run Stage 4 before showing the next
   paper.

## Stage 3 — option (4) "tell me more"

You must spawn the **`paper-explainer`** agent. Try
`subagent_type: "paper-explainer"` first; if Claude Code refuses with
"unknown subagent", fall back to the plugin-namespaced form
`subagent_type: "csnl-paper-archive-interview:paper-explainer"`. Pass it:
  - the paper JSON object you got from `pick_next.py`
  - the researcher's confirmed profile (snapshot + corrections)
  - one sentence asking it to explain the link in ≤ 3 short Korean
    paragraphs and to flag any risk of irrelevance.

The explainer agent runs in **its own context window** so the main
interview thread stays small. When it returns:
- The explainer is already capped at ≤ 3 short Korean paragraphs (~280
  chars each). If its output fits within ≤ 4 paragraphs, **pass it
  through verbatim** — do not re-summarise.
- If it overflows, trim *only* surrounding connectives. **Preserve every
  span that appears inside `"…"` quotes verbatim** (the explainer is
  required to ground itself in quoted text per `rules/03_grounding.md`;
  paraphrasing the quote breaks the trace).
- Then re-present the same MCQ. Do **not** auto-pick a choice on the
  researcher's behalf.

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

### 4.2 — reason (Opus, in your context)

Read the 10 recent responses *one by one*. For each:
- `save_later` → which dimension tags did the paper carry? Mark those
  as "evidence for". If the paper hit a combo, that's strong evidence.
- `not_relevant` + the researcher's `choice_detail.reason` Korean text
  → identify what the researcher said was wrong. Map that to dim tags
  the paper carried (those become "evidence against") AND/OR to a
  *missing* dim the paper lacked (those become "evidence for: I want
  more of dim X").
- `already_read` → that area is already covered for the researcher;
  the queue should de-prioritize close siblings of this paper but NOT
  remove the dim entirely (the researcher reads in that area).
- `tell_me_more` (option 4) → strong signal of intrigue without
  commitment; treat as half-strength `save_later`.
- `skipped` → no information.

Then compute deltas to apply to `dim_preferences`:
- **boost (+0.2, clamped to 1.0)** any dim tag that appeared in ≥3 of
  the `save_later` / `tell_me_more` papers but is currently at weight
  ≤ 0.5 in the researcher's profile.
- **downweight (−0.3, clamped to 0.1 minimum so we never zero out
  legitimate interest)** any dim tag that appeared in ≥3 of the
  `not_relevant` papers AND the researcher's reason text explicitly
  rejected that area (look for negation, "관련 없", "내 연구 아님",
  "다른 결").
- **leave alone** any tag with mixed signals or low evidence.

Also re-check the rubric checks for completeness:
- `not_relevant >= 6/10` → also propose `tighten_chunk` for the band
  that produced the most rejects.
- `already_read >= 6/10` → also propose `advance_chunk`.
- These can co-exist with the dim-level deltas.

### 4.3 — assemble the proposal payload

Build a single `proposal` JSON the meta_review row will store, with a
shape like:
```json
{
  "proposal_type": "belief_update",
  "deltas_applied": {
    "focus":  {"F-EFC": "+0.2 → 1.0"},
    "method": {"M-PSY": "−0.3 → 0.4"}
  },
  "rubric_signals": {
    "tighten_chunk": "recent",
    "advance_chunk": false
  },
  "evidence_summary": "save_later 4편 모두 efficient coding 매칭; not_relevant 3편이 동공측정 위주",
  "n_window": 10
}
```

Compute the new `dim_preferences` dict by applying the deltas to the
current (verified) profile prefs.

### 4.4 — write back

Two write operations, both idempotent:

1. **Stamp the meta_review row with the proposal:**
   ```
   python plugin/scripts/meta_review.py --init <init> --session <sid> \
     --proposal-json @proposal.json --apply
   ```
   (UPSERT on the same `(session, at_response_count)` row from 4.1.)

2. **Update the researcher's dim_preferences** (only if any delta is
   non-zero):
   ```
   python plugin/scripts/profile_confirm.py --session <sid> --init <init> \
     --snapshot-json @snap.json --dim-preferences-json @updated.json
   ```
   `updated.json` carries the post-delta `dim_preferences` (with the same
   `project_weights` carried over). The next operator-run queue rebuild
   will pick this up; the current session continues with the existing
   queue.

### 4.5 — explain to the researcher (Korean, ≤ 2 short sentences)

State what the system just learned and what changes next. Use English
technical terms inline; Korean only for the framing. Examples:
> "최근 10편을 보고, 박사님이 efficient coding 쪽 논문은 거의 다 저장
> 하시는데 pupillometry 쪽은 잘 안 보신다는 걸 알았어요. 다음 사이클부터
> efficient coding 비중을 조금 올리고 pupillometry는 줄일게요."
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
- Do not collapse the MCQ to fewer than 4 options.
- Do not show internal labels to the researcher: `canonical_id`, `DOI`,
  similarity score, `chunk` (`recent` / `mid` / `classic`),
  `rank_in_chunk`, `lab_scope_tags` codes, `session_id`,
  `proposal_type`, `tighten_chunk` / `advance_chunk` / `keep` labels,
  `applied`, JSON field names. Use the Tag rendering table and natural
  Korean dates instead.
- Do not paste raw script stdout into the chat. Always `json.loads` and
  rewrite in Korean prose.
- Do not call any pipeline script outside `<plugin-root>/scripts/`. The
  parent harness scripts (`scripts/`, `pipeline/`) are operator-only.
