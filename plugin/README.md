# CSNL Paper Archive — Researcher Interview Plugin

Interactive, one-paper-at-a-time interview over the lab's classic-paper
archive. Designed to be installed by an individual researcher in Claude
Code; everything is local except the DB calls back to the lab's Supabase
(`csnl_paper_rec.archive_*`).

## How the lab DB is structured

Two schemas. You should know which is which:

| Schema | What lives there | This plugin's access |
| --- | --- | --- |
| `csnl_research` | Each researcher's project metadata (purpose, hypotheses, manipulation variables, modalities). Maintained via the CSNL self-archive tool. | **read-only** — used to render your profile at Stage 1 |
| `csnl_paper_rec` | All paper-recommendation tables: the weekly Slack-delivered recommendation ledger, the cron state machine, AND the new `archive_*` layer this plugin uses. | **read on the archive tables**, **write only on the 4 plugin-writeable tables** (see boundary below) |

Within `csnl_paper_rec`, the archive layer (built by the operator before
you install this plugin) holds:

- `archive_papers`        — ~8,680 canonical papers merged from three
                             sources (lab classics archive, 7 years of
                             CWLL recommendation logs, PI-network publications)
- `archive_filter_decisions` — textbook / draft / poster / review-doc
                             filter outcomes + per-paper dimension tags
                             (focus / method / stim / subj)
- `archive_paper_embeddings` — BAAI/bge-m3 1024-dim vectors
- `archive_paper_dim_tags`  — normalized table of dimension tag hits
- `archive_researcher_queues` — per-researcher top-200 ranked queue
                             (recent ≤5y / mid 5–10y / classic >10y),
                             with composite score + S/A/B/C tier +
                             matched-dimension breakdown
- `archive_interview_sessions` ← *you write here* — your session row
- `archive_profile_verifications` ← *you write here* — your confirmed
                             dim_preferences + project_weights
- `archive_responses` ← *you write here* — one row per paper × choice
- `archive_meta_reviews` ← *you write here* — every-10 belief snapshot

## What this interview updates

The archive + your initial queue are already built. The interview
captures three things and feeds them back into the next queue rebuild:

1. **Profile confirmation** — verify the topic / methodology summary
   the system extracted from your `csnl_research.projects` rows. Edit
   what's wrong, add what's missing.
2. **Dimension preferences + project weights** — pick which
   methodology / stimulus / focus / subject categories matter to you
   right now, and (if you have multiple active projects) the percentage
   weighting between them.
3. **Per-paper choice signal** — for each paper in your queue, one of:
   `1` save for later, `2` not relevant, `3` already read, `4` tell me
   more (spawns an isolated explainer). Every 10 answers, the system
   actively updates its belief about your preferences (Stage 4) and
   writes the new weights back to the DB. The next operator-side queue
   rebuild picks those up.

You can stop and resume any time. The next `/paper-interview` resumes
your open session from where you left off.

## What it does

When you run `/csnl-paper-archive-interview:paper-interview <YOUR_INIT>`:

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

In a fresh Claude Code session:

```
/plugin marketplace add CSNL-vnilab/csnl-paper-rec-v2
# If you previously installed an older version, uninstall + refresh first:
/plugin uninstall csnl-paper-archive-interview@csnl-marketplace
/plugin marketplace update csnl-marketplace
/plugin install   csnl-paper-archive-interview@csnl-marketplace
```

Then set up your `.env`. Two options — pick whichever feels easier:

### Option A — interactive (recommended)

In a **real terminal** (not the Claude Code chat — `getpass` needs a
TTY to mask the password):

```
python ~/.claude/plugins/cache/csnl-marketplace/csnl-paper-archive-interview/*/scripts/setup.py
```

It walks you through 5 prompts (host / port / name / user / password /
schema), writes `~/.csnl-paper-archive/.env` with `chmod 600`, and
offers to verify the connection if you give your init.

### Option B — paste a heredoc

```
mkdir -p ~/.csnl-paper-archive && cat > ~/.csnl-paper-archive/.env <<EOF
SUPABASE_DB_HOST=<HOST_FROM_OPERATOR>
SUPABASE_DB_PORT=5432
SUPABASE_DB_NAME=postgres
SUPABASE_DB_USER=<USER_FROM_OPERATOR>
SUPABASE_DB_PASSWORD=<PASSWORD_FROM_OPERATOR>
CPR_LEDGER_SCHEMA=csnl_paper_rec
EOF
chmod 600 ~/.csnl-paper-archive/.env
```

That's it. Run with `/csnl-paper-archive-interview:paper-interview <YOUR_INIT>`.

If you ever forget where the `.env` lives or what was in it, just re-run
`setup.py` — it'll show the path and let you overwrite.

The plugin's `_pdb.py` enforces a strict table allowlist regardless of
DB role — so the shared admin credentials can't accidentally corrupt
the lab DB through this plugin. Full operator + researcher walkthrough
in [`docs/SHARED-DEPLOYMENT.md`](../docs/SHARED-DEPLOYMENT.md).

## Configure (alternative — interactive)

If you'd rather edit the .env in a text editor, copy
[`.env.example`](.env.example) to `~/.csnl-paper-archive/.env` and fill
in:

```
SUPABASE_DB_HOST=...
SUPABASE_DB_USER=...
SUPABASE_DB_PASSWORD=...
SUPABASE_DB_NAME=postgres
CPR_LEDGER_SCHEMA=csnl_paper_rec
```

Talk to the lab operator (`vnilab@gmail.com`) for credentials. Plugin
reads only the archive tables (`archive_papers`, `archive_filter_decisions`,
`archive_researcher_queues`) and writes only to `archive_responses`,
`archive_interview_sessions`, `archive_meta_reviews`, and
`archive_profile_verifications`. Your `csnl_research` rows are never
modified.

## Run

```
/csnl-paper-archive-interview:paper-interview BHL
```

(Replace `BHL` with your two- or three-letter init. The slash command is
namespaced under the plugin name — Claude Code does this automatically
for installed plugins. `/plugin list` shows what's available.)

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
