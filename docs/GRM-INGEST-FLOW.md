# GRM/PaperBlitz 자료 적재 흐름 (P31)

매주 수요일 오후, NAS 의 `GRM/2026/<YYYYMMDD>/` 새 폴더를 감지해
PaperBlitz·GRM 슬라이드 파일을 Supabase(`csnl_paper_rec`)로 적재하는 자동화.

```
매주 수 13:00  GRM 종료, 발표자/연구원이 슬라이드를 NAS 에 업로드
        ↓
   수 15:00 KST  launchd → run_grm_ingest.sh (.GRM_INGEST_ENABLED 게이트)
        ↓
   ingest_grm_nas.py --weeks 4 --apply
        ↓  NAS 스캔(read-only) → 파일 분류 → (선택)Notion 스케줄 enrich
   archive_meeting_materials  (슬라이드 파일 인덱스, UNIQUE(nas_path))
        ↓
   (추후, 별도) Claude MCP 세션이 발표 요약을 작성
        ↓  upload_meeting_summary.py --apply
   archive_meeting_summaries  (PB: 연구원×논문 / GRM: 발표자 요약)
```

자료 인덱스(누가/언제/무슨 파일)와 요약(내용)은 두 테이블로 분리된다.
인덱스는 cron 이 자동 채우고, 요약은 나중에 Claude MCP 가 채운다.

---

## 1. 대상 테이블

마이그레이션: `state/migrations/2026-06-12_p31_meeting_materials.sql`
(운영자 1회 실행, idempotent)

```
! python3 scripts/run_migration.py state/migrations/2026-06-12_p31_meeting_materials.sql
```

- **`archive_meeting_materials`** — 슬라이드 파일 1개당 1행. `UNIQUE(nas_path)`
  로 재스캔이 중복을 만들지 않는다(idempotent upsert).
- **`archive_meeting_summaries`** — 발표 요약 공간.
  `UNIQUE(meeting_date,kind,author_initial,paper_ref)`.

두 테이블 모두 `csnl_paper_rec` 스키마에만 생성된다.

---

## 2. 파일명 규칙 · 분류 (`ingest_grm_nas.py`)

폴더: `/Volumes/CSNL_new-1/GRM/2026/<YYYYMMDD>/` (환경변수 `GRM_NAS_BASE` 로 override).
`meeting_date` = 폴더명 `YYYYMMDD`. `file_format` = 확장자(pdf/pptx/ppt/key).

| kind | 규칙 (대소문자 무시) | 예시 | presenter |
|------|---------------------|------|-----------|
| `pb` | 파일명이 `PB` 로 시작 **또는** `paperblitz`/`paper blitz` 포함 | `PB.pptx` · `PB (1).pptx` · `PB_260610.pptx` · `PB_20260527.pptx` · `260506_PaperBlitz_SK_Carricarte2025.pdf` | NULL (랩 전체) |
| `grm` | 파일명에 `GRM` 포함 **또는** `<이니셜>_<날짜>.{pdf,pptx,key}` 패턴 | `BYL_GRM(26.05.06).pptx` · `JSL_260512.key` · `260520 GRM_MSY.pptx` · `MinJin_GRM260526.pdf` · `260610 GRM.pptx` · `260610_GRM_BHL.pdf` | 이니셜 추출 |
| `focus_grm` | Notion Type=`Focus GRM` 인 날짜의 grm 행 (스케줄 도달 시에만) | — | grm 과 동일 |

**presenter 추출(grm)**: 파일명에서 알파벳 토큰을 뽑아 키워드(GRM/PB/확장자) 제거 →

- 알려진 이니셜(`BHL/BYL/JOP/JYK/MSY/SMJ/SYJ/SK/JSL`) → `presenter_initial` 세팅
  (+ `presenter_name` = 등록부 한글명).
- 알려지지 않은 토큰·로마자 이름(`MinJin`) → `presenter_name` 만, `presenter_initial`
  = NULL, `matched_schedule=false` (운영자/Notion 이 해소).
- presenter 토큰 없음(`260610 GRM.pptx`) → 둘 다 NULL.
- 파일-노이즈 토큰(`Review`/`Final`/`Revised`/`Draft`/`Copy`/`Version` 등)은
  presenter 후보에서 제외 — `Review_GRM.pdf` 가 엉뚱한 `presenter_name='Review'`
  를 만들지 않는다(노이즈만 남으면 둘 다 NULL).

**스킵**: `~$…`(Office 락) · `._…`/`.…`(맥 메타/숨김) · 폴더 아닌 것 ·
확장자가 `{pdf,pptx,ppt,key}` 밖인 것 · PB/GRM 어느 쪽도 아닌 것.

분류 예시 매핑(전부 처리됨):

| 파일명 | kind | presenter_initial | presenter_name |
|--------|------|-------------------|----------------|
| `PB.pptx` | pb | NULL | NULL |
| `260506_PaperBlitz_SK_Carricarte2025.pdf` | pb | NULL | NULL |
| `BYL_GRM(26.05.06).pptx` | grm | BYL | 이보연 |
| `JSL_260512.key` | grm | JSL | 임재섭 |
| `260520 GRM_MSY.pptx` | grm | MSY | 여민수 |
| `260610_GRM_BHL.pdf` | grm | BHL | 이보현 |
| `MinJin_GRM260526.pdf` | grm | NULL | MinJin |
| `260610 GRM.pptx` | grm | NULL | NULL |

---

## 3. Notion 스케줄 enrich (선택 · graceful)

