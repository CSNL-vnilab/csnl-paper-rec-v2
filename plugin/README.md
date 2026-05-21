# CSNL Paper Archive — Researcher Interview Plugin

Interactive, one-paper-at-a-time interview over the lab's classic-paper
archive. Designed to be installed by an individual researcher in Claude
Code; everything is local except the DB calls back to the lab's Supabase
(`csnl_paper_rec.archive_*`).

## What it does

When you run `/paper-interview <YOUR_INIT>`:

1. **Profile check.** Shows the current topics, methods, and authors the
   lab DB thinks you focus on. You confirm or correct.
2. **Queue walk.** Walks three age chunks of papers (recent ≤5y → mid
   5–10y → classic >10y), in rank order. Each paper is summarised in one
   short Korean paragraph (journal, authors, year, scope tags, why-it-was-picked).
3. **MCQ.** For every paper you pick one of:
   - `(1) 나중에 읽을 리스트에 추가`
   - `(2) 내 연구와 관련 없음`
   - `(3) 이미 읽었음`
   - `(4) 더 자세히 소개해줘` — spawns an *isolated* explainer agent that
     either fetches the full-text (only when the parent harness's
     `pipeline/crawl.mjs` is reachable on disk — e.g. when this plugin
     is installed inside the operator's repo) **or falls back to the
     paper's stored abstract**. In abstract-only mode the explainer
     prefixes its reply with "전문 본문을 가져오지 못해 초록 기반으로
     설명드립니다." Context-budget kept low so the main interview thread
     doesn't bloat.
4. **Meta-review.** Every 10 answers, a short progress + criterion-adjustment
   pass: which choice patterns are emerging, and should the queue ranking
   be re-tuned before the next chunk?
5. **Persist.** All answers go to `csnl_paper_rec.archive_responses` and
   `archive_interview_sessions`. You can stop and resume; the next session
   picks up where you left off.

## Install

The lab marketplace listing is **not yet published**; for now, install
from a local checkout. Ask the operator (`jy061100@gmail.com`) to push
the marketplace manifest first if you need a one-line install.

Local install (current path):

```
/plugin install /Users/csnl/Documents/claude/csnl-paper-rec/plugin
```

Once published, the path will be:

```
/plugin marketplace add github:CSNL-vnilab/csnl-paper-rec-v2
/plugin install csnl-paper-archive-interview
```

## Configure

Copy `.env.example` to either `<plugin-dir>/.env` (recommended;
**wins over** the home-dir file when both exist) or
`~/.csnl-paper-archive/.env` (fallback). Fill in:

```
SUPABASE_DB_HOST=...
SUPABASE_DB_USER=...
SUPABASE_DB_PASSWORD=...
SUPABASE_DB_NAME=postgres
CPR_LEDGER_SCHEMA=csnl_paper_rec
```

Talk to the lab operator (`jy061100@gmail.com`) for credentials. Plugin
reads only the archive tables (`archive_papers`, `archive_filter_decisions`,
`archive_researcher_queues`) and writes only to `archive_responses`,
`archive_interview_sessions`, `archive_meta_reviews`, and
`archive_profile_verifications`. Your `csnl_research` rows are never
modified.

## Run

```
/paper-interview BHL
```

(Replace `BHL` with your two- or three-letter init.)

You can stop at any time by typing `/exit` or just leaving the conversation.
The next session resumes from where you left off.

## Constraints (read these)

- Korean text only for messages shown to you (the researcher). Internal
  labels and DB columns stay in English.
- No paper is sent to a third-party LLM. The explainer agent (option 4)
  runs in your own Claude Code session and tries to fetch full text via
  the lab's keyless scholarly crawler **only when that crawler is
  installed on disk** — otherwise it explains from the stored abstract
  and says so up front.
- No Slack, email, or external messaging is triggered by this plugin.
  It is read-and-record only. The plugin's `_pdb.py` enforces a
  table-name allowlist that blocks any write outside
  `archive_{interview_sessions, profile_verifications, responses,
  meta_reviews}` — accidental cross-writes raise an error.
- The lab operator can revoke your Supabase role at any time. Doing so
  silently disables the plugin; nothing on your machine is touched.
- Sessions that you abandon mid-walk (close the laptop, switch projects)
  are **not** auto-closed. The next `/paper-interview <init>` resumes
  the same session. Operator-side analytics on "completed sessions" only
  count sessions where you reached the end or typed `종료`.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `no queue rows for researcher_id=…` | Operator has not yet run `build_researcher_queue.py --apply` for you. Ask the operator. |
| `connection refused` / `password authentication failed` | `.env` not picked up. Copy `.env.example` to `.env` in the plugin dir. |
| `profile snapshot empty` | Your `csnl_research.projects` rows are below the active-project threshold (phase, confidence ≥ 0.7). Update them with the CSNL archive interview tool first. |

## Boundaries

- This plugin **does not** trigger paper recommendations, Slack DMs, or
  the weekly cron — those live in the parent `csnl-paper-rec-v2` harness
  and are operator-gated. The plugin is *retrospective and self-service*:
  walking the archive that has already been built for you.
- The "interview" flow is similar to the lab's CSNL self-archive but
  scoped strictly to paper-relevance signalling. No project metadata is
  collected or modified here.
