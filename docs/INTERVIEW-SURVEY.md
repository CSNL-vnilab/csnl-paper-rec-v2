<!-- v2 — revised after review cycle 2 (Opus 4.8 + GPT-5.5). Adds aim_id tuple as
atomic admission unit, keyword↔aim binding, H5 no-default+tick, N/A-prohibited
core fields, truth-in-advertising (current engine = priority rerank; tuple-
admission/exclude/definition matching is the P28 build spec), MSY blank handling.
One more cycle (c3) remains, then pre-fill per researcher with [확인필요] flags. -->

# CSNL 논문 추천 — 연구 프로파일 정밀 설문 (v5)

<!-- v4 — operator correction: the purpose is to recommend papers that can be
GENUINELY CONNECTED to the research (shared aim/phenomenon/mechanism, incl.
cross-domain/species/method), NOT papers that must EXACTLY match the tuples.
Specificity = the yardstick that separates a genuine connection from a spurious
(word-overlap) one — not an exact-match filter. So "admission invariant" → a
CONNECTION invariant; the tuple is the connection ANCHOR, not a match gate.
(v3 = 3-cycle Opus 4.8 + GPT-5.5 review.)
v5 — exploratory/framework block type (B 유형2; directional prediction optional),
ban-vague-language rule + uncertainty-is-the-researcher's-responsibility,
[자동]/【확인필요】 confidence convention, connection anchor narrowed to
domain×phenomenon×task (N/A-금지 only there). Opus burden/pedantry review next. -->


## 들어가며 (연구원께)

논문 추천이 **연구 범위를 벗어나는** 문제를 고치기 위한 설문입니다. 과거 자동
추출 프로파일이 너무 넓었던 게 원인입니다. 아래를 **원자 수준으로** 적어주시면
그 내용으로 추천 메모리(Postgres)를 재구축합니다.

> **★ 이 설문의 목적 (오해 금지):** 적어주시는 구체성은 **여러분 연구와 정확히
> 일치하는 논문만** 받기 위한 게 **아닙니다.** 오히려 **연구에 "연결될 수 있는"
> 논문** — 공유하는 *aim·현상·메커니즘/계산원리*를 통해, 필요하면 **다른 도메인·
> 종·방법이라도** — 을 폭넓게, 그러나 **진짜로 연결되는 것만** 추천하기 위함입니다.
> 구체성은 *진짜 연결*(여러분이 실제로 하는 것과의 연결)과 *허울뿐인 연결*(단어만
> 우연히 겹치는 것 — 과거 범위-밖 추천의 원인)을 가르는 **잣대**이지, 좁히는
> 필터가 아닙니다. ⇒ 다른 종/도메인 논문도 *진짜 연결되면 계속 추천*되고, §H 에서
> 본인이 **명시적으로 빼는 것만** 제외됩니다.

**작성 방법 — 꼭 읽어주세요**
- 표기: **[자동]** = 메모리로 자신 있게 채운 값(맞으면 그대로 두기, 틀리면 ✗ 후
  수정) · **【확인필요】** = 추정이라 *반드시* 확인. 직접 새로 쓴 답은 ✓ 없이도
  저장됩니다.
- **★ 애매한 표현 금지:** "잘 / 적당히 / 다양한 / 관련된 / 등등 / ~수도 있다 /
  ~를 보려고 한다" 같은 모호어·감정적 묘사 금지 → **구체적·확정적**으로.
  예) ✗ "serial dependence 를 잘 보려고 한다" → ✓ "oriented-Gabor delayed-
  estimation 에서 직전 자극 방향과 현재 오차의 DoG amplitude 를 측정한다".
- **★ 정확도 = 입력의 구체성:** 비우거나 애매하게 둔 항목은 **메모리에 반영되지
  않습니다** — 빈칸을 엔진이 추측해 넓게 추천하던 것이 과거 범위-밖 문제의 원인
  이었습니다. 추천 정확도는 여기 입력의 구체성에 직접 달려 있으니, 빈칸/애매어
  대신 **구체적으로** 부탁드립니다(모르면 `N/A`, 빈칸 금지).
- **연결 anchor = B-요약 튜플의 domain×phenomenon×task 는 N/A 금지** — 이게 있어야
  추천이 "연결"됩니다. 나머지(metric·조건·방향 등)는 해당 없으면 `N/A`.
- 용어는 **본인이 실제 쓰는 정확한 명칭**으로.

> (작동 방식, 정직하게) 현재 엔진은 키워드 substring 으로 추천 **우선순위**를
> 높이는 수준입니다. 이 설문이 만드는 정밀 메모리를 **실제로 좁히는 매칭**(아래
> "admission invariant")은 엔진 업그레이드(P28)로 구현 예정이며, 이 설문은 그
> 데이터 사양입니다. 즉 **지금 당장 모든 범위-밖이 사라지진 않지만**, 메모리가
> 정밀해지고 엔진이 P28 로 올라가면 좁아집니다.

