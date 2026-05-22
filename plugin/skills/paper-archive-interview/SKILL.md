---
description: Operating procedure for the CSNL paper-archive interview. Drives the one-paper-at-a-time MCQ flow over a researcher's pre-computed queue (recent / mid / classic), with profile verification + dimension preference confirmation at the start, a deterministic 4-option MCQ per paper, an isolated explainer sub-agent for option (4), and a meta-review every 10 answers. Use whenever the paper-interview slash command is invoked, or the researcher asks to "resume the paper interview", "더 보여줘", "이어서 진행해줘".
---

# paper-archive-interview — researcher procedure

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
   - Stage 3 option 4 deep-dive → `paper-explainer` agent
   - Stage 4 belief-update computation → `belief-updater` agent
   The main interview thread only persists their outputs via the DB
   scripts. This keeps the main thread small and reproducible across
   long sessions.

5. **Determinism.** All scoring + ranking + tagging is rule-based in
   the operator-side scripts (no LLM in the unattended path). The
   assistant's job is rendering, not re-ranking. Never reorder, skip,
   or surface a paper outside what `pick_next.py` issues.

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

Run scripts with `python <plugin-root>/scripts/<name>.py …`. The plugin
root is the directory containing `.claude-plugin/plugin.json`. If
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
   4. 각 추천 논문 4-지선다 (저장 / 관련없음 / 이미읽음 / 더자세히)
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
is non-null, Stage 1 is COMPLETE for this session. Print one short Korean
line — "이전에 확인하신 프로필로 이어서 진행할게요." — and proceed
directly to Stage 2. Do NOT re-ask the topic/method/dim questions; the
researcher will see the same questions twice in a session as a bug.



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
   - 한 문장으로 연관성 설명. **STRICT GROUNDING RULE (P16):**
     - You may claim a connection ONLY if it can be supported by:
       (a) a verbatim ≤ 12-word quote — **prefer the abstract; if the
           abstract is null/empty, fall back to the title** (the
           classics archive often has only a filename-derived title),
           AND
       (b) a SPECIFIC field of the researcher's confirmed profile (one
           of: topics[*], methods[*], or a dim_preferences focus/method/
           stim/subj code) that matches the paper's `dim_tags` /
           `lab_scope_tags`.
     - If EITHER (a) or (b) cannot be substantiated from the pick_next
       output alone, use this honesty fallback verbatim: "본 논문의
       초록/제목만으로는 <init> 연구원님 연구와의 직접적인 연결을
       단언하기 어렵습니다. 제목·키워드만 봐도 익숙한 영역이라면 1번,
       아니면 2번을, 더 자세한 설명이 필요하면 4번을 눌러주세요."
     - **DO NOT** infer transitive connections ("X 가 Y 일 수도 있어서
       박사님 연구와 …"). Either there is a direct verifiable link in the
       data or the fallback is the only acceptable output.
     - Use the paper's `tier` field to colour the sentence (never
       mention "tier" or `S/A/B/C` aloud). All tier templates use
       "제목/초록 인용:" — pick whichever field actually contains the
       quoted span:
     - `tier == "S"`: full match — "<init> 연구원님의 [matched method
       한글라벨] × [matched stim 한글라벨] 조합과 맞물립니다.
       제목/초록 인용: \"...\""
     - `tier == "A"`: strong partial — "<init> 연구원님이 자주 다루시는
       [matched-dim 한글라벨] 와 겹칩니다. 제목/초록 인용: \"...\""
     - `tier == "B"`: topical-only — quote the matching span from
       title or abstract; cite the topic field of the profile by name
       (NOT a code). "제목/초록 인용: \"...\""
     - `tier == "C"`: humble — "주제는 인접하지만 방법론은 다른 결입니다.
       제목/초록을 한번 훑어보실 만한지 봐주세요. 제목/초록 인용: \"...\""
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
