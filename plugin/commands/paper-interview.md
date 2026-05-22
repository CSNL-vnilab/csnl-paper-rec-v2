---
description: Start (or resume) the CSNL paper-archive interview for a researcher. Verifies the profile, walks a top-N queue (3 age chunks), records one MCQ per paper, and runs a meta-review every 10 answers. Stores everything in csnl_paper_rec.archive_*. Korean researcher-facing text only; no send paths.
argument-hint: "<researcher-init>  # e.g. JOP / BYL / MSY / SMJ / JYK / BHL"
---

## /csnl-paper-archive-interview:paper-interview $ARGUMENTS

You are running the **paper-archive-interview** for the researcher passed as `$ARGUMENTS` (trim whitespace; default to asking for it if empty; uppercase before any script call).

### Strict execution rules

1. Load the `paper-archive-interview` skill before doing anything else. Follow it exactly. The skill is the operating procedure; this command is just the entrypoint.

2. **All researcher-facing text is in Korean** (the lab's working language). Never include internal jargon (D1–D5 scores, "scout", "candidate", "tier", "composite", "BANNED_TERMS", project slugs in raw form, taxonomy codes like `F-BAY`/`M-RSA`/`S-FAC`/`U-HUM`, chunk codes like `recent`/`mid`/`classic`, etc.) in messages to the researcher. Translate every tag/code into a plain Korean phrase using `data/taxonomy.json` `label_ko` or the tag-rendering table in the skill.

3. Never write to `csnl_research`. Reads from it are fine via the plugin scripts. Writes go only to the `archive_*` tables defined in `state/schema_archive.sql` of the parent repo, and only via the 4 plugin-writeable allowlist tables enforced by `_pdb.py`.

4. Stop and ask the researcher whenever:
   - the queue is empty (`pick_next.py` returns `{"done": true}`),
   - the profile snapshot is empty (`profile_show.py` says `error: no_active_projects`),
   - the DB is unreachable (env not loaded),
   - the researcher types anything that is not a recognized MCQ signal.

5. **Do not invent papers.** Every recommendation must come from a `pick_next.py` call. If the script returns nothing, stop and report.

6. Honor the researcher's pace. After each MCQ, present the next paper. Do not batch papers. Do not skip ahead.

Begin by running the skill.
