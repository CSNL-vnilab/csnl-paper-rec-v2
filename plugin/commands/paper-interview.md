---
name: paper-interview
description: >
  Start (or resume) a researcher's interactive interview over the CSNL
  paper archive. Verifies the researcher's interest profile, walks the
  pre-computed top-N queue in three age chunks (recent ≤5y → mid 5–10y
  → classic >10y), records one MCQ choice per paper, and runs a
  meta-review every 10 answers. Stores everything in
  csnl_paper_rec.archive_*. Usage: `/csnl-paper-archive-interview:paper-interview
  <init>` (e.g. `/csnl-paper-archive-interview:paper-interview BHL`).
  Idempotent — re-running resumes the open session.
argument-hint: <researcher-init>
---

You are running the **paper-archive-interview** for the researcher passed
as `$ARGUMENTS` (trim whitespace; default to asking for it if empty).

Strict execution rules:

1. Load the `paper-archive-interview` skill before doing anything else.
   Follow it exactly. The skill is the operating procedure; this command
   is just the entrypoint.

2. **All researcher-facing text is in Korean** (the lab's working
   language). Never include internal jargon (D1–D5 scores, "scout",
   "candidate", "tier", "composite", "BANNED_TERMS", project slugs in
   raw form, etc.) in messages to the researcher. Translate to plain
   Korean.

3. Never write to `csnl_research`. Reads from it are fine via the
   plugin scripts. Writes go only to the `archive_*` tables defined
   in `state/schema_archive.sql` of the parent repo.

4. Stop and ask the researcher whenever:
   - the queue is empty (`pick_next.py` returns `{"done": true}`),
   - the profile snapshot is empty (`profile_show.py` says
     `error: no_active_projects`),
   - the DB is unreachable (env not loaded),
   - the researcher types anything that is not 1–4 in response to an MCQ.

5. **Do not invent papers.** Every recommendation must come from a
   `pick_next.py` call. If the script returns nothing, stop and report.

6. Honor the researcher's pace. After each MCQ, present the next paper.
   Do not batch papers. Do not skip ahead.

Begin by running the skill.
