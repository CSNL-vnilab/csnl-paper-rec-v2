<!-- v2 — revised after review cycle 2 (Opus 4.8 + GPT-5.5). Adds aim_id tuple as
atomic admission unit, keyword↔aim binding, H5 no-default+tick, N/A-prohibited
core fields, truth-in-advertising (current engine = priority rerank; tuple-
admission/exclude/definition matching is the P28 build spec), MSY blank handling.
One more cycle (c3) remains, then pre-fill per researcher with [확인필요] flags. -->

# CSNL 논문 추천 — 연구 프로파일 정밀 설문 (v13)

<!-- v13 — operator round 9: §분석방법 2축 재분류 — (1)데이터 종류(behavior/eye-tracking/
neural/ANN) 먼저, (2)각 안에서 큰 범주 approach(descriptive model fitting · hypothesis
testing · normative model+parameter recovery · decoding · state-space/manifold ·
representational alignment · encoding model)로 — 지엽적 method 나열 폐지, full term+abbr.
컨버터: 줄바꿈/띄어쓰기 버그 수정(emphasis·flag 인접 공백/구분자 보존 = Bug A, 블록 내
soft-wrap newline→space = Bug B; "작성 방법" 문장 절단 해소). -->

<!-- v12 — operator round 8: §분석방법 재구성(행동 curve-fitting 통합 + fMRI
GLM/retinotopy/RSA/MVPA/neural manifold 구분, full term + abbreviation); PI-컨펌
문구 삭제 → "모르면 지도교수·사수 확인, 미확정만 미정" 규칙; 플래그 컬러(컨버터:
[자동]=초록·【확인필요】=노랑·(직접 작성)=빨강 배경 + 페이지 상단 카운트); 줄간격/링크/
수식 렌더 점검; 사전채움 근거 = 라이브 PostgresDB(csnl_research·dim_preferences·
archive_responses) 재검증. -->

<!-- v11 — operator round 7: 가독성 리팩터 — 알파벳 섹션 헤더(A–I) → 직관적 한글 이름
+ 섹션별 한 줄 안내; §-교차참조 및 내부 용어(가설ID · connection anchor · refiner 등)
제거; B1–B10 필드 코드 제거; ▣ 가설 #A → 프로젝트 N. 컨버터: 마크다운 `*` 누출 수정
(re.DOTALL + _italic_ 지원 + 잔여 마커 strip) + 섹션/프로젝트 divider 간격. -->

<!-- v10 — operator round 6: §I (이론 프레임 + 형태 선호) DELETED — theory-frame 보기가
적고 편향적이며 mechanism 은 B8m(블록별)이 담당. 들어가며 추가 압축 + AI/세션 메타
프레이밍 제거(설문은 오직 연구자 research memory 를 체계적으로 빌드하는 추가설문).
§H note 의 RNN+fMRI+monkey 예시 제거. -->

<!-- v9 — operator round 5: 들어가며 compressed (long 오해-금지 blockquote → one 핵심
note); +mechanism axis (B8m field per block + B-요약 mechanism column; refiner cols
dropped → B-요약 5-col); §H exclusions optional; §A "주 연구 한 문장" ★필수; §C
single-colon; design/readability polish. RENDERING: the Notion API renders typed
blocks, NOT markdown — the converter emits native to_do / callout / table (so
[ ]/☐/______ become real checkboxes/answer-boxes/tables, not literal text). -->

<!-- v8 — operator round 4: §H5(종/방법 우선순위 표) DELETED — 종(species)은 어떤
기준도 아님(종 불문 functional brain; recurrent neural network (RNN) + functional
Magnetic Resonance Imaging (fMRI) + monkey electrophysiology 한 논문 공존). §G 는
온톨로지 인터뷰가 아님 → 모든 키워드 정의 요구 폐지, **중의적(polysemous) 용어에만**
정의 요청(G1 목록 + G2 중의어 정의; 예 'bias','reference'). §E computational model =
확정된 것만, 공란 허용(추측 금지). §I = 단답·메모리 필수만(이론 프레임 ✓ + 형태 선호
✓ 2항만; 추상 개방형 질문 제거). -->

