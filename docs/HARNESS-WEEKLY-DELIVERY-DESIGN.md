# Weekly Paper Delivery — Design Doc (P23 scaffold)

**Status**: design only · authored 2026-05-29 · ready for new-session implementation
**Target session task**: implement against this doc; no prior context required beyond the repo.

---

## 1. 목적

P13–P22c 인터뷰가 끝난 이후, **매주 정해진 시각에 7 명의 연구원에게 5 편의 논문을 Notion 으로 추천**하고, 연구원이 Notion 상에서 status property 를 바꾸면 `archive_responses` 가 자동 업데이트되어 다음 주 큐가 학습된 선호도로 재랭크되는 자동화 시스템.

기존 인터뷰 인프라 (`archive_paper_synopses`, `archive_researcher_queues`, `archive_responses`, `archive_profile_verifications`) 를 그대로 재사용. 새 컴포넌트는 (1) 정기 발송 트리거, (2) Notion integration, (3) response 폴링.

---

## 2. 시스템 다이어그램

```
┌──────────────────────── PostgreSQL (csnl_paper_rec) ─────────────────────────┐
│                                                                              │
│  archive_paper_synopses   archive_researcher_queues   archive_responses      │
│  (2,063 rows, frozen)     (1,400 rows = 7 × 200)      (영구 truth source)    │
│                                                                              │
│  archive_profile_verifications.dim_preferences (belief, 매 10 답마다 갱신)   │
│                                                                              │
│  + 새 테이블 (P23):                                                          │
│  archive_researcher_channels  · archive_weekly_digests  · archive_pending_*  │
└─────────────────────────┬──────────────────────────────┬─────────────────────┘
                          │ READ                         │ WRITE
                          ▼                              ▼
                ┌─────────────────────┐      ┌──────────────────────┐
                │  build_digest.py    │      │  capture_responses.py│
                │  (매주 월 09:00 KST)│      │  (매 30 분 cron)     │
                │                     │      │                      │
                │  큐 → 정책 적용 →   │      │  Notion DB 조회 →    │
                │  주간 digest 생성   │      │  Status 변경분 →     │
                └──────────┬──────────┘      │  archive_responses   │
                           │ POST            │  UPSERT              │
                           ▼                 └──────────┬───────────┘
                ┌─────────────────────┐                 │ trigger
                │  send_notion.py     │                 ▼
                │                     │      ┌──────────────────────┐
                │  연구원별 row 7개   │      │  belief-updater      │
                │  (5 편 × 7 = 35 행) │      │  (매 10 응답 cum.)   │
                │  in "이번 주 추천"  │      └──────────────────────┘
                │  Notion database    │
                └─────────────────────┘
                           │
                           ▼
              ┌──────────────────────────┐
              │  Notion workspace        │
              │                          │
              │  CSNL 논문 추천 (parent) │
              │    └─ 이번 주 추천 (DB)  │
              │         ├─ row 1 (BHL #1)│
              │         ├─ row 2 (BHL #2)│
              │         ├─ ...           │
              │         └─ row 35 (SYJ#5)│
              │                          │
              │  연구원이 본인 init 필터│
              │  적용 → Status drop-down│
              │  변경 (저장/관련없음/   │
              │  이미읽음)              │
              └──────────────────────────┘
```

---

## 3. 결정된 사항 (defaults — implement first, tunable later)

| Decision | Default | Rationale |
|---|---|---|
| Channel | **Notion only** (Slack 미사용) | Internal Integration Token + DB-row 가 가장 단순; 폴링으로 충분 |
| 주간 발송 시각 | **매주 월요일 09:00 KST 일괄** (전 연구원 동시) | scheduling 단순; 연구원별 시차 도입은 추후 |
| 편/주/researcher | **5 편** | UI 부담 적정선 |
| Tier 분포 강제 | **S 1 편 + A 2 편 + B 2 편** | 다양성 + top-quality 균형 |
| Chunk 균형 (recent/mid/classic) | **3 : 1 : 1** (recent 3 / mid 1 / classic 1) | 최근 연구 우선; classic 1 편 확보로 fundamental coverage |
| 재추천 cooldown | **8 주** (응답 없이 expire 후 재진입 가능) | 한 번 무응답 ≠ "관련 없음" 가정 |
| 응답 capture | **30 분 폴링** (`capture_responses.py`) | Notion enterprise webhook 미사용; 폴링이 표준 |
| 무응답 처리 | **다음 주 발송 직전 `expired` 라벨** + cooldown 시작 | archive_responses 에는 기록 안 함 (영구 truth source 보존) |
| Belief update 트리거 | **응답 누적 10 회마다** (기존 P17 로직 그대로) | 새 응답 path 도 cumulative count 에 반영 |
| 새 paper ingest | **없음 — 현 2,063 편 풀 고정** | P23 scope 밖; 추후 P24 |
| 보안 게이트 | **(c) hybrid — 기존 7 명 자율, 신규 연구원 등록 시 운영자 grant** | 자동화 가치 + 신규 연구원 safety |
| 발송 dry-run | **첫 4 주 동안 운영자 매주 일요일 dry-run preview** → 4 주 후 자율 | 정책 안정화 기간 |