권위 스케줄 = Notion DB **'GRM 발표 순번 리스트'**
(data source `4088bc86-a8e3-4386-8f3e-fee006563a0d`, 부모 'CSNL GRM' 페이지
`3022a38e-4f5f-801f-b89b-dd7226873640`). 컬럼: `날짜`(date) · `Type`(select
GRM|Focus GRM) · `발표자 이름`(formula) · `발표 순번`(number).

날짜로 매칭해 grm 행의 `presenter_name`/`schedule_order`/`schedule_type` 보강,
`Type=Focus GRM` 이면 `kind`→`focus_grm`, `matched_schedule=true`.

**통합토큰이 'CSNL GRM' 에 접근 못 할 수 있다.** `NOTION_API_KEY` 부재 또는
403(미공유)/API 오류 시 한 줄 로그 후 **조용히 건너뛰고 NAS-primary 로 진행**
(`matched_schedule=false`). `--no-notion` 으로 enrich 자체를 끌 수 있다.

**enrich 활성화(운영자 수동)**: Notion 에서 'CSNL GRM' 페이지(또는 'GRM 발표 순번
리스트' DB)를 weekly 통합(`NOTION_API_KEY` 의 워크스페이스 통합)에 **Share** 한다
(Share → Add connections → 해당 integration). DB ID 가 다르면
`NOTION_GRM_SCHEDULE_DB_ID` 로 override.

---

## 4. 운영 명령

```bash
# 자료 인덱스 — dry-run (기본): 미리보기 + JSONL 만, DB write 0, NAS read-only
python3 scripts/weekly/ingest_grm_nas.py                 # 최근 4주
python3 scripts/weekly/ingest_grm_nas.py --weeks 8       # 최근 8주
python3 scripts/weekly/ingest_grm_nas.py --date 20260610 # 단일 폴더
python3 scripts/weekly/ingest_grm_nas.py --no-notion     # 스케줄 enrich 생략
#   → state/archive/_tmp/grm_materials_preview.jsonl (DB 미연결에서도 동작)

# 적재 — 운영자(.env DB creds + 마이그레이션 완료 필요)
! python3 scripts/weekly/ingest_grm_nas.py --weeks 4 --apply

# 발표 요약 업로드 (추후 Claude MCP) — dry-run 기본
python3 scripts/weekly/upload_meeting_summary.py --file summaries.json
! python3 scripts/weekly/upload_meeting_summary.py --file summaries.json --apply
#   flat 인자도 가능:
! python3 scripts/weekly/upload_meeting_summary.py \
    --date 20260610 --kind grm --author BHL \
    --title "..." --summary "발표 요약 ..." --apply
```

요약 JSON 한 항목 키: `date|meeting_date` · `kind`(pb|grm|focus_grm) ·
`author|author_initial` · `paper_ref`(PB=논문 APA/제목/DOI, GRM=NULL) ·
`title` · `summary|summary_text` · `keywords`(list 또는 콤마) ·
`material_id`/`material_nas_path`(슬라이드 연결) · `source`(기본 `claude-mcp`).

`upload_meeting_summary.py --apply` 는 **psycopg2-binary 필요**(NULL-key
delete-then-insert 가 psql fallback 으로 표현 불가한 멀티-스테이트먼트 트랜잭션;
dry-run 은 무의존). `--file` 배치 적용 시 유효 항목은 적재되고 무효 형제 항목은
stderr 로 보고(all-or-nothing 아님) — 배치 후 stderr 확인 권장.

---

## 5. 자동화 (cron)

- 래퍼: `scripts/weekly/run_grm_ingest.sh` — `state/.GRM_INGEST_ENABLED` 게이트 +
  once-per-week 가드(`state/grm_last_run_week`) + Wed 15:00 KST 가드 + lockfile.
  ingest 가 **rc=0 일 때만** 주차를 마킹. 비정상 종료는 주차 미마킹 → 다음
  boot/calendar fire 가 재시도.
- ingest 종료 코드: `0` 성공(또는 마운트됐으나 빈 폴더) · `2` 마이그레이션 부재/DB
  미도달 · `3` **NAS 미마운트**(빈 폴더와 구분 — 일시적 unmount 가 그 주를 조용히
  버리지 않도록 주차 미마킹 후 재시도; 어차피 `--weeks 4` catch-up 창으로도 복구).
- plist: `cron/com.csnl.paper-rec.grm.plist` — Weekday 3 / Hour 15 /
  `TZ=Asia/Seoul` / `RunAtLoad`(부팅 catch-up). 로그 `state/cron-grm.log`.

활성화(운영자):

```bash
touch state/.GRM_INGEST_ENABLED
cp cron/com.csnl.paper-rec.grm.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.csnl.paper-rec.grm.plist
```

`.GRM_INGEST_ENABLED` 는 P23 발송 게이트(`.P23_ENABLED`)·v3 cron
(`.CRON_ENABLED`)와 **분리**된 별도 opt-in 이다.

---

## 6. 경계 (불가침)

- **NAS 는 read-only** — 스캔/적재 어느 단계도 공유 폴더에 쓰지 않는다.
- DB write 는 **`csnl_paper_rec`** 한정. `csnl_research`·`csnl_ops`·
  `archive_responses` 는 미수정. `ledger_schema()` 가 csnl_research 쓰기를 거부.
- **dry-run 이 기본**: 미리보기 + JSONL 파일만, DB write 0, DB 미연결에서도 동작
  (precheck/diff 는 DB 가용 시에만). `--apply` 는 운영자 전용(launchd/`!`, .env creds).
- Notion 은 read-only 조회(스케줄 enrich)만. 발송/쓰기 없음.
