# CSNL paper-archive interview — 연구원 가이드

연구실 paper archive 인터뷰 플러그인 사용법. **운영자 (vnilab@gmail.com)
에게서 받은 Supabase 비밀번호만 있으면 작동**합니다.

---

## 0. 한 줄 요약

설치 + 비밀번호 셋업 (한번) → 시간 날 때 `/csnl-paper-archive-interview:paper-interview <본인 init>` 실행 → 객관식 답변 → 시스템이 본인 응답을 학습해 다음 추천 갱신.

---

## 1. 설치 (3 단계, ~2 분)

### Step 1. Claude Code 세션에서 플러그인 설치

```
/plugin marketplace add CSNL-vnilab/csnl-paper-rec-v2
/plugin install csnl-paper-archive-interview@csnl-marketplace
```

(이전 버전 설치 이력 있으면 먼저 `/plugin uninstall csnl-paper-archive-interview@csnl-marketplace`)

#### 기존 사용자 — 새 버전으로 업데이트

`/plugin list` 에서 보이는 버전이 현재 (`0.5.1`) 보다 낮으면 다음 3 줄로
갱신하세요. **DB 응답 기록 (`archive_responses`) 은 절대 영향을 받지
않습니다** — 본인이 이전에 매긴 read / to_read / not_interested 라벨이
그대로 보존되고, 큐는 응답하지 않은 paper 부터 이어갑니다.

```
/plugin marketplace update csnl-marketplace
/plugin uninstall csnl-paper-archive-interview@csnl-marketplace
/plugin install csnl-paper-archive-interview@csnl-marketplace
```

업데이트 후 `/plugin list` 로 `csnl-paper-archive-interview@0.5.1` 확인 →
인터뷰 재개 (`/csnl-paper-archive-interview:paper-interview <본인 init>`).

**0.5.0 → 0.5.1 변경 요약** (자세한 내역은 본 repo `CLAUDE.md` 의 P22c
entry):
- **P22c**: 시놉시스 corpus 가 1,205 → 2,063 편으로 확장되었습니다 (in-scope
  1,324 + out-of-scope 739; out-of-scope 는 인터뷰 큐에서 자동 제외).
  cwll_rec_log + pi_network + classics 통합 풀에서 abstract 가 있는 모든
  논문을 커버하므로 Stage 2 한국어 추천 사유의 grounding 적용 비율이
  사실상 100% 에 근접합니다.
- **Block 2 가이드 강화**: lab CURRENT scope (efficient coding / Bayesian
  observer / RL / evidence accumulation) 가 아닌 scope-adjacent (Theory of
  Mind / Higher-order consciousness / 컴퓨테이셔널 정신의학 / 신경경제학)
  논문이 큐에 surface 되었을 때, 본인 프로젝트와 명시적 연결이 불가능하면
  강제로 Block 2 를 생성하지 않고 Block 3 (uncertainty branch) 로 분기 —
  거짓 연결 차단.

**0.4.x → 0.5.0 변경 요약** (이전 release):
- **P21**: 1,205 편의 framework-agnostic 시놉시스가 DB 에 적재되어,
  Stage 2 의 "왜 이 paper 인가" 한국어 추천 사유가 본 paper 의 핵심 질문 +
  실제 발견 + 사용 framework 와 본인 프로젝트의 매칭으로 grounding 됩니다.
- **P22**: 4번 옵션 "더 자세히 소개해줘" 가 제거되어 3 지선다 (저장 /
  관련 없음 / 이미 읽음) 로 단순화.

기존 응답 분포는 `python3 ~/.claude/plugins/cache/csnl-marketplace/csnl-paper-archive-interview/*/scripts/doctor.py --init <본인 init>` 가 그대로 출력합니다.

### Step 2. 실제 터미널 (Claude Code 채팅 아님) 에서 의존성 + 셋업

비밀번호 입력은 보안상 실제 터미널이 필요합니다.