---

## 4. 새 DB 테이블 (csnl_paper_rec.archive_*)

### 4-1. `archive_researcher_channels`

```sql
CREATE TABLE csnl_paper_rec.archive_researcher_channels (
  researcher_id     varchar(8)   NOT NULL,
  channel_type      varchar(16)  NOT NULL,   -- 'notion' (P23 only)
  channel_target    text         NOT NULL,   -- Notion user ID 또는 database row filter 값
  delivery_dow      smallint     NOT NULL DEFAULT 1,    -- 0=Sun, 1=Mon, ...
  delivery_hour_kst smallint     NOT NULL DEFAULT 9,
  enabled           boolean      NOT NULL DEFAULT true,
  consented_at      timestamptz  NOT NULL,             -- 연구원이 자동 추천 동의한 시각
  PRIMARY KEY (researcher_id, channel_type)
);
```

7 명 모두 동의 후 row 1개씩 (`notion`). 동의 안 한 연구원은 row 없음 → 자동 제외.

### 4-2. `archive_weekly_digests`

```sql
CREATE TABLE csnl_paper_rec.archive_weekly_digests (
  digest_id         bigserial    PRIMARY KEY,
  week_iso          varchar(8)   NOT NULL,    -- '2026-W23'
  researcher_id     varchar(8)   NOT NULL,
  canonical_id      varchar(64)  NOT NULL,
  tier_at_send      char(1)      NOT NULL,    -- 'S' / 'A' / 'B' / 'C'
  rank_in_digest    smallint     NOT NULL,    -- 1..5
  notion_page_id    varchar(64),              -- Notion row ID after POST
  sent_at           timestamptz  NOT NULL,
  response_choice   varchar(16),              -- 'save_later' / 'not_relevant' / 'already_read' / 'expired' / NULL
  response_at       timestamptz,
  UNIQUE (week_iso, researcher_id, canonical_id)
);
CREATE INDEX ON csnl_paper_rec.archive_weekly_digests (researcher_id, response_choice);
CREATE INDEX ON csnl_paper_rec.archive_weekly_digests (notion_page_id);
```

매주 발송된 paper 의 행. `response_choice` 가 `null` 인 동안은 pending; expire 시 `'expired'` 로 마킹.

### 4-3. cooldown 은 별도 테이블 없이 `archive_weekly_digests` view 로 처리

```sql
CREATE VIEW csnl_paper_rec.archive_paper_cooldown AS
SELECT researcher_id, canonical_id, MAX(sent_at) AS last_sent_at
FROM csnl_paper_rec.archive_weekly_digests
WHERE response_choice IN (NULL, 'expired')   -- 응답 안 한 경우만
GROUP BY researcher_id, canonical_id;
```

`build_digest.py` 가 후보 선정 시 `last_sent_at < now() - interval '8 weeks'` 인 paper 만 포함.

---

## 5. Notion workspace 구조

운영자가 수동으로 1회 셋업:

```
[Workspace]
└─ Page: "CSNL 논문 추천" (Integration "claude-paper-rec" 와 share)
   ├─ Page: "이번 주 추천" (database)
   │    Properties:
   │      - Title (text)               ← APA 인용 1줄
   │      - Researcher (Select)        ← BHL / BYL / JOP / JYK / MSY / SMJ / SYJ
   │      - Week (Text)                ← 2026-W23
   │      - Tier (Select)              ← S / A / B / C
   │      - Status (Status property)   ← 보낼 때 default: 미응답 / 응답 옵션: 📚저장 / ❌관련없음 / ✅이미읽음
   │      - Recommendation (Rich text) ← Block 2 (한국어 추천 사유)
   │      - DOI (URL)                  ← 클릭하면 paper 원문
   │      - Sent At (Date)
   │      - canonical_id (Rich text)   ← internal — 운영자 인덱스용
   │
   └─ Page: "읽은 / 읽을 논문 (누적)" (database — read-only view)
        Filtered view per researcher; archive_weekly_digests 의 응답 완료 행 미러
```

운영자가 Notion 측에서 **2개 database 를 미리 생성** + Integration 에 share 한 뒤,
`.env` 에 `NOTION_DIGEST_DB_ID`, `NOTION_HISTORY_DB_ID` 를 적어 둠.

---

## 6. 새 스크립트 (scripts/weekly/)