---

## A. 기본 정보

| 항목 | 사전기입 | [✓/✗] | (수정) |
|---|---|---|---|
| 이름(이니셜) | | [ ] | |
| 직책 | | [ ] | |
| 소속 lab / 지도교수 | | [ ] | |
| active 프로젝트 수 | | [ ] | |
| 주 연구 한 문장 | | [ ] | |

---

## B. 핵심 가설/주제 (프로젝트별 블록 — atomic) ★최우선

> **비중 큰 프로젝트 최대 3개**만 블록 작성. 각 블록에 **가설 ID**(A1, A2, …)를
> 부여 — 이 ID 가 추천 연결의 **원자 단위**입니다.
>
> **블록 유형** — 명제형 가설이 *아니어도 괜찮습니다.* 각 블록 첫 줄에 유형 표시:
> - **[유형1 명제형]** — 방향 예측이 있는 가설. B1–B10 작성(B8 예측 포함).
> - **[유형2 탐색형/프레임워크형]** — 아직 narrow 안 됨 / 현상을 explore·발견하는
>   연구 / falsifiable 하지 않은 framework. B1–B7 을 *채울 수 있는 만큼* 구체적으로
>   (특히 **domain·phenomenon·task**) + B8 대신 **B8′**(탐구 질문/관심 현상, 방향
>   예측 불필요) + B9·B10. "방향 예측 미정"이라 써도 됩니다. **domain×phenomenon×
>   task 만 구체적이면 연결 추천은 유형1과 동일하게 작동**합니다(예측은 순위
>   refiner 일 뿐 연결 조건이 아님). ※ 단, "탐색형"이라도 *무엇을* 보는지(domain·
>   현상·과제)는 애매어 없이 구체적으로 — 그게 연결의 anchor 입니다.

### ▣ 가설 #A1   (사전기입 시 각 칸 [✓/✗])

- **B1. Domain**(무엇을 지각/추정/기억) *필수*: _orientation / spatial frequency /
  motion / numerosity / duration / color / contrast / depth / face identity /
  expression / size / position …_ → ______ [ ]
- **B2. Population/종**: _신경전형 성인 / 아동 / 임상(명시) / 인간만_ → ______ [ ]
- **B3. Task + 자극** *필수*: paradigm _2AFC / detection / delayed estimation
  (adjustment) / match-to-sample / continuous report / n-back / magnitude
  estimation / categorization / search_ → ______ ; 자극 _Gabor/RDK/dot-array/
  face-morph…_ → ______ ; 시행구조(ISI/지연) → ______ [ ]
- **B4. 동시측정**: ☐행동 ☐fMRI(_T) ☐EEG/MEG ☐eye ☐pupil ☐tES ☐기타_ [ ]
- **B5. Phenomenon** *필수*: _cardinal/oblique bias / serial dependence
  (attractive|repulsive) / central-tendency bias / adaptation aftereffect /
  variability(precision) / error rate / swap error / history effect / gambler's
  fallacy …_ → ______ [ ]
- **B6. 비교 조건**: _same vs diff position / low vs high contrast / same vs diff
  task / set-size / categorical vs continuous …_ → ______ [ ]
- **B7. 정량화 지표** (유형1 권장 · 유형2 미정 가능): _DoG amp·width / bias slope / θ̂−θ / circular SD·
  var(θ̂) / regression β / d′ / JND·threshold / decoding acc / BOLD
  autocorrelation / EEG band amp(band) / alpha phase / pupil diameter_ → ______ [ ]
- **B8. (유형1) 가설 한 문장**(템플릿): "**[B1]**에서 **[B3]**로 **[B6]**을 비교하면
  **[B5]**이 **[B7]**로 **[방향]** 나타날 것" → ______ [ ]
- **B8′. (유형2) 탐구 질문 / 관심 현상**(방향 예측 불필요): "**[B1]**에서 **[B3]**로
  **[B5]**(들)이 어떻게 나타나는지 탐색 — 방향 예측: 미정" → ______ [ ]
  *(유형1·2 중 본인에 해당하는 한 줄만)*
- **B9. 배경**: ______ [ ]
- **B10. Seed paper**: ______ [ ]

### ▣ 가설 #A2 / #A3 … (복제)

### ▣ B-요약: 가설 튜플 표 (★ admission 의 원자 단위 · N/A 금지)

> 각 블록을 **한 줄 튜플**로. 이 튜플은 추천이 **연결될 기준점(connection anchor)**
> 입니다 — 논문이 *정확히 일치*해야 하는 게 아니라, 이 튜플의 요소(특히 phenomenon·
> mechanism)에 **진짜로 연결**되면 추천 후보가 됩니다(다른 도메인·종·방법도 OK,
> 연결만 genuine 하면). domain×phenomenon×task = 연결 anchor(필수 3요소, N/A 금지),
> metric·조건·방향 = 순위 refiner. domain/phenomenon 를 따로 두면 허울뿐인 조합이
> 생기므로 **반드시 한 행에 묶어** 적어주세요.

