# Post-follow-up record — run 20260519-1539, 2026-05-20

Second send pass under the same RID + APPROVED token: reply detection,
feedback recording, and two-mode DM (ack to replier, reminder to non-repliers).

## Reply detection (`scripts/fetch_replies.py`)

7 recipients · **1 reply** (JOP, threaded under the recommendation) · 6 non-repliers.
Enhancement landed this pass: pull `conversations.replies` per parent ts in
addition to `conversations.history`, so thread replies under the bot's
recommendation message are caught (the first pass missed JOP's threaded reply).

## Feedback classification (`scripts/classify_feedback.py`)

JOP signal: **thumbs_up** (high) — committed engagement.
The reply mentioned Paper Blitz; the classifier was extended with Korean
1st-person commitment cues (`할게`, `쓸게`, `사용하겠`, `활용하겠`, `발표하겠`)
to catch the engagement signal. **It does NOT trigger on "paper blitz"** —
rules/00 keeps Paper Blitz out of scope (SMJ's domain).

## Ledger write (`scripts/apply_feedback.py`)

`csnl_paper_rec.feedback_events`: **+1 row** (JOP / signal=thumbs_up /
recommendation_doi=10.1111/bjop.70070 / idem_key=20260519-1539:JOP:...).
No `exclusion_rules` change (thumbs_up doesn't exclude).

## DMs sent (sequential ≥7 s, tone-lint OK, ledger written each)

### Ack to replier (10_feedback_acks.json — 1 recipient)

| → DM | who | ts | content |
|---|---|---|---|
| D0AMRACTLBH | JOP | 1779265659.957299 | thumbs_up ack: "추천이 도움 되신 것으로 확인했습니다. 동일한 방향성을 향후 추천에 유지하도록 반영하겠습니다." (NO PB mention) |

### Reminders to non-repliers (09_followups.json — 6 recipients)

| → DM | who | ts |
|---|---|---|
| D0AN6PMLWCS | BYL | 1779265677.150039 |
| D0AP128V9DE | MSY | 1779265686.076969 |
| D0AN0CHTJP5 | SMJ | 1779265695.006839 |
| D0AN3B8K0CD | JYK | 1779265703.857549 |
| D0AN4N0278E | SYJ | 1779265712.703349 |
| D0AN6PXAESE | BHL | 1779265721.525409 |

Reminder template (neutral, NO Paper Blitz, NO signature): *"…지난 추천 논문(<title>, <DOI>)을 확인하셨는지요. 추천작에 대한 피드백, 또는 함께 보내드린 후보 목록 중 다른 논문을 읽어보고 싶으시면 본 메시지에 회신해 주십시오. 응답이 없으셔도 무방합니다."*

Permalinks (all): `https://csnlworkspace.slack.com/archives/<dm>/p<ts>`.

## Why JOP's ack does NOT mention Paper Blitz

`rules/00_scope.md` + `DECISIONS-2026-05-18` make Paper Blitz SMJ's
exclusive administrative domain ("must not be touched", requires explicit
re-authorization). JOP's reply mentioned PB autonomously, which is fine —
that's the researcher's own scheduling decision with SMJ. Our ack
recognizes the *engagement* signal (thumbs_up, "동일한 방향성을 향후 추천에
유지") without entering PB territory.

## Ledger state (after this pass)

- `paper_recommendations`: 16 rows (8 legacy + 7 original send + 1 JOP ack
  redirected to same PK via ON CONFLICT DO NOTHING → effectively the 6
  reminder rows accumulate; recommendation_messages tracks each message).
- `recommendation_messages`: +7 this pass (1 ack + 6 reminders).
- `paper_recommendations_read`: 2 (unchanged).
- `feedback_events`: 1 (new JOP thumbs_up).
- `exclusion_rules`: 3 (unchanged).
