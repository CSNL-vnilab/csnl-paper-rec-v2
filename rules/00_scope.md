---
name: scope-rule
description: What this system does and does not do. Boundary between paper recommendation content (in scope) and schedule reminders / unattended LLM paths (out of scope).
source: BUILD_SPEC.md + docs/DECISIONS-2026-05-18.md + project_smj_pb_cwll.md + feedback_llm_key_policy.md
---

## What this system does

Delivers a single topically-grounded journal or preprint recommendation per researcher unit,
per manual run. The recommendation is posted to the unit's INIT_claude channel, with a short
DM ping pointing to that post. Delivery is gated behind operator approval for all new routes
(see `rules/05_delivery.md`).

Source data: `csnl_research.projects` rows where `phase ∈ {data_collection, analysis,
manuscript_draft}` AND `confidence_avg ≥ 0.7`. Discovery uses keyless public APIs
(OpenAlex, Crossref, Semantic Scholar, PubMed E-utilities, arXiv, bioRxiv).

## What this system does NOT do

### 1. Paper Blitz / CWLL schedule reminders

Paper Blitz (Wednesday 10:00) and CWLL (Tuesday midnight) reminders are handled by SMJ
(정새미). This system sends recommendation *content*; schedule administration is SMJ's
domain and must not be touched.

Source: `project_smj_pb_cwll.md` — "Paper Blitz 나 CWLL 일정은 알아서 SMJ 가
발송하고 있으니까 너가 할 필요 없어."

Any future cron or route that would send PB/CWLL reminder messages is out of scope
and requires explicit operator re-authorization.

### 2. Unattended LLM calls via Anthropic API

No `ANTHROPIC_API_KEY` anywhere in this codebase. No Anthropic SDK import in any
pipeline script. Scoring and drafting are performed by the in-session Opus agent
(operator-driven, manual). Any future cron LLM path must use local Ollama only.

Source: `feedback_llm_key_policy.md` — "No direct Anthropic API key usage anywhere in
code or env." + BUILD_SPEC.md: "No Anthropic API anywhere."

### 3. Automatic/unattended delivery

All sends are manual for the validation phase (operator-driven, no cron, no GitHub
Actions). `deliver.py` defaults to `--dry-run`; a real send requires both `--send` and
`--operator-approved` flags plus a on-disk approval token `state/.APPROVED_<RUN_ID>`.

Source: `docs/DECISIONS-2026-05-18.md` decision #4 — "Manual only until validated."

### 4. Automated feedback classification

Feedback intent classification (positive / negative / information) is deferred. The
infrastructure (ledger `feedback_events`, `exclusion_rules`) is built, but automated
re-querying on feedback is not triggered without operator involvement during the
validation phase.

### 5. Topics outside active projects

This system infers topic fit from `csnl_research.projects` structured fields. It does
not accept ad-hoc topic lists, does not scrape NAS directly, and does not query any
researcher's personal reading notes or Zotero library.

## In-scope output

```
state/runs/<RUN_ID>/
  01_active_projects.json   — project rows from Supabase
  02_topic_bundles.json     — per-unit keyword + anchor_doi bundles
  03_candidates.json        — discovered papers (pre-verify, pre-dedup)
  04_verified.json          — DOI-verified candidates
  05_deduped.json           — dedup vs ledger + exclusion_rules
  06_scored.json            — scored by in-session Opus agent (SKILL)
  07_drafts.json            — Korean academic drafts (SKILL)
ledger.sqlite               — paper_recommendations, feedback_events, exclusion_rules
```

— end of 00_scope.md
