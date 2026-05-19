# Paper-rec run 20260519-1539 — review packet

- Method: **harness v2 — Opus scout fan-out (Playwright full-text crawl) + producer–reviewer**, Postgres data plane, no API key/OpenRouter/Ollama.
  각 unit: 도메인 질의 자체 수립 → 학술 검색(OpenAlex/EuropePMC/arXiv/S2) → 전문(full text) 정독 → D1–D5 → ≥3 후보 review loop → 생성-검증 초안.
- 날짜창: journal ≥2025-05-19 / preprint ≥2026-02-19 (strict). dedup: Postgres ledger(8 rec/1 read/3 excl) + reading-DB 1069 + exclusions.
- **발송 안 됨.** `scripts/deliver.py --run-id 20260519-1539` dry-run + 톤린트. 게이트 유지(`state/.APPROVED_20260519-1539` + `--send --operator-approved`).

## 요약

| unit | 채택 #1 | composite | best_dim | 후보수 | 추천근거자수 | iters |
|---|---|---|---|---|---|---|
| JOP | Endogenous precision of the number sense | 8 | D1 | 10 | 260 | 1 |
| BYL | Process dynamics of serial biases in visual percepti… | 8 | D1 | 7 | 273 | 1 |
| MSY | History bias and its perturbation of the stimulus re… | 7 | D1 | 10 | 268 | 2 |
| SMJ | Saccades to spatially extended objects: The roles of… | 9 | D1 | 4 | 275 | 1 |
| JYK | Transitions in dynamical regime and neural mode duri… | 8 | D3 | 3 | 270 | 2 |
| SYJ+BHL | Process dynamics of serial biases in visual percepti… | 8 | D3 | 7 | 274 | 3 |

각 unit 상세(채택 draft + ≥3 후보 + grounding + 인용)는 `<unit>.md`. 전체 후보 색인 `candidates.md`.

## 운영자 결정
- 각 unit에서 #1 발송 / 후보 #2·#3 교체 검토 후 지정.
- 실제 발송: 검토 후 `touch state/.APPROVED_20260519-1539` + `python scripts/deliver.py --run-id 20260519-1539 --send --operator-approved` (first-external-action 게이트; 순차·unit당 ≥7s).
- SYJ+BHL 병합 unit → 두 INIT_claude 채널 + 두 DM (운영자가 단일 채널로 변경 가능).