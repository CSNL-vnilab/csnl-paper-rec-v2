---
description: Show this week's top unread-paper recommendations for a CSNL researcher (auto-generated Tue evening by the operator-side weekly_recommend cron). Researcher-facing; Korean. Reads csnl_paper_rec.archive_weekly_picks; never writes.
argument-hint: "<researcher-init>  # e.g. JOP / BYL / MSY / SMJ / JYK / BHL"
---

## /csnl-paper-archive-interview:paper-weekly $ARGUMENTS

Show the CSNL researcher (`$ARGUMENTS`) the current week's recommended unread papers.

### What to do

1. Trim `$ARGUMENTS`, uppercase it, store as `RID`. If empty, ask the researcher for their initials and stop.

2. Run:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/paper_weekly_show.py" "$RID"
   ```
   The script reads `archive_weekly_picks` for the latest week and prints the Korean digest.

3. If the script reports zero rows ("이번 주 추천이 아직 생성되지 않았습니다"), explain in Korean that the operator's Tuesday cron has not yet produced this week's batch and ask the researcher whether they'd like to:
   - run the interactive interview now (`/csnl-paper-archive-interview:paper-interview $RID`), or
   - check back tomorrow.

4. If rows are present, present the list verbatim, then ask:
   > "이 중 어떤 paper 가 관심 가시나요? `/csnl-paper-archive-interview:paper-interview $RID` 로 시작하시면 한 편씩 진행할 수 있습니다."

### Boundaries

- Read-only. Never write to the DB from this command.
- Korean researcher-facing text only. Preserve English scientific terms (efficient coding, RSA, …) verbatim. Never reveal internal codes (F-NIM, M-RSA, …).
- `RID` is locked to `$ARGUMENTS`; do not show another researcher's batch from this session.