**한 번에 의존성 설치 (충돌 방지):**
```
python3 -m pip install --user --upgrade -r ~/.claude/plugins/cache/csnl-marketplace/csnl-paper-archive-interview/*/requirements.txt
```

**그다음 셋업 스크립트 실행:**
```
python3 ~/.claude/plugins/cache/csnl-marketplace/csnl-paper-archive-interview/*/scripts/setup.py
```

스크립트가:
- Python 버전 확인 (3.8+ 필요; 더 낮으면 brew install 안내)
- psycopg2 (Postgres 드라이버) 검증 — 위 의존성 설치로 이미 설치됨
- 5 가지 값 묻기 (HOST/USER 는 기본값 자동 채움, PASSWORD 만 입력)
- `~/.csnl-paper-archive/.env` 에 chmod 600 으로 저장
- 본인 init 알려주면 즉시 연결 테스트 진행

### Step 3. 진단 (선택, 권장)

문제 생기면 doctor 가 정확한 위치를 알려줍니다.

```
python3 ~/.claude/plugins/cache/csnl-marketplace/csnl-paper-archive-interview/*/scripts/doctor.py --init <본인 init>
```

→ Python 버전 / psycopg2 / .env 위치 / DB 연결 / 본인 큐 존재 여부를 모두 점검하고 실패 시 한글 안내 출력.

---

## 2. 인터뷰 진행 (Claude Code 세션)

```
/csnl-paper-archive-interview:paper-interview <본인 init>
```

(`<본인 init>` = JOP / BHL / BYL / JYK / MSY / SMJ / SYJ)

진행 단계:

| 단계 | 무엇을 묻나 | 본인이 해야 할 일 |
| --- | --- | --- |
| **Stage 0** | 환경 점검 + 짧은 오리엔테이션 | 그냥 읽기 |
| **Stage 1.1** | 현재 주목 주제 정리 (csnl_research 자동 추출) | "맞아요" 또는 잘못된/빠진 항목 알려주기 |
| **Stage 1.2** | 방법론 정리 | 동일 |
| **Stage 1.3** | (프로젝트 ≥ 2개일 때) 프로젝트 비중 | 합 100 으로 N개 숫자 (예: `70/20/5/5`) |
| **Stage 1.4** | 차원 선호 자동 추출 결과 확인 | (a) 좋아요 / (b) 한두 개 수정 / (c) 다시 |
| **Stage 2** | 한 번에 한 편씩 paper 소개 + MCQ | 1 / 2 / 3 중 하나 답변 |
| **Stage 4** | 매 10편마다 자동 belief 업데이트 + 한글 요약 | 그냥 읽기, 변경안에 "네/아니요" |
| **Stage 5** | 모든 큐 소진 시 wrap-up | 자동 종료 |

세션 중간에 닫아도 됩니다. 다시 `/paper-interview` 호출 시 이어서 진행.

---

## 3. 3 지선다 답변 가이드 — 진화에 이로운 응답

매 paper 마다 3 가지 선택 (2026-05-28 부터 — 이전 4 옵션 "더 자세히" 는
P21 synopsis 기반 Block 2 가 본문 인용까지 포함하므로 제거):

| 번호 | 의미 | 시스템이 학습하는 신호 |
| --- | --- | --- |
| **1** | 나중에 읽을 리스트에 추가 | 이 paper 의 dim_tags + 키워드 → 본인 선호 prior 가 **+0.2 강화**. 다음 추천에 비슷한 paper 우선순위 ↑. |
| **2** | 내 연구와 관련 없음 + 한 문장 이유 | 이유에 부정 토큰 (`관련 없`, `내 연구 아`, `다른 결`) 포함 시 dim_tags → **−0.3 약화**. 이유 없으면 무시. |
| **3** | 이미 읽었음 + 한 문장 활용 맥락 | "이미 알고 있는 영역" 으로 표시. 가중치 변경 없음 (선호도 신호 아님). 큐 novelty 지표만 누적. |

**진화에 가장 이로운 답변 패턴:**

