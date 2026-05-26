---
description: Show the upcoming Wednesday Paper Blitz (lab 5-min journal club) assignment for a CSNL researcher. Reads csnl_paper_rec.archive_paper_blitz; Korean researcher-facing. Read-only.
argument-hint: "<researcher-init>  # e.g. JOP / BYL / MSY / SMJ / JYK / BHL"
---

## /csnl-paper-archive-interview:paper-blitz $ARGUMENTS

Show the CSNL researcher (`$ARGUMENTS`) their next Wednesday Paper Blitz assignment.

### What to do

1. Trim `$ARGUMENTS`, uppercase it, store as `RID`. If empty, ask the researcher for their initials and stop.

2. Run:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/paper_blitz_show.py" "$RID"
   ```
   The script reads `archive_paper_blitz` for the upcoming Wednesday and prints the Korean digest with paper metadata + a discussion-prompt block.

3. If the script reports "이번 주 발표 없음", explain in Korean:
   > "지난 1주간 인터뷰에서 `이미 읽음`으로 표시하신 paper 가 없어 이번 수요일 Paper Blitz 발표 없음으로 배정되어 있습니다. `/csnl-paper-archive-interview:paper-interview $RID` 로 인터뷰를 진행하시면 다음 주 발표 후보가 자동 생성됩니다."

4. If a paper is assigned, present it with a 5-min Blitz preparation prompt:
   > "수요일 5분 Paper Blitz 준비를 도와드릴까요? 이 paper 의 핵심 주장, 방법, 결과를 5분 분량으로 정리해드릴 수 있습니다."
   If yes, generate a Korean 5-min outline (claim / method / result / connection to RID's projects).

### Boundaries

- Read-only. Never write to the DB from this command.
- Korean researcher-facing text only. Preserve English scientific terms verbatim.
- `RID` locked to `$ARGUMENTS`. Do not show another researcher's Blitz from this session.