| 스크립트 | 호출 시점 | 책임 |
|---|---|---|
| `build_digest.py` | 월 09:00 KST cron | 연구원별 큐 read → cooldown filter → tier 분포 강제 → 5 편 선정 → `archive_weekly_digests` 행 7×5=35 INSERT (response_choice NULL) |
| `send_notion.py` | `build_digest.py` 완료 후 즉시 chain | 미발송 행 (notion_page_id IS NULL) read → `archive_paper_synopses` JOIN → Notion DB 에 row create → notion_page_id UPDATE |
| `capture_responses.py` | 매 30 분 cron | 발송된 행 (response_choice IS NULL) read → 해당 notion_page_id 의 Status property 조회 → 변경 있으면 `response_choice` + `response_at` UPDATE → 동시에 `archive_responses` 에 UPSERT |
| `expire_pending.py` | 매주 일 18:00 KST cron (월 발송 15 시간 전) | 다음 주 digest 빌드 직전, 이전 주 미응답 행을 `response_choice='expired'` 로 마킹 |
| `dry_run_preview.py` | 매주 일 09:00 KST cron (첫 4 주만; 이후 비활성화) | 내일 발송 예정 digest 를 Slack 운영자 채널로 미리 보내 검토 가능하게 |

**모든 스크립트의 공통 contract:**
- `--apply` 없으면 dry-run; `--apply` 시 DB write
- 환경 변수 `NOTION_API_KEY` (Internal Integration Token), `NOTION_DIGEST_DB_ID`, `NOTION_HISTORY_DB_ID`, Supabase 접속 정보 (기존 그대로)
- `archive_responses` 의 PK (researcher_id, canonical_id) 는 **절대 덮어쓰지 않음**. 새 응답이면 INSERT, 이미 존재하는 응답이면 SKIP (warn log)

---

## 7. cron / launchd 스케줄

`cron/p23-weekly.plist` 1 개 추가 (기존 cron/ 디렉토리 패턴 그대로):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>csnl.paper-rec.p23.weekly</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/csnl/Documents/claude/csnl-paper-rec/scripts/weekly/run_weekly_cron.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Hour</key><integer>0</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Hour</key><integer>0</integer><key>Minute</key><integer>30</integer></dict>
        <!-- ... 30 분 간격 capture_responses 호출 (기존 P14 cron 패턴 참고) -->
    </array>
    <key>StandardOutPath</key><string>/Users/csnl/Documents/claude/csnl-paper-rec/state/cron-p23.log</string>
    <key>StandardErrorPath</key><string>/Users/csnl/Documents/claude/csnl-paper-rec/state/cron-p23.err</string>
</dict>
</plist>
```

쉘 래퍼 `run_weekly_cron.sh`:
- 현재 KST 시각 / dow 확인
- 월 09:00 → `build_digest.py --apply` → `send_notion.py --apply`
- 일 18:00 → `expire_pending.py --apply`
- 그 외 30 분 마다 → `capture_responses.py --apply`
- 일 09:00 (첫 4 주만) → `dry_run_preview.py`

---

## 8. 보안 게이트 / 권한

- 신규 연구원이 자동 추천에 등록되려면 `archive_researcher_channels` 에 row 가 있어야 함. `consented_at` 명시적 동의 기록 필수.
- 운영자가 첫 4 주 일요일 dry-run preview 를 확인 후 GO/NO-GO 결정.
- `archive_responses` 무손상 contract — 자동화 path 가 응답을 덮어쓰지 않음 (`INSERT ... ON CONFLICT DO NOTHING` + warn log).
- Notion Integration Token 은 `.env` 만, repo 에 절대 commit 금지 (`.gitignore` 확인).
- `csnl_research` 는 read-only 유지 — 자동화는 `csnl_paper_rec` 만 write.

---

## 9. Response capture mechanism — Notion polling

Notion API (Free/Pro tier) 에는 property-change webhook 가 없음. **30 분 폴링** 으로 처리.

`capture_responses.py` 흐름:
1. SELECT FROM archive_weekly_digests WHERE response_choice IS NULL AND sent_at > now() - interval '8 weeks'
2. 각 row 의 notion_page_id 에 대해 `GET https://api.notion.com/v1/pages/<id>` 호출
3. Status property 값 read
4. 미응답 (default) → skip; 응답 (저장/관련없음/이미읽음) → :
   a. `archive_weekly_digests.response_choice` UPDATE
   b. `archive_responses` 에 UPSERT (`ON CONFLICT (researcher_id, canonical_id) DO NOTHING`)
   c. 누적 응답 카운트가 10 의 배수 진입 시 `belief-updater` 발동

`belief-updater` 는 기존 P22 agent 그대로 호출. 큐는 다음 주 `build_digest.py` 가 새 `dim_preferences` 로 재랭크.