1. **2번 (관련 없음) 선택 시 *반드시* 한 문장 이유 작성.** "방법은 비슷한데 자극이 너무 다름" 같은 구체적 이유가 강한 신호. 이유 안 쓰면 시스템이 응답을 무시 — 본인 시간 낭비.

2. **3번 (이미 읽음) 사용 시 활용 맥락 적어주기.** "이전 manuscript motivation 으로 인용" 같은 정보가 본인 연구 *맥락* 신호 — 다음 추천에서 이 paper 와 *비슷하지만 새로운* 것 우선시.

3. **응답률 ≥ 80% 유지.** skipped 비율이 높으면 belief 업데이트 약해짐. 1, 2 만으로도 충분.

4. **Stage 4 변경안 검토.** 매 10번째 응답 후 시스템이 "최근 패턴 + 다음 추천 방향" 한글 2문장 보여줌. 동의/거부가 belief 업데이트의 가장 큰 레버.

---

## 4. 업데이트 주기

| 주기 | 누가 | 무엇 |
| --- | --- | --- |
| **실시간** | 자동 | 응답 즉시 `archive_responses` 기록. 다음 paper 가 새 prefs 반영 (P17 in-session re-rank). |
| **매 10 응답** | 자동 | Stage 4 belief 업데이트: dim weights 조정, `archive_meta_reviews` 기록, 다음 paper 부터 즉시 반영. |
| **월 1회** | 운영자 | `archive-feedback-analyst` (P20 예정) 가 응답 + queue feedback 분석 → 제안 (새 키워드 / 가중치 / 큐 확장). 모든 변경은 운영자 검토 후 `apply_evolution.py` 로 적용. 자동 적용 없음. |
| **분기 1회** | 연구원 + 운영자 | retrospective 설문 5분: 지난 3개월 추천을 (a) 저장만 / (b) 일부 읽음 / (c) 완독 / (d) 인용 / (e) 논문 작성 활용 했는지. **비순환적 ground truth** — 알고리즘 정성 평가 기준. |
| **수시** | 운영자 | csnl_research.projects 업데이트 (본인이 자가-아카이브로 새 프로젝트 추가 등) → 다음 큐 빌드에서 fingerprint 자동 재추출. |

운영자 수동 작업이 필요한 경우:
- 새 프로젝트 시작 → CSNL 자가-아카이브 툴로 `csnl_research.projects` 업데이트
- 큐 고갈 (200 편 모두 응답) → 운영자가 OpenAlex 재인제스트 + 큐 재빌드
- `mcq_precision_30d` < 45% → 운영자가 알림 받고 fingerprint 재구성 검토

---

## 4-bis. DB 가 무엇을 누적하나

매 MCQ 답변이 `csnl_paper_rec.archive_responses` 에 영구 저장됩니다. 운영자
측 `archive_paper_status` view + `list_status.py` CLI 를 통해 본인의 paper
별 상태를 plain-Korean 으로 조회할 수 있습니다:

```
$ python3 scripts/archive/list_status.py <본인 init>

JOP — 총 20편 응답
  이미 읽음   (read)               n=2
  읽을 예정   (to_read)            n=12
  관심 없음   (not_interested)     n=6
```

MCQ 답변 → 상태 매핑 (2026-05-28 부터 3-MCQ):
- `1` (저장) → **읽을 예정** (to_read)
- `2` (관련 없음) → **관심 없음** (not_interested) — 추후 추천에서 비슷한 paper 자동 배제
- `3` (이미 읽음) → **이미 읽음** (read)

데이터는 영구 보존됩니다 — 같은 paper 가 다시 나오지 않고, "관심 없음"
영역과 비슷한 paper 도 추천 우선순위에서 자동으로 내려갑니다.

(이전 4번 옵션 "더 자세히 소개해줘" 는 2026-05-28 에 제거되었습니다.
P21 synopsis 가 Block 2 의 짧은 한국어 소개에서 충분한 맥락을 제공하므로
별도 deep-dive 가 필요 없어졌습니다.)

---

## 5. 자주 발생하는 문제

