---
name: paper-archive-interview
description: >
  Operating procedure for the CSNL paper-archive interview. Drives the
  one-paper-at-a-time MCQ flow over a researcher's pre-computed queue
  (recent ≤5y / mid 5–10y / classic >10y), with a profile verification
  stage at the start, a deterministic 4-option MCQ per paper, an isolated
  explainer sub-agent for option (4), and a deterministic meta-review every
  10 answers. Use whenever `/paper-interview <init>` is invoked, or the
  researcher asks to "resume the paper interview", "더 보여줘", "이어서
  진행해줘".
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

## Tag rendering (English-internal → Korean researcher-facing)

When you have `lab_scope_tags` like `["BDM","NN"]` from `pick_next.py`,
render the keywords for the researcher in Korean. Pick 2–4 short phrases:

| Tag | Render as (Korean) |
| --- | --- |
| BDM  | 의사결정 / 베이지안 추론 |
| NN   | 신경망 동역학 / 생물학적 신경망 |
| fVC  | fMRI 시각피질 |
| VWM  | 시각작업기억 |
| SD   | 시계열 의존성(serial dependence) |
| CG   | 범주학습 / 일반화 |
| METH | 방법론 (모델 비교 등) |

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

## Stage 0 — preflight (run first; fail fast)

1. Call `preflight.py <init>`. Parse the JSON.
2. If `ok: false`: print the script's `message_ko` to the researcher
   verbatim (no extra prose, no signature), then stop. Do not call any
   other script.
3. If `ok: true`: show a one-line Korean greeting (no signature)
   mentioning the queue size — e.g. "안녕하세요. 추천 큐 N편 준비됐어요.
   인터뷰를 시작할게요." — then proceed to Stage 1.

## Stage 1 — profile verification (run once per session)

1. Call `profile_show.py <init>`. Parse the JSON.
2. If `error == "no_active_projects"`: tell the researcher in Korean
   that the lab DB has no active project rows above the confidence
   threshold, ask them to update their project metadata via the CSNL
   self-archive tool first, and stop.
3. Otherwise: present a compact summary in Korean. One short paragraph
   for each of:
   - 현재 주목 중인 주제 (`topics`)
   - 다루는 방법론 (`methods`)
   - 자주 참조한 저자 (`authors`)
   - 진행 중인 프로젝트들 (`projects[].title` + phase)
   Then ask: "위 정리가 정확한가요? 잘못된 항목이나 빠진 항목을 알려주세요.
   (없다면 '맞아요'라고 답해주세요.)"
4. Capture the researcher's reply.
   - If they say "맞아요" / "좋아요" / "정확해요" / etc. — `corrections = {}`.
   - If they correct something — store as
     `{topics_remove:[...], topics_add:[...], methods_remove:[...], ...}`.
5. Call `profile_confirm.py --session <sid> --init <init>
   --snapshot-json @snap.json --corrections-json @corr.json`. Use a temp
   file under `<plugin-root>/state/_tmp/` (mkdir -p first) for the JSON
   payloads to avoid quoting issues. Confirm the script printed `ok:true`.

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
   - 키워드 2–4개 — render `lab_scope_tags` via the Tag rendering table
     above (never use the raw codes `BDM`, `NN`, etc.) and add salient
     nouns from the title.
   - 한 문장으로 연관성 설명: 어느 프로젝트의 어떤 요소(가설/방법/변수)와
     맞닿는지. 데이터 안에 명시된 부분만 인용 — 추측 금지.
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

You must spawn the **`paper-explainer`** agent (subagent_type ==
"paper-explainer"). Pass it:
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

## Stage 4 — meta-review (every 10 answers)

1. Call `meta_review.py --init … --session …` *without* `--proposal-json`.
   The script writes a `keep`-shaped placeholder row keyed on
   `(session_id, at_response_count)` — a second call with a confirmed
   proposal at the same N updates the same row (no duplicates).
2. Parse the JSON. Build a deterministic proposal payload from the
   breakdown — these are *internal* labels, never spoken aloud:
   - if `not_relevant >= 6 of last 10` → proposal_type = `tighten_chunk`,
     `target_chunk` = the age band ("recent" / "mid" / "classic") that
     produced the most `not_relevant` papers.
   - else if `already_read >= 6 of last 10` → proposal_type =
     `advance_chunk` (move to the next age band earlier).
   - else if `save_later + tell_me_more >= 7` → proposal_type = `keep`,
     `topn_delta` = +2.
   - else → proposal_type = `keep`, `topn_delta` = 0.
3. Present a short Korean update to the researcher — translate the
   internal proposal_type into a plain Korean sentence. Do NOT mention
   the label, the band code, or any internal field name. Examples:
   - keep+delta:  "지금까지 N개의 논문을 봤어요. 저장 X, 이미 읽음 Y,
                  관련 없음 Z. 추천이 잘 맞는 것 같네요 — 다음 묶음에서
                  몇 편 더 보여드릴게요."
   - tighten:     "최근 본 논문 10편 중 절반 이상이 관련이 없다고
                  하셨어요. 이 시기 (최근 5년 / 5-10년 전 / 10년 이상 전
                  — 해당하는 시기로 풀어서 말하세요) 의 추천 기준을 조금
                  더 엄격하게 적용해도 될까요?"
   - advance:     "최근 본 논문 다수를 이미 읽으셨네요. 다음 시기로
                  바로 넘어갈까요?"
4. Wait for the researcher's confirmation.
   - "네/좋아요/그렇게 해주세요" → re-call `meta_review.py …
     --proposal-json @p.json --apply` with the proposal stamped; the
     script flips `applied=TRUE` on the same row.
   - "아니요/괜찮아요/그대로 두세요" → re-call `meta_review.py …
     --proposal-json '{"proposal_type":"keep","topn_delta":0}' --apply`.
5. Continue the queue walk.

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

If the researcher comes back later and runs `/paper-interview <init>`
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
