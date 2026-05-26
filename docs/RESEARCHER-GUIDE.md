# CSNL paper-archive interview — 연구원 가이드

연구실 paper archive 인터뷰 플러그인 사용법. **운영자 (vnilab@gmail.com)
에게서 받은 Supabase 비밀번호만 있으면 작동**합니다.

---

## 0. 한 줄 요약

설치 + 비밀번호 셋업 (한번) → 시간 날 때 `/csnl-paper-archive-interview:paper-interview <본인 init>` 실행 → 객관식 답변 → 시스템이 본인 응답을 학습해 (a) 매주 화요일 자동 추천 batch 생성, (b) 수요일 Paper Blitz 발표 후보 자동 배정, (c) 다른 Claude 세션도 본인 연구 맥락을 1초 안에 priming.

### 4개 슬래시 명령

| 명령 | 언제 |
| --- | --- |
| `/csnl-paper-archive-interview:paper-interview <init>` | **메인** — 시간 날 때마다 객관식 인터뷰 |
| `/csnl-paper-archive-interview:paper-weekly <init>`    | **매주** — 이번 주 추천 top-5 확인 (운영자측 화요일 18:00 KST 자동 생성) |
| `/csnl-paper-archive-interview:paper-blitz <init>`     | **수요일 아침** — 본인이 발표할 Paper Blitz paper 확인 + 5분 발표 준비 도움 |
| `/csnl-paper-archive-interview:paper-context <init>`   | **다른 세션 시작 시** — 본인 연구 맥락 priming (현재 프로젝트 / 어휘 / 선호 / 최근 읽은 paper). 매번 "무슨 연구 하세요?" 다시 묻지 않아도 됨. |

---

## 1. 설치 (3 단계, ~2 분)

### Step 1. Claude Code 세션에서 플러그인 설치

```
/plugin marketplace add CSNL-vnilab/csnl-paper-rec-v2
/plugin install csnl-paper-archive-interview@csnl-marketplace
```

(이전 버전 설치 이력 있으면 먼저 `/plugin uninstall csnl-paper-archive-interview@csnl-marketplace`)

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
| **Stage 2** | 한 번에 한 편씩 paper 소개 + MCQ | 1 / 2 / 3 / 4 중 하나 답변 |
| **Stage 4** | 매 10편마다 자동 belief 업데이트 + 한글 요약 | 그냥 읽기, 변경안에 "네/아니요" |
| **Stage 5** | 모든 큐 소진 시 wrap-up | 자동 종료 |

세션 중간에 닫아도 됩니다. 다시 `/paper-interview` 호출 시 이어서 진행.

---

## 3. 4 지선다 답변 가이드 — 진화에 이로운 응답

매 paper 마다 4 가지 선택:

| 번호 | 의미 | 시스템이 학습하는 신호 |
| --- | --- | --- |
| **1** | 나중에 읽을 리스트에 추가 | 이 paper 의 dim_tags + 키워드 → 본인 선호 prior 가 **+0.2 강화**. 다음 추천에 비슷한 paper 우선순위 ↑. |
| **2** | 내 연구와 관련 없음 + 한 문장 이유 | 이유에 부정 토큰 (`관련 없`, `내 연구 아`, `다른 결`) 포함 시 dim_tags → **−0.3 약화**. 이유 없으면 무시. |
| **3** | 이미 읽었음 + 한 문장 활용 맥락 | "이미 알고 있는 영역" 으로 표시. 가중치 변경 없음 (선호도 신호 아님). 큐 novelty 지표만 누적. |
| **4** | 더 자세히 소개해줘 | 격리된 explainer agent 가 본문 읽고 한글 3 문단 설명. MCQ 재표시. **save_later 의 0.5 배** 약한 긍정 신호. |

**진화에 가장 이로운 답변 패턴:**

1. **2번 (관련 없음) 선택 시 *반드시* 한 문장 이유 작성.** "방법은 비슷한데 자극이 너무 다름" 같은 구체적 이유가 강한 신호. 이유 안 쓰면 시스템이 응답을 무시 — 본인 시간 낭비.

2. **3번 (이미 읽음) 사용 시 활용 맥락 적어주기.** "이전 manuscript motivation 으로 인용" 같은 정보가 본인 연구 *맥락* 신호 — 다음 추천에서 이 paper 와 *비슷하지만 새로운* 것 우선시.

3. **4번 (더 자세히) 자주 활용.** explainer 가 본문에서 직접 인용 + 본인 프로젝트와의 연결을 보여줌. 인용 문장 없으면 "초록만으로 단정 어려움" 명시 — 환각 안 함.

4. **응답률 ≥ 80% 유지.** skipped 비율이 높으면 belief 업데이트 약해짐. 1, 2 만으로도 충분.

5. **Stage 4 변경안 검토.** 매 10번째 응답 후 시스템이 "최근 패턴 + 다음 추천 방향" 한글 2문장 보여줌. 동의/거부가 belief 업데이트의 가장 큰 레버.

---

## 4. 업데이트 주기 — 세션이 능동적으로 진화

기본 원칙: **운영자가 주기적으로 `--apply` 를 누르는 게 아니라, 세션
자체가 응답을 기반으로 추천 방법을 진화시키며 다음 추천 list 를 만든다.**