| 가설ID | **domain** | **phenomenon** | **task** | metric(refiner) | 조건(refiner) | 방향(refiner) |
|---|---|---|---|---|---|---|
| A1 | | | | | | |
| A2 | | | | | | |
| A3 | | | | | | |

> (굵은 3열 = 연결 anchor, N/A 금지. 방향(refiner) = 예측 부호/유형, 예: attractive /
> repulsive / 증가 / 감소 / n.s. / **탐색·미정**[유형2]. 모르면 비워도 됩니다.)

---

## H. 연구 범위 & 제외 (★ 범위-밖 차단)

**H1. in-scope 한 단락**: ______ [ ]

**H2–H4. 제외 목록** — **각 행이 known_negatives 1건**이 됩니다. 구체적으로,
한 항목당 한 행. (유형 = domain / phenomenon / method / population / topic /
anti-example(실제 "오면 안 되는" 논문 제목) / adjacent(인접하지만 관심 없음))

| 제외 항목 (구체) | 유형 | 왜 아닌가 (한 줄) |
|---|---|---|
| _예: value-based decision·reinforcement learning_ | topic | 가치기반 의사결정은 내 지각/추정 범위 밖 |
| _예: navigation / grid·place cell_ | topic | |
| _예: multi-item WM capacity_ | phenomenon | |
| _(실제로 추천돼서 안 됐던 논문 제목)_ | anti-example | |
| _(인접하지만 관심 없는 주제)_ | adjacent | |
| | | |

**H5. 종/집단/방법 허용도** *(대부분 비워도 됩니다)* — 시스템 기본은 *현상·메커니즘이
맞으면 종 불문 추천*입니다. **좁히고 싶은 행만** △(현상·메커니즘이 정확히 맞을 때만)
또는 ✕(제외)로 표시하고 그 행에만 "이런 논문" 예 1개. 나머지는 빈칸=기본(○) 유지.

| 대상 | ○/△/✕ | [✓확인] | ✕·△ 제외 예 (필수) |
|---|---|---|---|
| 비인간 영장류(macaque) | | [ ] | |
| 설치류(rat/mouse) | | [ ] | |
| 임상·정신질환 집단 | | [ ] | |
| 발달(아동/영유아) | | [ ] | |
| AI/ML·인공신경망 | | [ ] | |
| 로보틱스 | | [ ] | |
| 계산모델-only(데이터 없음) | | [ ] | |
| 순수 이론/리뷰 | | [ ] | |
| 비인간 modality(단일세포·calcium 등) | | [ ] | |

---

## G. 키워드 + 과학적 정의 (+ 가설 바인딩)

> 키워드는 추천 **순위 보정(reranker)**에 쓰입니다(단독으로 논문을 통과시키진
> 않음). 키워드 *나열*은 가볍게 — 단, **매칭이 헷갈리는 핵심 5개 정도만** 본인
> operational 정의를 적어주세요(같은 단어가 연구자마다 뜻이 달라서). '제외 의미'·
> 가설바인딩·출처는 **선택**(충돌하는 용어만).

| 키워드 | operational 정의 (★핵심 5개만 필수) | 제외 의미 (충돌 용어만·선택) | 가설ID(선택) | 출처(선택) |
|---|---|---|---|---|
| _history effect_ | 직전 *자극*이 현재 *지각*을 끄는 효과 | 반응 priming 아님 | A1 | F&W 2014 |
| | | | | |
| (핵심 위주, 너무 많지 않게) | | | | |

- 헷갈리지만 본인은 구분하는 용어쌍: ______

---

## C. 실험 인프라 (한 줄)

보유/사용 장비·환경(예: 7T fMRI@OO, 64ch EEG, EyeLink 1000, tDCS, Psychtoolbox):
______ [ ]

---

## D. 관심 그룹 / PI

| 주목 PI(이름+소속) | 어느 내 가설ID/현상과 연결 (필수) | [✓/✗] |
|---|---|---|
| | | [ ] |
| (5–10명) | | [ ] |

- 자주 추천되지만 관심 아닌 PI/그룹: ______

---

## E. Computational modeling (모델↔현상↔방식 3종 필수)

- 사용/관심: ☐한다 ☐안한다 ☐읽기만(추천 원함)

| 모델(정확히) | 적용 현상(가설ID) | 사용방식 | [✓/✗] |
|---|---|---|---|
| _efficient coding (Fisher info)_ | A1 (cardinal bias) | ☐적용 ☐검증 ☐확장 ☐반론 | [ ] |
| | | ☐적용 ☐검증 ☐확장 ☐반론 | [ ] |

