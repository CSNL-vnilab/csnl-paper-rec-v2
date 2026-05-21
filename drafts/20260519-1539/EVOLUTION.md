# Feedback reflection plan — run 20260519-1539 (as of 2026-05-21)

Phase 7 (`paper-rec-evolve`) proposal. **Proposes only** — no auto-apply.
Honors `rules/04` (feedback loop, exclusions, carryover), `rules/06 §3`
(2+ consistent signals before logic change), `rules/00` (Paper Blitz
out-of-scope; SMJ's domain), `DECISIONS-2026-05-18 #4` (manual-only).

## 1. Evidence snapshot

| Recipient | Signal | Confidence | Action taken | Action pending |
|---|---|---|---|---|
| JOP 박준오 | thumbs_up | high | `feedback_events`+1 (`thumbs_up`, doi 10.1111/bjop.70070); ack DM sent | none — engagement loop closed |
| BYL 이보연 | — (silent) | — | reminder DM sent 2026-05-20 | wait window |
| MSY 여민수 | — (silent) | — | reminder DM sent 2026-05-20 | wait window |
| SMJ 정새미 | — (silent) | — | reminder DM sent 2026-05-20 | wait window |
| JYK 김정예 | — (silent) | — | reminder DM sent 2026-05-20 | wait window |
| SYJ 조수영 | — (silent) | — | reminder DM sent 2026-05-20 | wait window |
| BHL 이보현 | — (silent) | — | reminder DM sent 2026-05-20 | wait window |

Ledger (`csnl_paper_rec`): `paper_recommendations` 15 · `recommendation_messages` 14 · `paper_recommendations_read` 2 · `feedback_events` 1 · `exclusion_rules` 3 (legacy JOP: RT/uncertainty/tDCS — pre-dating this run).

## 2. What the evidence supports — and what it doesn't

**Supports (act on):**
- JOP's `thumbs_up` validates the operator override path (`time2dist` focus + swap to `10.1111/bjop.70070`). This is one data point against one configuration — useful for *confirming* the path was right, **not** for changing logic for other units.

**Does NOT support (do not act):**
- Silence from 6 researchers ≠ rejection. Per `rules/06 §3` skepticism, this is **weak** evidence — could mean: too busy, will reply later, satisfied (no need to reply), missed the DM, off-topic for them. Not actionable as exclusions or keyword changes.
- `rules/06 §1`: no recommendation > a bad one. Don't fabricate signals to feel productive.
- `rules/04`: carryover rank 2–5 logic already covers "no top-1 feedback → use alternates next cycle"; no exclusion to add.

## 3. Reflection actions (already applied vs proposed)

### Already applied (done before this plan)

- ✅ `feedback_events`: JOP thumbs_up row written.
- ✅ JOP ack DM sent (no PB mention per `rules/00`).
- ✅ 6 reminder DMs sent (neutral, no PB).
- ✅ `paper_recommendations_read`: JOP's prior rec `10.7554/elife.101277` marked read (so dedup never re-recommends it).

### Proposed (operator-gated; not auto-applied)

**None for the ledger right now.** No new exclusions, no signal-driven status changes warranted. The carryover-rank logic in `rules/04` is the mechanism for next cycle; it doesn't need a manual DB write — next run's `scout_*.json` candidates are already in-memory artifacts and the dedup snapshot already excludes the 16 in-ledger DOIs + 1069 reading-DB entries.

## 4. Logic / keyword evolution diff (Phase 7)

Per `paper-rec-evolve` skill §Step 4: **require ≥2 consistent signals across unit-members** before a logic change. The bar is not met.

```
# Evolution proposals — 20260519-1539

## JOP — proposed changes
- NO CHANGE to recommendation logic on this evidence (1 positive signal is
  weak basis for keyword/query change; it validates the override path).
- NEXT CYCLE: keep time2dist focus per operator; preserve carryover ranks
  2–5 from this run's scout_JOP.json as next-cycle seeds (the scout's
  PLoS Biology paper #2, eLife #3, BMC Bio #5, PLoS Biology #6 candidate).
- Add JOP's new READ row (10.7554/elife.101277) to dedup_terms next cycle
  (already in paper_recommendations_read; dedup_snapshot.py will pick up).

## BYL — proposed changes
- NO CHANGE (silence ≠ rejection per rules/06 §3).
- NEXT CYCLE: preserve carryover ranks 2–5 from scout_BYL.json (Glasauer &
  Medendorp 2026 PLoS Comp Biol comp 8, Shan/Hajonides PLOS Biology comp 8).

## MSY — proposed changes
- NO CHANGE (silence).
- NEXT CYCLE: carryover ranks 2–5 from scout_MSY.json (Chen & Bae 2025
  J Vision comp 7, Poncet et al. Cereb Cortex 2025 comp 7).
- Note: MSY had the thinnest scout (top composite 7); if next cycle's
  fresh scout also yields composite < 8, consider a re-scout brief tune
  (more emphasis on cat_mag_main's StyleGAN2 face conditioning).
  Threshold not yet met (1 thin run is not a pattern; needs 2+).

## SMJ — proposed changes
- NO CHANGE (silence).
- NEXT CYCLE: carryover ranks 2–5 from scout_SMJ.json (Mairon & Ben-Shahar
  2025 comp 8, Murlidaran/Eckstein 2026 arXiv comp 8).

## JYK — proposed changes
- NO CHANGE (silence; only 3 candidates this run — domain niche is thin).
- NEXT CYCLE: carryover Wu & Zhou 2025 eLife comp 8 as primary alternate.

## SYJ — proposed changes
- NO CHANGE (silence).
- NEXT CYCLE: carryover scout ranks 3–7 from shared SYJ+BHL pool
  (Shan/Hajonides PLOS Biology comp 8, Andriushchenko BMC Bio comp 7).

## BHL — proposed changes
- NO CHANGE (silence).
- NEXT CYCLE: carryover from shared pool (e.g., Park HB comp 8 — currently
  SYJ's; or fresh re-scout for BHL specifically given the unit split).
```

## 5. Decision matrix (what to do now)

| Action | Recommend | Reason |
|---|---|---|
| Apply more `exclusion_rules` rows | **No** | No rejection signals; 1 thumbs_up + 6 silences doesn't justify any exclusion (rules/04 + rules/06 §3). |
| Send second-round reminders | **No** | Risk of researcher fatigue; silence after 1 reminder is acceptable per rules/06 §4 (resilience without surfacing system state). |
| Modify scout query seeds / keywords | **No** | No 2+ consistent signal pattern (rules/06 §3 §1). |
| Re-recommend an alternate to anyone | **No** | No `picked_alternate_doi` proposals; nobody asked. |
| Commit this plan + wait for next cycle | **Yes** | Move on to next run's prep; let silent recipients have time / next cycle. |

## 6. Next-cycle prep (when ready for next run)

When the operator triggers the next weekly cycle (new `RID`):

```sh
NEW_RID=YYYYMMDD-HHMM  # e.g. 20260526-1500 (next week)
cd /Users/csnl/Documents/claude/csnl-paper-rec
# 1) refresh interest + dedup against the now-15-row ledger + new read row
! python pipeline/00_select_projects.py $NEW_RID
! python pipeline/01_extract_topics.py $NEW_RID
! python scripts/dedup_snapshot.py $NEW_RID
python  scripts/build_scout_briefs.py $NEW_RID
# 2) scouts will automatically dedup against current ledger (including
#    JOP's read row 10.7554/elife.101277 and the 7 recs from 20260519-1539)
#    and will carry over rank-2..5 candidates from 20260519-1539 scout files.
# 3) JOP's brief should retain time2dist as primary (operator override
#    persisted in this run's brief; re-confirm at next cycle if intent
#    has shifted).
```

If by next cycle ≥2 silent recipients have replied with consistent signals
(e.g., 2 thumbs_down on "RNN modeling" topic), THEN propose a specific
keyword diff at that point — not before.

## Outcome

- **Reflection plan: status-quo for ledger + logic; carryover-based next cycle.**
- **No further sends this turn** (no signals warrant new messages).
- Plan committed to `drafts/20260519-1539/EVOLUTION.md` for the run's record.