<!-- v7 — operator round 3: per-project framing (each project=가설ID=연결 단위);
domain·species = PRIORITY signal, NOT rejection (same domain + neurotypical human
= default top priority; cross-domain/species ranked lower, never rejected; reject =
phenomenon-mismatch only; §H5 reframed to priority); academic terms written in FULL
in the DB — avoid abbreviations, spell out when used (e.g. functional Magnetic
Resonance Imaging (fMRI)); NO question-count limit — uniform full specificity for
all researchers (§G definition for every keyword; §D full). Flag-tiering kept. -->


<!-- v6 — operator round 2: 지도교수=SHL(이상훈) default; ban arbitrary Korean
translation of academic terms; B8′ demands a minimal mechanism/direction
speculation w/ examples (not vague "어떻게"), 미정 allowed; exclusions reframed to
phenomenon/research-focus mismatch (NOT domain/species) + mandatory structured
contrast template + de-weighted as past-answer reference (PI confirms);
phenomenon = THE connection criterion, cross-species/domain welcome; encourage
미정 for exploratory parts; don't over-weight juniors' prior answers. -->


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

연구자님의 연구 프로파일을 더 체계적으로 정리해 논문 추천 메모리(Postgres)에
반영하기 위한 추가 설문입니다. 아래를 **구체적으로** 적어주세요.

**작성 방법 — 입력 방식 안내**
- **체크박스(네모 칸) 항목**: 내용이 맞거나 본인에게 해당하면 **체크**하고, 틀리거나 해당 없거나 모르면 비워두세요(미리 채워진 값이 틀리면 글자를 고친 뒤 체크).
- **✍️ 회색 답변 상자 · 표(table)**: 칸을 클릭해 직접 입력하세요(해당 없으면 비워두셔도 됩니다).
- **색 표시**(미리 채운 값의 신뢰도): [자동] = 초록(메모리로 확인된 값, 맞으면 그대로) · 【확인필요】 = 노랑(꼭 확인해 주세요) · (직접 작성) = 빨강(비어 있으니 채워주세요).
- 학술 용어는 **원어(영어)**로(약자는 전체 명칭 병기, 예: functional Magnetic Resonance Imaging (fMRI)). 잘 모르는 부분은 지도교수·사수께 확인하고, **아직 확정 안 된 것만 "미정"**. 이 설문은 **프로젝트별**이며, "연구 프로젝트별 핵심 주제"의 domain × phenomenon × task 는 꼭 채워주세요.

---

## 기본 정보

이름·직책·소속과 현재 진행 중인 연구를 한 문장으로 적어주세요.

| 항목 | 사전기입 | [✓/✗] | (수정) |
|---|---|---|---|
| 이름(이니셜) | | [ ] | |
| 직책 | | [ ] | |
| 소속 lab / 지도교수 | CSNL / **이상훈 (SHL, Sang-Hun Lee)** — 전원 동일 [자동] | [ ] | |
| active 프로젝트 수 | | [ ] | |
| 주 연구 한 문장 ★필수 | | [ ] | |

---

## 연구 프로젝트별 핵심 주제 ★최우선

진행 중인 프로젝트마다 무엇을(domain) 어떤 과제(task)로 보고 어떤 현상(phenomenon)을
연구하는지 적어주세요. 추천이 여기에 맞춰집니다.

> **비중 큰 프로젝트 최대 3개**만 블록으로 작성해 주세요. 각 블록을 **프로젝트 1,
> 프로젝트 2 …** 로 번호를 붙입니다 — 이 프로젝트 단위로 논문이 연결됩니다.
>
> **블록 유형** — 명제형 가설이 *아니어도 괜찮습니다.* 각 블록 첫 줄에 유형을 표시해
> 주세요:
> - **[유형1 명제형]** — 방향 예측이 있는 가설. Domain~Seed paper 항목을 모두 작성합니다(가설 한 문장 포함).
> - **[유형2 탐색형/프레임워크형]** — 아직 narrow 되지 않았거나, 현상을 explore·발견
>   하는 연구이거나, falsifiable 하지 않은 framework. Domain~정량화 지표 항목을 *채울 수 있는 만큼*
>   구체적으로(특히 **domain·phenomenon·task**) 적고, 가설 한 문장 대신 **탐구 질문**(관심
>   현상 — 방향 예측 불필요)과 배경·Seed paper 를 적습니다. "방향 예측 미정"이라고 쓰셔도
>   됩니다. **domain×phenomenon×task 만 구체적이면 연결 추천은 유형1과 똑같이
>   작동**합니다(예측은 순위를 다듬을 뿐, 연결 조건은 아닙니다). ※ 단, "탐색형"
>   이어도 *무엇을* 보는지(domain·현상·과제)는 애매한 표현 없이 구체적으로 적어주세요
>   — 그것이 연결의 기준점입니다.

