# CSNL lab registry — people · NAS · calendars · Supabase

> Source: `~/Downloads/CSNL_info.md` (operator-supplied), normalised to valid YAML and
> extended 2026-07-21 with the **MM**, **PB/GRM** and **Supabase** sections (§3a, §3b, §8).
> This file is the basis for the weekly material-completeness check and its email guidance
> (`scripts/weekly/check_materials.py` + `scripts/weekly/notify.py`).
>
> Machine-readable consumer note: `notify.py` reads addresses from
> `config/researchers.yaml`, which is kept in sync with §2 below.
>
> ⚠ Contains member email addresses — private repo only; do not publish.

```yaml
# 1. PI · 연구실 명칭
pi:
  initial: "SL"
  full_name: "Sang-hun Lee"
  email: "sanghun.lee.vni@gmail.com"
lab:
  name_ko: "인지및시스템신경과학연구실"
  name_en: "Cognitive and Systems Neuroscience Laboratory"
  handle: "csnl"

# 2. 연구원 마스터 (PI 포함, active + alumni)
researchers:
  - { initial: "SL",  full_name: "Sang-hun Lee", role: "pi",      email: "sanghun.lee.vni@gmail.com", status: "active" }
  # Postdocs
  - { initial: "JSL", full_name: "Jaeseob Lim",  role: "postdoc", email: "jasup1883@gmail.com",       status: "active" }
  - { initial: "JHR", full_name: "Juhyoung Ryu", role: "postdoc", email: "jh67753737@gmail.com",      status: "active" }
  # Ph.D. students
  - { initial: "JWL", full_name: "Joonwon Lee",  role: "phd",     email: "jwl89@snu.ac.kr",           status: "active" }
  - { initial: "SK",  full_name: "Sungje Kim",   role: "phd",     email: "ksemperor97@gmail.com",     status: "active" }
  - { initial: "MJC", full_name: "Min Jin Choe", role: "phd",     email: "minjin.choe@gmail.com",     status: "active" }
  - { initial: "JOP", full_name: "Joonoh Park",  role: "phd",     email: "joonop99@snu.ac.kr",        status: "active" }
  - { initial: "SMJ", full_name: "Saemi Jung",   role: "phd",     email: "jsaemi22@gmail.com",        status: "active" }
  - { initial: "JYK", full_name: "Jungye Kim",   role: "phd",     email: "jy061100@gmail.com",        status: "active" }
  - { initial: "BHL", full_name: "Bohyun Lee",   role: "phd",     email: "leebohyun2002@gmail.com",   status: "active" }
  # Master's students
  - { initial: "MSY", full_name: "Minsu Yeo",    role: "master",  email: "mike1224@snu.ac.kr",        status: "active" }
  - { initial: "BYL", full_name: "Boyun Lee",    role: "master",  email: "bolee0755@gmail.com",       status: "active" }
  # ── Alumni (no active material duties; never emailed by the weekly check) ──
  - { initial: "HS",  full_name: "Hansem Sohn",      role: "phd",    email: null, defended_on: "2013", status: "alumni" }
  - { initial: "SHP", full_name: "Soo Hyun Park",    role: "phd",    email: "soohyunpark@kaist.ac.kr", defended_on: "2013", status: "alumni" }
  - { initial: "JK",  full_name: "Jin Young Kim",    role: "phd",    email: null, defended_on: "2022", status: "alumni" }
  - { initial: "HSL", full_name: "Heeseung Lee",     role: "phd",    email: null, defended_on: "2023", status: "alumni" }  # RESTRICTED — 외부 인용 금지
  - { initial: "HJL", full_name: "Hyang-Jung Lee",   role: "phd",    email: null, defended_on: "2024", status: "alumni" }
  - { initial: "JYA", full_name: "Jeong Yeol Ahn",   role: "phd",    email: null, defended_on: "2024", status: "alumni" }
  - { initial: "JWR", full_name: "Jungwon Ryu",      role: "phd",    email: null, status: "alumni" }
  - { initial: "KWC", full_name: "Kyoung Whan Choe", role: "phd",    email: null, status: "alumni" }
  - { initial: "HG",  full_name: "Hyunwoo Gu",       role: "master", email: null, defended_on: "2022", status: "alumni" }
  - { initial: "DGY", full_name: "Dong-gyu Yoo",     role: "master", email: null, defended_on: "2024", status: "alumni" }

# 3. NAS 마운트 / 디렉토리 컨벤션
nas:
  mount_root: "/Volumes/CSNL_new"              # 이름은 마운트 순서에 따라 달라짐 — 아래 주의 참고
  smb_url: "smb://147.47.70.15/CSNL_new"       # 문서상 SMB URL
  smb_url_lan: "smb://192.168.2.2/CSNL_new"    # 2026-07 실측 마운트 (동일 share, 다른 인터페이스)
  mount_caveat: >
    macOS는 SMB를 사용자별로 mode-700 마운트하므로 다른 멤버의 마운트는 읽을 수 없다.
    같은 share를 두 번째로 마운트하면 '/Volumes/CSNL_new-1', 세 번째는 '-2'가 붙는다.
    따라서 경로를 하드코딩하면 안 되고, 런타임에 해석해야 한다
    (`ingest_grm_nas._resolve_nas_base()` 또는 GRM_NAS_BASE 환경변수).

  subtrees:
    # (a) 표준화된 메타데이터 트리
    - { path: "/Memory/{initial}/{project_code}/", pattern: "{Code,Context,Data,Results}/* + {project,template,kb}_*.json + README.md", purpose: "AI agent용 구조화된 프로젝트 메타데이터" }
    - { path: "/Memory/MetaData/", pattern: "registry.json + members/{INITIAL}.json + infrastructure/{NAME}.json + SCHEMA.md + MANIFEST.md", purpose: "마스터 인덱스" }
    # (b) 자유 형식 개인 작업 공간
    - { path: "/people/{initial}/<freeform>/", pattern: "엄격한 규칙 없음", purpose: "연구원 개인 워크스페이스" }
    # (c) 원본 영상 데이터
    - { path: "/fMRI_DICOM/{YYYYMMDD}_{subj_token}/", pattern: "DICOM raw (.IMA / .dcm)", purpose: "fMRI raw" }
    - { path: "/fMRI_DICOM_USB/", pattern: "USB DICOM 백업", purpose: "오프라인 백업" }
    # (d) 공유 리소스 / 인프라
    - { path: "/Memory/Papers/", pattern: "{Author}_{Year}_{Title}.pdf", purpose: "공유 논문 PDF" }
    - { path: "/Memory/{CRC,CWLL,SfM,Grant,GRM_archive,Workshop,Slack,Reports,Batch,7T,Eyelink}/", pattern: "인프라별 자체 구조", purpose: "공동 사업·이벤트 아카이브" }
    # (e) 기타 워크스페이스
    - { path: "/{AI_workspace,GPU,Server,SNUBIC,Skope_SNUBIC,group-volume,BCS_Events,Photo,Grant,Temp_188_BRL}/", pattern: "용도별", purpose: "어드민 / 인프라 / 임시" }

# 3a. MM — Milestone Meeting (개인 미팅)   ★ 2026-07-21 추가
mm:
  cadence: "비정기 — 지도교수 1:1 (주로 대면)"
  calendar: "CSNL Calendar, 이벤트 제목 'Meeting: [INIT]'"
  nas_dir: "{mount_root}/MM/{INIT}/"
  filename: "MM_yymmdd.pptx"
  db_table: "csnl_ops.milestone_meetings (meeting_date, researcher_initial, slides_path, slides_submitted)"
  observed: "BYL / JOP / JYK / SMJ 폴더 존재 · BHL / SYJ 는 폴더 미생성 (2026-06 기준)"
  expectation: "캘린더에 'Meeting: [INIT]' 일정이 있었다면, MM/{INIT}/ 안에 그 날짜의 자료가 있어야 한다."
  verified: false   # NAS 미마운트 상태에서 문서화 — 실제 레이아웃 재확인 필요

# 3b. GRM · PB — 주간 랩미팅 (수요일)   ★ 2026-07-21 추가
grm_pb:
  cadence: "매주 수 10:00–11:30 Paper Blitz · 11:30–13:00 GRM"
  nas_dir: "{mount_root}/GRM/{YYYY}/{YYYYMMDD}/"
  archive_dir: "{mount_root}/GRM/GRM Archive/{YYYY}_GRM/"
  pb:
    who: "교수를 제외한 전원이 그 주에 읽은 논문을 5분 요약"
    filename: "PB_yymmdd.pdf"
    aggregates: "{mount_root}/GRM/PaperBlitz.MD · PaperBlitz.json (누가 무엇을 읽었는지 — 논문 선호 신호)"
  grm:
    who: "연구원 1인이 본인 연구를 심층 발표"
    filename: "[INIT]_yymmdd.pdf  (keynote/pptx 도 있음; BYL 아카이브는 'LBY_' alias)"
    special: "Focus GRM / 복수 발표자 등 예외 존재"
  db_table: "csnl_ops.lab_meetings (meeting_date, type, presenter_initial, slides_path, paper_doi)"
  expectation: "GRM 일정이 있었다면, 그 날짜 폴더 안에 GRM 자료와 PB 자료가 모두 있어야 한다."
  verified: true    # ingest_grm_nas.py 가 이미 kind={grm,pb,focus_grm} 로 분류 중

# 4. Google Calendar 멤버 토큰 (MJC 는 'MJ'·'MinJin' 둘 다 사용)
calendars:
  slab: ["SL","JSL","JHR","JWL","SK","MJ","MinJin","JOP","SMJ","JYK","BHL","MSY","BYL"]
  csnl: ["SL","JSL","JHR","JWL","SK","MJ","MinJin","JOP","SMJ","JYK","BHL","MSY","BYL"]
  service_account_email: null

# 5. 진행 중 grants
grants:
  - { code: "NRF-2024-...", title: "...", category: "중견",      pi_initial: "SL", start_date: "2024-03-01", end_date: "2027-02-28", status: "active" }
  - { code: "...",          title: "...", category: "기초연구실", pi_initial: "SL", start_date: "...",        end_date: "...",        status: "active" }

# 6. 진행 중 projects (Slab calendar [Project] 토큰 ↔ 풀네임 ↔ 책임자)
projects:
  - { code: "CPS",                full_name: "Concentric Pattern Sensitivity — pRF Ellipticity and Visual Cortex", lead_initial: "JHR" }
  - { code: "OM_WB",              full_name: "Orientation Map — Whole Brain (NSD-based)", lead_initial: "JHR" }
  - { code: "Passive_navigation", full_name: "Passive Navigation (Intersubject correlation, fMRI)", lead_initial: "JSL" }
  - { code: "SerialDep_Spatial",  full_name: "Serial Dependence in Relative Coordinates (Multiple Items)", lead_initial: "JSL" }
  - { code: "CatMag",             full_name: "Categorical vs. Magnitude Decision-Making", lead_initial: "MSY" }
  - { code: "VWM_Contrast",       full_name: "Sequential Visual Working Memory Interaction Based on Contrast Difference", lead_initial: "MJC" }
  - { code: "Screen_Retinotopy",  full_name: "Screen Retinotopy (pRF, voxel receptive field)", lead_initial: "SK" }
  - { code: "WMRepresentation",   full_name: "WM Representation in Early Visual Cortex (2024 updated)", lead_initial: "SK" }
  - { code: "RNN",                full_name: "Recurrent Neural Network Modeling", lead_initial: "JYK" }
  - { code: "BiasVar",            full_name: "Bias-Variance Tradeoff in Working Memory Orientation", lead_initial: "BYL" }
  - { code: "Uncertainty",        full_name: "Posterior Uncertainty via Betting Range", lead_initial: "JOP" }
  - { code: "RingRepSca",         full_name: "Ring Reproduction & Scaling: History Effect in Estimation", lead_initial: "JOP" }
  - { code: "Time",               full_name: "Duration Perception History Effect", lead_initial: "JOP" }
  - { code: "Time2Dist",          full_name: "Duration Perception Distribution Learning", lead_initial: "JOP" }
  - { code: "GranNMDS",           full_name: "Granularity Effect — NMDS & Shepardian Distance Analysis", lead_initial: "JOP" }
  - { code: "GranRDT",            full_name: "Granularity Effect — Rate-Distortion Theory Analysis", lead_initial: "JOP" }
  - { code: "tDCS",               full_name: "tDCS Study (metadata pending)", lead_initial: "JOP" }
  - { code: "Concentricity",      full_name: "Concentricity Prior in Visual Search & Oculomotor System", lead_initial: "SMJ" }

# 7. 논자시 / 학위논문 일정
candidacy_schedule:
  - { initial: "JY", planned_oral_date: "2026-08-15" }
defense_schedule:
  - { initial: "HK", planned_defense_date: "2027-02-..." }

# 8. Supabase (Postgres)   ★ 2026-07-21 추가
supabase:
  connection: "SUPABASE_DB_{HOST,PORT,NAME,USER,PASSWORD} in .env (repo .env is gitignored)"
  schemas:
    csnl_ops:
      owner: "csnl-ops (Next.js on Vercel) — GitHub Actions crons POST /api/cron/*"
      tables:
        - "researchers — 멤버 마스터 (§2 와 대응)"
        - "lab_meetings (meeting_date, type, presenter_initial, slides_path, paper_doi) — GRM/PB 캘린더 진실"
        - "milestone_meetings (meeting_date, researcher_initial, slides_path, slides_submitted) — MM 캘린더 진실. PK=(meeting_date, researcher_initial), csnl_calendar_event_id UNIQUE"
        - "cwll_entries (due_date, researcher_initial) — CWLL 주간 글"
        - "experiment_bookings / behavioral_experiments — Slab 예약 + 실험 데이터"
        - "annual_events · sync_anomalies — 연간 행사 · 동기화 이상 로그"
    csnl_research:
      owner: "operator"
      access: "READ-ONLY (불가침)"
      tables: ["projects — 연구원 프로젝트 JSONB (aims/hypothesis/manipulation/background)"]
    csnl_paper_rec:
      owner: "csnl-paper-rec (this repo)"
      tables:
        - "archive_papers / archive_paper_synopses / archive_paper_embeddings — 논문 코퍼스"
        - "archive_responses — 연구원 읽음/관심없음 영구 진실 (무손상)"
        - "archive_researcher_queues (builder='brq' 권위 / 'p28' 파킹) — 추천 큐"
        - "archive_weekly_digests · archive_researcher_channels — 주간 Notion 배송"
        - "archive_meeting_materials — GRM NAS 자료 인덱스 (P31)"
        - "archive_discovery_watermark — 신규 논문 검색 커서 (P33)"
    csnl_assistant:
      owner: "assistant overlay"
      access: "read-only views over csnl_research/ops (dim_researcher, v_project, v_aim, v_modality, v_meeting)"
  boundary: "csnl_research 는 읽기 전용. 쓰기는 csnl_paper_rec (및 csnl_ops 는 해당 앱) 에 한정."
```

---

## 자료 완결성 규칙 (주간 점검의 근거)

| 캘린더에 있었다면 | NAS 에 있어야 하는 것 | 위치 |
|---|---|---|
| `Meeting: [INIT]` (MM) | 그 날짜의 자료 1건 | `MM/{INIT}/` (예: `MM_yymmdd.pptx`) |
| GRM (수요일 랩미팅) | **GRM 자료 + PB 자료 둘 다** | `GRM/{YYYY}/{YYYYMMDD}/` (`[INIT]_yymmdd.pdf`, `PB_yymmdd.pdf`) |

**안내 톤 (중요):** 저장 규칙을 강요하지 않는다. "규정상 필수" 가 아니라
**"…에 옮겨주시면 csnl-on-ai 프로젝트에 도움이 됩니다"** 로 서술한다.
발송은 **주 1회**로 제한한다 (`state/materials_last_notified_week`).