| 주기 | 무엇이 / 누가 트리거 | DB 상태 변화 |
| --- | --- | --- |
| **응답 즉시** | 본인이 1/2/3/4 누름 | `archive_responses` 에 (researcher, paper, choice) 기록 — 읽음/읽을 예정/관심 없음의 진본 |
| **다음 paper 픽** | `pick_next.py` 자동 | 응답 즉시 반영. 이미 응답한 paper 는 자동 제외. **현재 dim_preferences 로 unanswered 풀 전체 즉석 재랭킹** (P17). 운영자 개입 없음. |
| **매 10 응답** | Stage 4 자동 | belief-updater 가 dim_preferences 갱신 → `archive_profile_verifications` 새 row. 다음 paper 부터 즉시 새 prefs 로 ranking. 운영자 개입 없음. |
| **세션 종료** | 자동 | `archive_interview_sessions.completed_at` 기록. 다음 세션은 같은 row 에서 이어지거나 새 session 시작. |
| **월 1회** | 운영자 (검토 후 적용) | (P20 예정) `archive-feedback-analyst` 가 lab-wide 신호 분석 → lexicon/taxonomy/가중치 진화 제안. **연구원별 큐는 운영자 개입 없이 항상 dynamic** — 이건 lab-wide 알고리즘 진화 차원. |
| **분기 1회** | 연구원 + 운영자 | retrospective 설문 5분 (비순환적 ground truth) |
| **수시** | 본인 | CSNL 자가-아카이브 툴로 `csnl_research.projects` 업데이트 → 다음 fingerprint 추출 시 자동 반영 |

운영자 수동 개입이 필요한 경우 (**드물게만**):
- 초기 설정 시 **한 번**: `build_researcher_queue.py --all-in-scope` 로 본인 큐에 in-scope 전체 paper 사전 점수 채우기 (~7,500 후보)
- 새 paper 가 archive 에 추가됐을 때 (월 1회 운영자 ingest 후): 본인 큐 갱신
- 본인이 새 프로젝트 시작 시: fingerprint 재추출

평소 매일/매주는 세션이 알아서 진화 — 운영자 개입 0.

## 4-bis. PostgresDB 가 무엇을 누적하나

`csnl_paper_rec.archive_paper_status` view 가 본인의 paper 별 상태를
보여줍니다 (운영자 조회용):

```
$ python3 scripts/archive/list_status.py <YOUR_INIT>

JOP — 총 20편 응답
  읽을 예정              (to_read)            n=12
  관심 없음              (not_interested)     n=6
  이미 읽음              (read)               n=2
  ...
```

매핑:
- `read` (이미 읽음) = MCQ 답변 3번 → 다음 수요일 Paper Blitz 발표 후보 자동 배정
- `to_read` (읽을 예정) = MCQ 답변 1번 (저장)
- `not_interested` (관심 없음) = MCQ 답변 2번 → 추후 추천에서 비슷한 paper 자동 배제
- `maybe_interested` (더 알아볼 만함) = MCQ 답변 4번

이 데이터는 영구 보존되며 다음 추천 시 자동 활용 — 같은 paper 가 다시
나오지 않고, 본인이 "이미 읽음" 표시한 영역의 *다른* paper 가 우선됩니다.

## 4-tris. Wednesday Paper Blitz (5분 저널클럽) 연동

매주 수요일 오전 연구실 Paper Blitz 에서 각자 지난 1주간 본인이 "이미 읽음"
으로 표시한 paper 중 하나를 5분간 발표 + discuss 합니다.

운영자측 cron 이 매주 화요일 18:00 KST 에 자동으로:
1. 각 연구원의 지난 7일 `already_read` 응답을 조회
2. 그 중 가장 composite score 가 높은 paper 를 본인 발표 슬롯으로 배정
3. `archive_paper_blitz` 에 영구 기록

수요일 아침에 `/csnl-paper-archive-interview:paper-blitz <init>` 실행하면:
- 본인의 발표 paper 가 출력됨
- 원하시면 Claude 가 5분 발표 outline (핵심 주장 / 방법 / 결과 / 본인 연구와의 연결) 자동 생성

지난 1주간 새로 읽은 paper 가 없으면 그 주는 발표 없음으로 자동 처리됩니다 —
인터뷰만 꾸준히 하시면 발표 후보는 자연스럽게 누적됩니다.

## 4-quater. 미래 Claude 세션을 위한 retrieval priming

archive 인터뷰의 가장 큰 payoff: 본인이 한 모든 응답이 누적되어, 미래에
연구를 도와줄 어떤 Claude 세션이든 본인 맥락을 1초 안에 불러옵니다.

```
# 어떤 새 Claude Code 세션에서든
/csnl-paper-archive-interview:paper-context JOP

→ JOP 연구원님의 진행 중인 4개 프로젝트, 최근 읽은 paper 12편,
  차원 선호 (F-NIM 강함, M-RSA 강함) 모두 로드했습니다.
  어떤 부분을 도와드릴까요?
```

이게 작동하려면 인터뷰를 **꾸준히** 하셔야 합니다 (시간 날 때 5-10편 단위).
누적 응답 수가 많을수록 priming context 가 정확해집니다.

---

## 5. 자주 발생하는 문제

| 증상 | 원인 | 해결 |
| --- | --- | --- |
| `Unknown command: /paper-interview` | namespace 없이 호출 | `/csnl-paper-archive-interview:paper-interview <init>` |
| `/plugin list` 비어 있음 | install 실패 / 옛 버전 캐시 | `/plugin uninstall ... && /plugin marketplace update csnl-marketplace && /plugin install ...` |
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
- explainer agent (옵션 4): 본인 환경에서는 abstract 만으로 설명 ("전문 본문을 가져오지 못해 초록 기반으로 설명드립니다." 명시).

---

## 7. 운영자 연락처

문제 / 질문 / 새 키워드 제안: **vnilab@gmail.com**.

운영자 측 전체 시스템 설명: 본 repo 의 `docs/HARNESS-ARCHIVE-DESIGN.md`,
`docs/HARNESS-ALGORITHM-DESIGN.md`, `docs/SHARED-DEPLOYMENT.md`.
