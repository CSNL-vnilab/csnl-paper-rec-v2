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

The researcher sees Korean phrases only. Internal codes (`BDM`, `F-BAY`,
`M-RSA`, `S-FAC`, `U-HUM`, `S/A/B/C`, `recent`/`mid`/`classic`) never
appear in messages. Load the live rendering table from the operator's
taxonomy file every time:

```
cat <plugin-root>/../state/archive/taxonomy.json | jq -r \
  '.dimensions | to_entries[] | .key as $d | .value | to_entries[] | "\($d) \(.key) \(.value.label_ko)"'
```

(The path resolves to the parent harness repo when the plugin is
installed in-tree. When installed standalone, read
`$CSNL_PIPELINE_DIR/../state/archive/taxonomy.json` if `CSNL_PIPELINE_DIR`
is set, otherwise fall back to the lab-bucket table below for the
7-bucket top layer only.)

**Top-layer lab buckets** (always available, no taxonomy needed):

| Tag | Render as (Korean) |
| --- | --- |
| BDM  | 의사결정 / 베이지안 추론 |
| NN   | 신경망 동역학 / 생물학적 신경망 |
| fVC  | fMRI 시각피질 |
| VWM  | 시각작업기억 |
| SD   | 시계열 의존성(serial dependence) |
| CG   | 범주학습 / 일반화 |
| METH | 방법론 (모델 비교 등) |

**52-cat sub-tags** (4 dimensions × focus/method/stim/subj) — for each
code returned by `pick_next.py` in `dim_match.matched` or in `dim_tags`,
read `taxonomy.json` and use the `label_ko` field as the Korean phrase.
Examples (full table in taxonomy.json):

| Code | label_ko | dim |
| --- | --- | --- |
| `F-EFC`  | 효율부호화·정보이론 | focus |
| `F-BAY`  | 베이지안 관찰자 모델 | focus |
| `M-RSA`  | 표상유사도 분석 | method |
| `M-EFC`  | 효율부호화 분석 | method |
| `S-ORI`  | 방위 (오리엔테이션) | stim |
| `S-FAC`  | 얼굴 | stim |
| `U-HUM`  | 정상 인간 피험자 | subject |
| `U-NHP`  | 비인간 영장류 | subject |

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

## Stage 0 — preflight (run first; fail fast)

1. Call `preflight.py <init>`. Parse the JSON.
2. If `ok: false`: print the script's `message_ko` to the researcher
   verbatim (no extra prose, no signature), then stop. Do not call any
   other script.
3. If `ok: true`: show a one-line Korean greeting (no signature)
   mentioning the queue size — e.g. "안녕하세요. 추천 큐 N편 준비됐어요.
   인터뷰를 시작할게요." — then proceed to Stage 1.

## Stage 1 — profile verification (run once per session)

1. Call `profile_show.py <init>`. Parse the JSON. The JSON may also carry
   `dim_preferences` (auto-derived or previously confirmed) and `chunk_mix`
   — surface those at step 4 (P14 dimension confirmation).
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

