# CLAUDE.md — csnl-paper-rec-v2

## 하네스: per-researcher paper recommendation

**목표:** 연구자별 최신 관심사(Postgres `csnl_research.projects`)에 근거한
논문 추천을, 전문(full-text) 정독 기반 Opus 스카우트 팬아웃 + 생성-검증
초안 루프로 생성하고, 운영자 게이트에서 멈춘다 (무발송).

**트리거:** 논문 추천 관련 작업("paper rec 실행", "주간 추천", "추천
돌려줘", "다시 실행", "이 unit만 다시", "dry-run 다시") 시
`paper-rec-orchestrator` 스킬을 사용하라. 단순 질문은 직접 응답 가능.

**경계 (불가침):**
- 발송 금지. first-external-action 게이트(`--send --operator-approved` +
  `state/.APPROVED_<RID>`)는 운영자 전용. 이 세션은 dry-run 패킷까지만.
- DB 접근(init/migrate/00_select/dedup_snapshot/deliver)은 운영자가 `!`로
  실행. 에이전트는 prod DB에 접속하지 않는다. `csnl_research`는 읽기 전용,
  쓰기는 `csnl_paper_rec` 스키마에 한정.
- 실행 모드 = 서브 에이전트(Agent 도구, `model:"opus"`, 팬아웃은
  `run_in_background`). TeamCreate/SendMessage 미사용.
- 연구자 노출 텍스트에 내부 용어/서명 금지 (rules/01·06).
- PB/CWLL 일정은 SMJ 영역 — 범위 밖 (rules/00).

상세 설계 근거·검증: `docs/HARNESS-DESIGN-v2.md`. 규칙: `rules/00–06`.

**변경 이력:**
| 날짜 | 변경 내용 | 대상 | 사유 |
|------|----------|------|------|
| 2026-05-19 | 초기 구성 (v2) | agents/×5, skills/×4, orchestrator | 하네스 엔지니어링 마이그레이션; 전문 정독 스카우트 + Postgres 데이터 평면 |
| 2026-05-19 | DM 모드 추가 | scripts/deliver.py(--mode dm), scripts/build_dm_drafts.py | 운영자 override: 전체 추천+후보 목록+선택 질의를 개별 연구자 DM 로 전달 (run 20260519-1539) |
| 2026-05-19 | DM 일괄 발송 | csnl_paper_rec ledger | 7/7 발송 완료; JOP 시점 변경(time2dist 집중, 직전 추천 read 표시, 후보 #4 교체); SYJ/BHL 개별 추천 |
| 2026-05-20 | Phase 7 진화 하네스 | agents/feedback-analyst, skills/paper-rec-evolve, scripts/{fetch_replies,classify_feedback,propose_followups,apply_feedback}.py | 응답 캐치 → 분류 → 중립 follow-up(NO PB) → 게이트된 적용 + 키워드 진화 diff 제안 (manual-only 유지) |
| 2026-05-21 | v3: 주간 cron + 다중턴 대화 | scripts/{cron_tick,apply_evolution}.py + scripts/run_*_cron.sh + cron/*.plist + state/schema_v3.sql + docs/DECISIONS-v3.md | DECISIONS #4(manual-only) override; 매주 금 14:00 KST 자동 사이클 + 4h tick state machine (awaiting_initial_reply → reminded → awaiting_decision → decided/passed/timeout) + 목 23:00 rule-based evolution. NO LLM in unattended path; Opus drafts off-ramp(operator pre-run). PB 영역 불가침. |
| 2026-05-21 | P13: 아카이브 + 인터뷰 플러그인 (scaffold) | state/schema_archive.sql + scripts/archive/{ingest_classics,ingest_rec_log,ingest_pi_network,merge_dedupe_filter,compute_embeddings,build_researcher_queue}.py + plugin/{.claude-plugin,commands,skills,agents,scripts}/* + docs/HARNESS-ARCHIVE-DESIGN.md | 추천 사이클이 신규 논문만 다루는 한계 극복 — 4,878편 클래식 PDF + 7년 추천로그 1,306 DOI + PI-network 182명 출간물(10y)을 머지/필터/임베딩 후 연구원별 큐(≤5y/5-10y/>10y 3-chunk) 사전 생성, 마켓플레이스 플러그인이 1-paper-MCQ 인터뷰로 응답 수집. 발송 경계 그대로 — 인터뷰는 read+record only. Opus×2 review + codex adversarial review를 통과해야 배포. |
| 2026-05-21 | P13 리뷰 라운드 반영 (Opus×2 + codex adversarial) | 위 파일들 + plugin/scripts/_pdb.py | 11개 크리티컬 패치 적용: 머지 UPSERT 추상 덮어쓰기 방지 (COALESCE + 길이 가드), psql 치환 안전화 (토큰 분할), fuzz-collapse 고아 행 정리 (트랜잭션), 큐 빌더 레이스 (build_token UUID), 플러그인 DB 쓰기 화이트리스트 + multi-statement 차단, SKILL.md raw-JSON 누출 방지 + 태그 한글화, 익스플레이너 fallback + 인용 보존, MCQ 관대 매처, meta-review idempotent UPSERT (UNIQUE INDEX 마이그레이션), 세션-스테이지드 paper 검증 (pick_next→record_choice), 원격 임베딩 운영자 승인 게이트 (state/.ARCHIVE_EMBED_APPROVED + --operator-approved-remote-embed). 데이터 평면/플러그인 평면 모두 재-임포트 + 재-스모크 통과. |
| 2026-05-26 | P19a: 과학적 엄밀성 알고리즘 (3-Opus 설계 + codex 적대적 리뷰 → ship-with-cuts) | scripts/archive/{build_corpus_idf,build_fingerprints,validate_drift}.py + state/archive/known_phrases.txt + state/archive/lexicon_idf.json + state/archive/fingerprints/*.json + scripts/archive/build_researcher_queue.py (BM25 신호 추가) + plugin/skills/paper-archive-interview/SKILL.md (top_signals 렌더링) + docs/HARNESS-ALGORITHM-DESIGN.md | 글로벌 키워드 백 매칭의 한계 — 연구원 본인 vocabulary (granularity effect, Blahut-Arimoto, Shepard generalization gradient 등) 표현 불가 — 극복. 3-Opus 병렬 설계 (A: fingerprint 추출 / B: 베이지안 랭킹 / C: 검증 harness) + codex 적대적 리뷰 (10개 공격 벡터, 3 CRITICAL: ground-truth 순환성 / method abstract 한계 / success 미정의) 결과 ship-with-cuts. **Ship**: lexicon-anchored phrase 추출 (146 phrase seed) + 도메인 IDF (8,661편 corpus) + 연구원별 fingerprint JSON + 큐 빌더 BM25 30% 가중 + explain_why Stage 2 한글 + MCQ-precision 드리프트 모니터. **Cut** (P20 이전): CWLL backtest as primary metric (#1 순환성), Thompson sampling 실시간 (#8), fixed κ knob (#6), method signature from abstract (#5), 인용 그래프 (#4 coverage), Beta-Bernoulli posterior (#3 starvation). 실측 JOP top-5: granularity effect / history effect / sensory adaptation + efficient coding / serial dependence — 모두 JOP 실연구 정확 매칭, 이전 cosine 만에선 surfacing 안 됨. |