### 프로젝트 1   (사전기입 시 각 칸 [✓/✗])

- **Domain**(무엇을 지각/추정/기억) *필수*: _orientation / spatial frequency /
  motion / numerosity / duration / color / contrast / depth / face identity /
  expression / size / position …_ → ______ [ ]
- **Population (대상/종)**: _신경전형 성인 / 아동 / 임상(명시) / 인간만_ → ______ [ ]
- **Task + 자극** *필수*: paradigm _2AFC / detection / delayed estimation
  (adjustment) / match-to-sample / continuous report / n-back / magnitude
  estimation / categorization / search_ → ______ ; 자극 _Gabor/RDK/dot-array/
  face-morph…_ → ______ ; 시행구조(ISI/지연) → ______ [ ]
- **동시측정**: ☐행동 ☐fMRI(_T) ☐EEG/MEG ☐eye ☐pupil ☐tES ☐기타_ [ ]
- **Phenomenon (현상)** *필수*: _cardinal/oblique bias / serial dependence
  (attractive|repulsive) / central-tendency bias / adaptation aftereffect /
  variability(precision) / error rate / swap error / history effect / gambler's
  fallacy …_ → ______ [ ]
- **비교 조건**: _same vs diff position / low vs high contrast / same vs diff
  task / set-size / categorical vs continuous …_ → ______ [ ]
- **정량화 지표** (유형1 권장 · 유형2 미정 가능): _DoG amp·width / bias slope / θ̂−θ / circular SD·
  var(θ̂) / regression β / d′ / JND·threshold / decoding acc / BOLD
  autocorrelation / EEG band amp(band) / alpha phase / pupil diameter_ → ______ [ ]
- **(유형1) 가설 한 문장**(템플릿): "**Domain**에서 **Task**로 **비교 조건**을 비교하면
  **Phenomenon**이 **지표**로 **방향** 나타날 것" → ______ [ ]
- **(유형2) 탐구 질문 + 최소한의 메커니즘/방향 speculation** — 막연히 "어떻게"로
  두지 마시고, **가능한 메커니즘이나 방향을 한 조각이라도** 적어주세요(확정이 아니어도
  좋고, 정말 없으면 "미정"). 예시:
  > ▸ "post-decisional bias 는 attraction 을, perceptual carryover 는 repulsion 을
  >   일으켜 서로 상충하는 힘으로 bias 를 만든다"
  > ▸ "ITI/ISI 가 길수록(또는 실험조건 X 가 클수록) Phenomenon(현상)의 bias 가 커진다"
  > ▸ "두 조건이 attraction vs repulsion 으로 갈릴 것이다"
  → ______ [ ]
  *(유형1·2 중 해당하는 한 줄만 적으세요. 메커니즘이 떠오르면 유형1처럼 방향까지 적어도
  좋습니다.)*
- **메커니즘 / 계산이론 (있으면)** *(있으면 적고, 미정도 가능)*: 이 현상을 만든다고
  보시는 계산 원리·메커니즘입니다 — 예: efficient coding, Bayesian cue combination,
  attractor dynamics, divisive normalization, post-decisional bias. **연결의 한 축**
  이며, 없으면 "미정"으로 두세요. → ______ [ ]
- **배경**: ______ [ ]
- **Seed paper**: ______ [ ]

### 프로젝트 2 / 프로젝트 3 … (복제)

### 프로젝트 한눈에 보기