- 모델링 논문 추천: ☐적극 ☐가끔 ☐거의 불필요

---

## F. 선호 분석 방법론 (method-only 는 약한 신호)

☐regression(mixed) ☐psychometric fit ☐DoG fit ☐MDS ☐PCA ☐RSA ☐MCMC/HBM
☐bootstrap/permutation ☐decoding(SVM/LDA) ☐time-frequency ☐GLM(fMRI) ☐기타_

---

## I. 보편 심화 질문

1. 5년 내 핵심 질문 1–2개: ______
2. 큰 이론 프레임(efficient coding/Bayesian brain/predictive coding/attractor…): ______
3. 최근 1–2년 흥미로웠던 논문 3편(저자(연도))+왜: ______
4. 형태 선호: ☐empirical ☐neuro ☐modeling ☐methods ☐review ☐preprint
5. 빈도/난이도 선호: ______
6. (자유) 추천 중 좋았던/나빴던 구체 예 + 바라는 점: ______

---

## 부록 (운영자용)

### A. Connection invariant — 추천 채택 규칙 (P28 엔진 빌드 사양; 현재 미구현)

> 목적: 논문을 *정확히 일치*시키는 게 아니라 **genuine 하게 연결**되는 것을 찾는다.
> 구체성(튜플·정의)은 genuine 연결 vs spurious(단어만 겹침) 연결을 가르는 잣대.
> (현재 엔진은 substring+우선순위 reranking 만; 아래는 P28 빌드 목표.)

1. **CONNECT, not exact-match**: 논문은 어떤 가설ID 의 **aim · phenomenon ·
   mechanism 중 하나에 genuine 하게 연결**되면 추천 후보다 (P26 계약 A/B/C). 연결의
   *anchor* 는 B-요약 튜플(domain/phenomenon/task)이지만 **정확히 일치할 필요는
   없다** — 다른 도메인·종·방법이라도 그 튜플 요소(특히 phenomenon·mechanism)에
   진짜로 연결되면 후보. **genuine 판정 = same-job**: 예) 같은 메커니즘 이름이 *본인
   현상에 실제로 쓰일 때만* C; 단어만 겹치는 spurious 연결은 제외.
2. **H-veto**: H2–H4 hard-negative / H5 ✕ 에 걸리면 제외(다른 신호 무시).
3. **H5 △**: 그 종/방법은 연결이 *특히 강할 때만*(phenomenon·mechanism 이 명확히
   일치) 채택.
4. **Rerankers**(단독 채택 불가): metric·condition·direction·model(E)·keyword-unbound
   (G)·PI(D)·method(F)·theory·seed 는 *연결된* 논문의 **순위**만 조정.
5. **definition-aware**: G 정의/제외의미로 spurious(동단어) 연결 차단.

### B. 미구현 소비자 (구현 필요 — 운영자 검토용)

| 소비자 | 상태 |
|---|---|
| `known_negatives` 컬럼 / per-researcher exclude pass | **미구현** (P24 follow-up ①) |
| aim-tuple admission (substring → conjunction) | **미구현** (현재 build_researcher_queue 는 substring+positive-only) |
| fingerprint `definition` 필드 + subtractive scoring | **미구현** (현재 matcher 는 정의 무시) |
| H5 species/clinical override gate | **미구현** |
| keyword↔aim 바인딩 enforcement | **미구현** |

### C. 응답 → 메모리 매핑

| 설문 | 메모리 |
|---|---|
| A | profile.summary |
| B 블록 + B-요약 튜플 | profile.aims/phenomena (**aim_id 튜플**); P28 admission 의 원자 단위 |
| B5 + G | `fingerprints/<INIT>.json` (+`definition`,`bound_aim` 필드 신설); 정의-anchored |
| B9/B10 | open_questions, seed → snowball anchor |
| H1–H4 | known_negatives(+exclude pass 신설) |
| H5 | researcher species/method override(✕ veto / △ tuple-gated); 기본 비움=미확인 |
| C | profile.infra |
| D | PI rerank(+aim 바인딩); negative PI deprioritize |
| E | mechanisms_theories; **same-job**(모델이 그 가설 현상에 쓰일 때만 C) |
| F | method rerank(약) |
| I | theory_frame, recency/format 선호 |

> 핵심: **genuine 연결(튜플 anchor) admits · H vetoes · spurious(단어만 겹침)
> 차단 · 나머지는 rerank.** 목표는 *좁히기*가 아니라 *연결 정확도*다 — 다른 도메인·
> 종 논문도 진짜 연결되면 추천하고, 허울뿐인 연결만 거른다. 설문은 그 판정에 필요한
> 구체성을 모은다. (MSY 등 미인터뷰자는 **빈 설문**으로 시작.)