---

## 10. 새 세션 입력 (implementation directives)

새 세션에서 첫 메시지로 운영자가 보낼 내용:

> "P23 implement. Read `docs/HARNESS-WEEKLY-DELIVERY-DESIGN.md` and `CLAUDE.md` P22c entry, then implement weekly delivery per spec. Notion integration token already in `.env` (NOTION_API_KEY); test on a fresh Notion database I'll create. Operator will manually create the 'CSNL 논문 추천' page + 2 child databases first."

**Implementation 순서:**

1. **DB migrations** — `state/migrations/2026-XX-XX_p23_weekly.sql` 작성 (4-1, 4-2, 4-3 테이블/뷰). Idempotent.
2. **Migration runner** — `scripts/run_migration.py` 가 이미 있으므로 그대로 호출 (운영자 grant 후).
3. **운영자 셋업 — 사람 작업**:
   - Notion: "CSNL 논문 추천" page + "이번 주 추천" + "읽은/읽을 논문 (누적)" database 생성. Integration 에 share.
   - `.env` 에 `NOTION_API_KEY` + `NOTION_DIGEST_DB_ID` + `NOTION_HISTORY_DB_ID` 추가.
   - 7 명 연구원 동의 받고 `archive_researcher_channels` 에 row 7 개 INSERT (수동 SQL 또는 setup script).
4. **scripts/weekly/build_digest.py** — § 6 contract 따라 작성.
5. **scripts/weekly/send_notion.py** — Notion API client (직접 requests 또는 `notion-client` pip package).
6. **scripts/weekly/capture_responses.py** — 폴링 + UPSERT.
7. **scripts/weekly/expire_pending.py** — cooldown 마킹.
8. **scripts/weekly/dry_run_preview.py** — 첫 4 주만 활성화될 운영자 알림.
9. **scripts/weekly/run_weekly_cron.sh** + **cron/p23-weekly.plist** — § 7.
10. **End-to-end smoke test** (운영자가 수동 trigger): `build_digest.py --apply` → Notion 에 7×5 row 생긴 것 확인 → Status 1 개 수동 변경 → `capture_responses.py --apply` → `archive_responses` 에 row 1 개 들어간 것 확인.

---

## 11. Acceptance criteria

- [ ] 4 개 새 테이블/뷰 모두 idempotent migration 통과.
- [ ] `build_digest.py --apply` 가 7 × 5 = 35 행을 `archive_weekly_digests` 에 INSERT (tier 분포 SAB×3 강제, recent:mid:classic = 3:1:1 강제, cooldown 8 주 강제).
- [ ] `send_notion.py --apply` 가 Notion database 에 35 행 create, `notion_page_id` 채움.
- [ ] `capture_responses.py --apply` 가 Status 변경된 행을 detect 하고 `archive_responses` 에 UPSERT (충돌 시 INSERT 안 함, warn log).
- [ ] `expire_pending.py --apply` 가 이전 주 미응답을 `'expired'` 로 마킹, `archive_responses` 는 touch 안 함.
- [ ] 첫 4 주 동안 운영자 일요일 dry-run preview 활성화; 4 주 후 자동 비활성화.
- [ ] codex adversarial review 1 회 통과 (특히: archive_responses 무손상, dim_preferences 동기화, cooldown 계산 정확성, Notion API rate-limit 처리).
- [ ] CLAUDE.md 에 P23 entry append.

---

## 12. 운영자 1회 셋업 작업 (Implementation 시작 전 완료 필요)

1. Notion workspace 에 page 생성:
   - **CSNL 논문 추천** (parent)
   - 그 child 로 **이번 주 추천** database (§ 5 의 properties 정확히)
   - 그 child 로 **읽은 / 읽을 논문 (누적)** database

2. 두 database 모두 Integration `claude-paper-rec` 와 share (Connections 메뉴).

3. Database ID 2 개 추출 (URL `https://www.notion.so/<workspace>/<DB_ID>?v=...` 에서 `<DB_ID>` 부분, 32 자 hex).

4. `.env` 에 추가:
   ```
   NOTION_API_KEY=ntn_xxxxxxxx (이미 있음)
   NOTION_DIGEST_DB_ID=<이번 주 추천 DB ID>
   NOTION_HISTORY_DB_ID=<읽은/읽을 누적 DB ID>
   ```

5. 7 명 연구원에게 동의 메시지 발송 + `archive_researcher_channels` row INSERT.

6. 새 세션 시작 — 첫 입력은 § 10 의 인용 메시지.

---

**문서 종료.** 새 세션은 이 문서만 읽고 § 10 의 순서대로 구현 가능. 모든 design 결정 사항은 § 3 에 명시됨; default 외 다른 값 원하면 운영자가 해당 row 만 override 후 implementation 시작.