> 각 프로젝트를 **한 줄로** 요약해 주세요. 이 한 줄이 추천의 **연결 기준점**입니다 —
> 논문이 *정확히 일치*해야 하는 것이 아니라, 이 한 줄의
> 요소(특히 phenomenon·mechanism)에 **진짜로 연결**되면 추천 후보가 됩니다(연결만
> genuine 하면 다른 도메인·종·방법도 괜찮습니다). domain×phenomenon×task = 연결
> 기준점(필수 3요소, 반드시 채워주세요)이고, mechanism/계산이론 = 추가 연결축(미정 가능)입니다.
> (지표·조건·방향 같은 순위 보정 항목은 위 프로젝트 블록에 이미 적으셨으니 여기서는 생략합니다.)
> domain 과 phenomenon 을 따로 떼어 적으면 허울뿐인 조합이 생기므로, **반드시 한 행에
> 묶어** 적어주세요.

| 프로젝트 | **domain** | **phenomenon** | **task** | mechanism/계산이론 |
|---|---|---|---|---|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |

> (굵은 3열 = 연결 기준점이므로 꼭 채워주세요. **mechanism/계산이론** = 추가 연결축(미정 가능).
> 모르면 비워두셔도 됩니다.)

---

## 추천에서 빼고 싶은 주제 (선택)

내 연구 범위와 추천에서 빼고 싶은 주제를 적어주세요. 없으면 비워두셔도 됩니다.

> **종(species)·측정기법(method)은 제외 기준이 아닙니다** — 아래 제외는 **phenomenon /
> research-focus 가 다를 때만** 의미가 있습니다(종·도메인·기법만으로는 빼지 않습니다).

**내 연구 범위 한 단락**: ______ [ ]

**빼고 싶은 주제 목록** *(★ 선택 — 없으면 비워두셔도 되고, 특히 추천 이력이 적으면
건너뛰세요)* — 각 행이 빼고 싶은 주제 1건입니다. **배제 기준은 "논문의 phenomenon/
research-focus 가 내 것과 다르다"**이지, 종·도메인이 달라서가 아닙니다(도메인·종이
달라도 현상만 맞으면 도움이 됩니다). 적으실 때는 **"왜 아닌가"를 구체적 대조로** 적어
주세요 — "관심 없음", "거리가 멈" 같은 추상적 사유로는 AI 가 근거를 추론할 수
없습니다. 템플릿:
> "내 research-focus 는 **[내 domain]의 [내 phenomenon]**인데, 이 논문은
> **[논문 domain]의 [논문 phenomenon]**을 다룬다 → 내 phenomenon([X])에서 벗어남."

| 제외 항목 (구체) | 유형 | 왜 아닌가 (구체 대조 — 위 템플릿) |
|---|---|---|
| _예: value-based choice (선호 형성)_ | phenomenon | "내 focus 는 perceptual estimation bias 인데, 이 논문은 value-based choice 의 선호 형성을 다룸 → 내 phenomenon 밖" |
| _(있다면) 실제로 추천됐는데 아니었던 논문 제목_ | anti-example | "내 [domain]의 [phenomenon]인데 이 논문은 [domain′]의 [phenomenon′] → 내 phenomenon 밖" |
| | | |

> *(유형 = phenomenon / research-focus / anti-example / adjacent.
> **domain·species·population·method 만으로는 제외 사유가 되지 않습니다** — 오히려
> 환영 대상입니다.)*
> *(사전기입된 제외 항목은 **과거 인터뷰 응답을 참고한 것**이니, 종·도메인 기준으로
> 너무 넓게 빼지 않았는지 다시 확인해 주세요. 확신이 없으면 지도교수님·사수께 확인 부탁드립니다.)*

---

## 검색 키워드

내 연구를 잘 나타내는 키워드를 적어주세요. 뜻이 갈리는 용어만 따로 설명해주시면 됩니다.

> 키워드는 추천 **순위 보정**에 쓰입니다(키워드만으로 논문을 통과시키지는
> 않습니다). **이건 용어 사전(ontology) 인터뷰가 아니므로**, 모든 키워드에 정의를 달
> 필요는 없습니다. 키워드 목록은 맞으면 그대로 두시고(틀리면 ✗, 빠진 것은 추가), **중의어 정의에는
> 실험·가설에 따라 뜻이 달라지는 *중의적 용어*에만** 정의를 적어주세요.

**키워드 목록** (사전기입 — 맞으면 그대로 두시고, 틀리면 ✗, 추가 환영):
______

**중의적 용어만 정의** — 같은 단어가 실험·가설에 따라 의미가 갈리는 것만 적어주세요.
예) "**bias**" = attractive / repulsive / estimation 중 무엇인지 · "**reference**" =
reference frame / reference stimulus 중 무엇인지 · "gain", "adaptation",
"normalization", "tuning" 등. 의미가 분명한 용어(예: "serial dependence")는 **정의가
필요 없습니다**.