| 증상 | 원인 | 해결 |
| --- | --- | --- |
| `Unknown command: /paper-interview` | namespace 없이 호출 | `/csnl-paper-archive-interview:paper-interview <init>` |
| `/plugin list` 비어 있음 | install 실패 / 옛 버전 캐시 | `/plugin uninstall ... && /plugin marketplace update csnl-marketplace && /plugin install ...` |
| `/plugin list` 가 옛 버전 (예: 0.3.x / 0.4.x) | marketplace 캐시 만료 | 위 "기존 사용자 — 새 버전으로 업데이트" 3 줄 sequence 재실행. 그래도 옛 버전이면 `rm -rf ~/.claude/plugins/cache/csnl-marketplace` 후 install. |
| 4번 옵션 (더 자세히) 가 안 보임 | 정상 — 2026-05-28 부터 3 지선다 (1/2/3) | 의도된 동작입니다. 자세한 내역: 본 가이드 § 3. |
| `Supabase 연결 정보 .env 파일이 없어요` | 첫 셋업 안 됨 | terminal 에서 `setup.py` 실행. doctor.py 가 정확한 경로 안내. |
| `Supabase 연결 실패` | .env 값 잘못 | `setup.py --force` 또는 `~/.csnl-paper-archive/.env` 직접 편집 + 운영자 확인 |
| `활성 프로젝트가 csnl_research 에 없습니다` | 본인 프로젝트가 confidence ≥ 0.7 미만 | CSNL 자가-아카이브 툴로 프로젝트 메타데이터 먼저 업데이트 |
| `추천 큐가 아직 생성되지 않았습니다` | 운영자가 본인 init 큐 미빌드 | 운영자에게 `! python scripts/archive/build_researcher_queue.py <YOUR_INIT> --apply` 요청 |
| 한글 깨져 보임 (`F-EFC` 코드 노출) | taxonomy 로딩 실패 | Claude Code 세션 재시작. 계속되면 운영자에게 보고. |
| psycopg2 import 안 됨 | pip install 누락 | `python3 -m pip install --user psycopg2-binary`. `psql` CLI 가 PATH 에 있어도 fallback. |
| Python 3.7 이하 | macOS Catalina / 일부 Linux | `brew install python@3.11` 후 셸 재시작; pyenv 도 가능 |

**막혔다면**: terminal 에서 `doctor.py --init <본인 init>` 가 모든 점검 결과 + 한글 안내 출력.

---

## 6. 보안 + 경계

- 비밀번호는 `~/.csnl-paper-archive/.env` 에 `chmod 600` 으로 저장 (본인만 읽기 가능).
- 플러그인 `_pdb.py` 가 **4 개 테이블에만 쓰기 허용** (interview_sessions / profile_verifications / responses / meta_reviews). 본인 응답 외 어떤 DB 변경도 발생하지 않습니다 — 코드 레벨 + DB 역할 레벨 양쪽 강제.
- 본 인터뷰는 **읽기 + 본인 응답 기록 only**. Slack DM, 이메일, 외부 메시지 절대 전송 안 됨.
- 본인 외 다른 연구원의 응답 / 큐는 본인 클라이언트에서 안 보임 (`pick_next.py` 가 `WHERE researcher_id = <본인>` 강제).
- 본인 응답이 다른 연구원의 큐를 변경하지 않음 (월간 evolution 도 ≥2 연구원 합의 신호 + 운영자 게이트 필요).
- (2026-05-28 부터 옵션 4 "더 자세히" 는 제거되었습니다. 본문 deep-dive
  대신 Block 2 한국어 추천 사유에 synopsis 의 핵심 발견과 framework 매칭이
  포함됩니다.)

---

## 7. 운영자 연락처

문제 / 질문 / 새 키워드 제안: **vnilab@gmail.com**.

운영자 측 전체 시스템 설명: 본 repo 의 `docs/HARNESS-ARCHIVE-DESIGN.md`,
`docs/HARNESS-ALGORITHM-DESIGN.md`, `docs/SHARED-DEPLOYMENT.md`.
