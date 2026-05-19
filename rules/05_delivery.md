---
name: delivery-rule
description: Channel + DM ping delivery mechanics. Sequential per unit, ≥7s gap, ledger+permalink verify. First-external-action gate. dry-run is default; real send requires operator approval + on-disk token.
source: feedback_first_run_external.md + feedback_slack_pacing.md + docs/DECISIONS-2026-05-18.md + BUILD_SPEC.md §deliver.py
---

## Channel architecture

Each researcher unit has one or more INIT_claude channels. For SYJ+BHL (merged unit),
both channels receive the post. The full recommendation is posted to the **channel**; a
short DM ping points to the channel post.

Channel IDs (from `config/researchers.yaml`):

| Init | Channel |
|---|---|
| JOP | `C0B3FTHAVR8` |
| BYL | `C0B3DRPBP9C` |
| MSY | `C0B4A6WAGNL` |
| SMJ | `C0B39GQK067` |
| JYK | `C0B3FTKE4HY` |
| BHL | `C0B39GVLKCK` |
| SYJ | `C0B3FTNR00J` |

Source: `feedback_channel_routing_and_strict_tone.md` — "모든 substantive Q 발신은
INIT_claude 채널 (`C0B3FTHAVR8` 등) 로."

## First-external-action gate

Paper recommendation delivery is a **new route** not covered by any standing approval.
The first real send requires:

1. Operator runs `deliver.py --dry-run` → reviews full preview output.
2. Operator explicitly approves (out-of-band confirmation).
3. Operator creates on-disk token: `state/.APPROVED_<RUN_ID>` (touch, no content).
4. Operator runs `deliver.py --send --operator-approved`.

Without both `--send` AND `--operator-approved` AND the token file, delivery is blocked
unconditionally. No environment variable override exists for this gate.

Source: `feedback_first_run_external.md` — "dry-run + show output + wait for explicit
approval before the FIRST real execution of any external-side-effect operation."
Source: `docs/DECISIONS-2026-05-18.md` — "Paper-rec is a NEW route, NOT covered by the
`memev_autofire` standing approval."

### `--dry-run` output (required before any real send)

Print per unit:
- Target channels (IDs + names)
- DM targets (init list)
- Exact channel body (full Korean text)
- Exact DM ping text
- Ledger rows that would be written
- Tone lint result (pass / fail + matched terms)

## Sequential delivery: one unit at a time

Never batch. Always process units one at a time:

1. Lint the channel body against `rules/01_tone.md` BANNED_TERMS — abort unit if fail.
2. Post to all unit channels via `SLACK_BOT_TOKEN`.
3. Verify Slack API response `ok == true` + extract `ts` (message timestamp).
4. Retrieve permalink.
5. Write ledger row to `paper_recommendations` + `recommendation_messages`.
6. Send DM ping to each `dm_init` in the unit (one DM per init, sequential).
7. Sleep ≥ 7 seconds before processing the next unit.

Source: `docs/DECISIONS-2026-05-18.md` — "Sequential pacing — one researcher unit at a
time, ≥5–10 s gap, ledger row + Slack permalink verified between sends. Never batch."
Source: `feedback_slack_pacing.md` — "한 번에 1 명만 DM 발사. 발사 직후 ledger row +
slack permalink 검증. 다음 발사까지 최소 5~10 초 gap."

## DM ping format

Short, no recommendation content, no signature:

```
INIT_claude 채널에 이번 주 후보 논문을 게시했습니다.
```

Maximum 2 lines. No paper title, no DOI, no grounding text. Just a pointer.

Source: BUILD_SPEC.md §07_drafts.json — "short DM-ping text ('INIT_claude 채널에 다음
주 후보 논문을 게시했습니다.' style, no signature, ≤2 lines)."

## Tone lint

`deliver.py` calls `lint_body(text)` before each outbound Slack post. The function reads
the BANNED_TERMS fenced block from `rules/01_tone.md` and checks for substring matches
(case-insensitive). Also checks `paradigm` and `framework` occurrence counts.

If lint fails: print matched terms, do NOT send the unit's message, mark that unit as
`lint_fail` in the run summary, continue to next unit.

## Slack API requirements

- Authentication: `SLACK_BOT_TOKEN` from environment (never hardcoded).
- Method for channel post: `chat.postMessage` with `channel=<channel_id>`.
- Method for DM: `chat.postMessage` with `channel=<user_id>` (DM opens automatically).
- Rate-limit (429): retry with `Retry-After` header value; max 3 retries with exponential
  backoff (1x, 2x, 4x); after 3 failures, mark unit `pending_429` and exit without abort.

## Ledger write (post-send only)

Write to ledger only after Slack confirms `ok == true`:

```sql
-- paper_recommendations
INSERT INTO paper_recommendations
  (run_id, unit_id, member_init, channel_id, slack_ts,
   paper_doi, paper_title, paper_date, tier, posted_at)
VALUES (...);

-- recommendation_messages (one row per channel post)
INSERT INTO recommendation_messages
  (id, channel_id, message_ts, unit_id, paper_doi, posted_at, context_json)
VALUES (...);
```

`paper_recommendations` primary key is `(unit_id, paper_doi)` — a duplicate insert means
the paper was already recommended to this unit, which should have been caught by dedup.
Treat as a logic error: log and abort the unit.

--- end of 05_delivery.md