| 중의적 용어 | 본인 연구에서의 operational 정의 | (선택) 헷갈리는 옆 용어 — 아닌 것 |
|---|---|---|
| _예: bias_ | _attractive bias = 추정이 직전 자극 쪽으로 끌림_ | _repulsion / response priming 아님_ |
| | | |

---

## 실험 장비·환경

보유하거나 사용하시는 실험 장비·소프트웨어 환경을 한 줄로 적어주세요.

보유하거나 사용하시는 장비·환경 (예 — 7T fMRI@OO · 64ch EEG · EyeLink 1000 · tDCS ·
Psychtoolbox): ______ [ ]

---

## 관심 있는 연구자 (PI)

논문을 주목하는 PI(연구책임자)와, 그분이 내 어느 프로젝트·현상과 연결되는지 적어주세요.

| 주목하는 PI(이름+소속) | 내 어느 프로젝트/현상과 연결되나 (필수) | [✓/✗] |
|---|---|---|
| | | [ ] |
| (5–10명) | | [ ] |

- 자주 추천되지만 관심 없는 PI/그룹: ______

---

## 계산 모델 (computational model)

지금 실제로 쓰고 있거나 확정한 계산 모델이 있으면 적어주세요. 없으면 비워두셔도 됩니다.

> 후보 모델이 많아 아직 고르지 않으셨을 수도 있습니다. **지금 실제로 쓰고 있거나
> 확정한 모델이 있을 때만** 적어주세요 — 없으면 **공란으로 두셔도 됩니다**(추측으로
> 채우지 마세요).

- modeling 을 직접 하시나요: ☐한다 ☐안한다 ☐읽기만(추천 원함) ☐미정

| 모델(정확한 이름, 확정된 것만) | 적용 현상(프로젝트) | 사용 방식 | [✓/✗] |
|---|---|---|---|
| | | ☐적용 ☐검증 ☐확장 ☐반론 | [ ] |

- modeling 논문 추천을 원하시나요: ☐적극 ☐가끔 ☐거의 불필요

---

## 관심 방법론

먼저 (1) **어떤 데이터**를 분석하시는지 고르고, 그 안에서 (2) **어떤 방식**으로 분석하시는지 큰 범주로 골라주세요(복수 가능 · 데이터 종류만 고르고 방식은 비워두셔도 됩니다). 큰 범주만 고르시면 되고, 괄호 안은 세부 기법 예시입니다.

**행동 (behavior)**: ☐ descriptive psychophysics / curve fitting (심리물리 곡선에서 지표 추출; psychometric function fitting · signal detection theory (SDT), d-prime · difference-of-Gaussians (DoG) curve fitting) ☐ sequential-sampling / evidence-accumulation modeling (선택·반응시간 동시 모델링; drift-diffusion model (DDM) · leaky competing accumulator (LCA) · hierarchical DDM (HDDM)) ☐ estimation / bias modeling (연속 추정의 bias·precision; mixture model of estimation error · bias–variance decomposition) ☐ normative / ideal-observer modeling (규범적 관찰자 기반; Bayesian observer / ideal-observer model · efficient-coding model · parameter recovery) ☐ statistical hypothesis testing (t-test · analysis of variance (ANOVA) · (generalized) linear mixed-effects model (GLMM))

**시선·동공 (eye-tracking / pupillometry)**: ☐ descriptive fixation / saccade metrics (고정·도약 지표; fixation duration · saccade amplitude · microsaccade rate · scanpath) ☐ pupillometry (동공 기반 각성·인지부하; pupil diameter / dilation · tonic vs phasic pupil · locus-coeruleus–norepinephrine (LC-NE) arousal index) ☐ gaze dynamics / state-space (시선 궤적 동역학; hidden Markov model (HMM) of fixation states) ☐ statistical hypothesis testing ((generalized) linear mixed-effects model (GLMM) · cluster-based permutation test)