5. **(P14) Dimension preference confirmation.** Read `dim_preferences`
   from the profile_show output. Two cases:

   **5a. Empty / auto-derive yielded nothing** (`dim_preferences` is
   null OR all 4 sub-dicts empty): say in Korean —
   > "어떤 측면을 우선해 추천드릴지 알려주시면 정확도가 올라가요.
   > 다음 4가지에서 관심 있는 항목을 골라주세요 (여러 개 가능, 없으면
   > '없음'):
   >   • 연구 초점: 행동 모델 / 인간 뇌영상 / 침습 전기생리 / 신경회로
   >     모델링 / 베이지안 관찰자 / 효율부호화 / 시지각 / 기억 / 주의 /
   >     학습 / 임상 / 발달 / 이론
   >   • 방법론: 심리물리학 / 확산모델 / 베이지안 모델적합 / 효율부호화
   >     분석 / fMRI 분석 / pRF·망막순응도 / 표상유사도 분석 / 인코딩
   >     모델 / M/EEG / 동공측정 / 시선추적 / 뇌자극 / 심층신경망 모델
   >     / 대규모 전기생리 / 칼슘영상 / VR
   >   • 자극: 방위 / 운동 / 생체운동 / 대비·휘도 / 공간주파수 / 크기 /
   >     수량 / 시간간격 / 깊이 / 색채 / 얼굴 / 사물·범주 / 자연영상 /
   >     음고 / 촉각 / 가치·보상 / 추상자극 / 다감각
   >   • 실험 대상: 정상 인간 / 임상 인간 / 비인간 영장류 / 설치류 /
   >     발달·영유아 / 모델만 / 기타 종"
   Researcher picks Korean phrases. Map each phrase back to the code via
   `taxonomy.json` `label_ko` → `code`. Assign weight 1.0 to each picked
   code in its dimension; 0.0 for the rest.

   **5b. Auto-derived prefs exist** (at least one dim populated): render
   them in Korean (use `label_ko` from taxonomy.json) and present a 3-option
   chip set:
   > "박사님 프로젝트를 보니 주로 [효율부호화·정보이론 + 베이지안 관찰자]
   > 쪽으로 추천드리면 잘 맞을 것 같아요. 어떻게 할까요?
   >   (a) 좋아요, 이대로 진행
   >   (b) 거의 맞는데 한두 가지 빼고 / 더하고 싶어요
   >   (c) 다시 정해주세요 — 전체 메뉴 보여드릴게요"
   - `a` → use the auto-derived prefs as-is, `source = "auto-then-confirmed"`.
   - `b` → ask in Korean what to remove/add; edit weights and set
     `source = "auto-then-confirmed"`.
   - `c` → fall through to 5a flow.

   **(P14) Chunk mix.** Ask in plain Korean:
   > "추천 묶음 비율을 정해주세요 (기본: 최근 5년 120편 / 5-10년 전 60편
   > / 10년 이상 전 20편). 그대로 좋다면 '기본', 아니면 원하는 비율을
   > 알려주세요 (예: '최신 위주 / 고전 위주 / 50:30:20')."
   - "기본" → use `{"recent":120,"mid":60,"classic":20}`.
   - "최신 위주" → `{"recent":160,"mid":30,"classic":10}`.
   - "고전 위주" → `{"recent":40,"mid":60,"classic":120}`.
   - Custom triple → parse into integers; ensure each ≥ 5.

6. Call `profile_confirm.py --session <sid> --init <init>
   --snapshot-json @snap.json --corrections-json @corr.json
   --dim-preferences-json @dim.json --chunk-mix-json @mix.json`. Use temp
   files under `<plugin-root>/state/_tmp/` (mkdir -p first). Confirm
   `ok:true`. Tell the researcher (Korean): "확인됐어요. 추천 순서는 다음
   사이클부터 반영돼요. 일단 지금 큐로 시작할게요."

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
   - **(P14) else if `dim_freq` shows ≥ 7 of last 10 in one focus value
     while the researcher's confirmed `dim_preferences.focus` ranks a
     different value highest** → proposal_type = `shift_focus`,
     `target_dim_shift = {"focus":{"add":[underrepresented_code],
     "downweight":[overrepresented_code]}}`.
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
   - **shift_focus** (P14): "최근 본 10편 중 [N편]이 [overrepresented
                  한글라벨] 중심이었는데, 박사님 프로젝트를 보면
                  [underrepresented 한글라벨] 비중도 큰 것 같아요. 다음
                  묶음에선 [underrepresented 한글라벨] 쪽을 더 보여드릴
                  까요?" (한글라벨 = taxonomy.json `label_ko`.)
4. Wait for the researcher's confirmation.
   - "네/좋아요/그렇게 해주세요" → re-call `meta_review.py …
     --proposal-json @p.json --apply` with the proposal stamped; the
     script flips `applied=TRUE` on the same row.
     **For `shift_focus`** (P14): additionally call `profile_confirm.py
     --session <sid> --init <init> --snapshot-json @snap.json
     --dim-preferences-json @updated.json` where `updated.json` is the
     current dim_preferences with the proposal's `add` codes set to
     weight 1.0 and the `downweight` codes set to 0.3 (not 0 — the
     researcher may still want occasional ones). The queue rebuild
     itself is operator-only — tell the researcher in Korean: "확인됐
     어요. 다음 추천 사이클부터 반영돼요." (Do NOT claim the queue is
     rebuilding now.)
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