**신경 (neural — fMRI 주력; electroencephalography (EEG) / magnetoencephalography (MEG); electrophysiology)**: ☐ encoding models (자극·특징 → 반응; general linear model (GLM) · population receptive field (pRF) / retinotopy · voxelwise encoding model) ☐ decoding / multivariate pattern analysis (반응 → 자극 분류·복원; multi-voxel pattern analysis (MVPA) · searchlight · inverted encoding model (IEM)) ☐ representational alignment (표상 구조 비교; representational similarity analysis (RSA) with representational dissimilarity matrix (RDM)) ☐ connectivity modeling (영역 간 결합; functional connectivity · effective/causal — dynamic causal modeling (DCM) / directed-graph (DAG) causal discovery) ☐ state-space / neural manifold (저차원 집단 동역학; demixed principal component analysis (dPCA) · neural manifold) ☐ univariate statistical testing (단변량; mass-univariate GLM · cluster-based permutation test)

**인공신경망 (artificial neural network, ANN)**: ☐ task-trained network modeling (과제 학습 회로; recurrent neural network (RNN) · convolutional neural network (CNN) as cortical model) ☐ representational alignment (뇌·모델 표상 비교; representational similarity analysis (RSA) · centered kernel alignment (CKA)) ☐ state-space / latent dynamics (잠재 상태공간; fixed-point / attractor analysis · neural manifold) ☐ decoding / probing (내부 표상 probing; linear probe · diagnostic classifier) ☐ training dynamics (학습 과정; learning-curve / representation-trajectory analysis)

**기타**: ______

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
2. **H-veto**: H2–H4 hard-negative(= phenomenon/research-focus mismatch)에 걸리면
   제외(다른 신호 무시). 종·도메인·기법은 veto 가 아니다.
3. **species = 비기준 · domain = priority reranker (NOT veto)**: 종(species)은 채택
   /순위 어디에도 영향 없음(종 불문 functional brain; RNN + fMRI + monkey
   electrophysiology 공존). domain 만 약한 우선순위(동일 domain + neurotypical human
   = 최우선; 다른 domain — value/emotion/face 등 — 은 순위만 낮을 뿐 채택). veto 는
   phenomenon mismatch 일 때만.
4. **Rerankers**(단독 채택 불가): metric·condition·direction·model(E)·keyword-unbound
   (G)·PI(D)·method(F)·theory·seed 는 *연결된* 논문의 **순위**만 조정.
5. **definition-aware**: G 정의/제외의미로 spurious(동단어) 연결 차단.

### B. 미구현 소비자 (구현 필요 — 운영자 검토용)

| 소비자 | 상태 |
|---|---|
| `known_negatives` 컬럼 / per-researcher exclude pass | **미구현** (P24 follow-up ①) |
| aim-tuple admission (substring → conjunction) | **미구현** (현재 build_researcher_queue 는 substring+positive-only) |
| fingerprint `definition` 필드 + subtractive scoring | **미구현** (현재 matcher 는 정의 무시) |
| keyword↔aim 바인딩 enforcement | **미구현** |

### C. 응답 → 메모리 매핑

| 설문 | 메모리 |
|---|---|
| A | profile.summary |
| B 블록 + B-요약 튜플 | profile.aims/phenomena/**mechanisms** (**aim_id 튜플** + B8m mechanism 열); P28 admission 의 원자 단위. mechanism = 연결축 C 의 per-aim primary source |
| B5 + G | `fingerprints/<INIT>.json` (+`definition`,`bound_aim` 필드 신설); 정의-anchored |
| B9/B10 | open_questions, seed → snowball anchor |
| H1–H4 | known_negatives(+exclude pass 신설) |
| C | profile.infra |
| D | PI rerank(+aim 바인딩); negative PI deprioritize |
| E | computational_models (확정 모델만); mechanism 은 B8m/B-요약 가 primary. **same-job**(모델이 그 가설 현상에 쓰일 때만 C) |
| F | method rerank(약) |

> 핵심: **genuine 연결(튜플 anchor) admits · H vetoes · spurious(단어만 겹침)
> 차단 · 나머지는 rerank.** 목표는 *좁히기*가 아니라 *연결 정확도*다 — 다른 도메인·
> 종 논문도 진짜 연결되면 추천하고, 허울뿐인 연결만 거른다. 설문은 그 판정에 필요한
> 구체성을 모은다. (MSY 등 미인터뷰자는 **빈 설문**으로 시작.)
