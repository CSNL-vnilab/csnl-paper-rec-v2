---
description: Prime the current Claude session with a CSNL researcher's research context from the archive interview DB (projects, fingerprint vocabulary, dim_preferences, recent reads, will-reads, exclusions). Read-only; <1s. Use at the start of any session that helps that researcher with their work.
argument-hint: "<researcher-init>  # e.g. JOP / BYL / MSY / SMJ / JYK / BHL"
---

## /csnl-paper-archive-interview:paper-context $ARGUMENTS

You are about to help a CSNL researcher (`$ARGUMENTS`). Before answering anything, prime yourself with the persistent research context the paper-archive interview has accumulated in Postgres. This is the **payoff** of every MCQ they have ever answered.

### What to do

1. Trim `$ARGUMENTS`, uppercase it, and store it as `RID`. If empty, ask the researcher for their initials and stop until they reply.

2. Run, exactly once:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/../scripts/archive/get_researcher_context.py" "$RID" --human
   ```
   The script reads csnl_paper_rec + csnl_research and prints a Korean-formatted summary.

3. After it prints, **silently absorb the context as your working knowledge of this researcher**. Do NOT echo the full payload back to them — they wrote most of it. Instead, give a short Korean confirmation (≤2 sentences) like:
   > "JOP 연구원님의 진행 중인 4개 프로젝트(Time2Dist, RingRepSca, GranRDT, GranNMDS), 최근 읽은 paper 12편, 차원 선호(F-NIM 강함, M-RSA 강함) 모두 로드했습니다. 어떤 부분을 도와드릴까요?"

4. From this point on, every recommendation, summary, or follow-up you give MUST be informed by the loaded context:
   - Reference their actual projects when explaining how a paper relates to their work.
   - Avoid suggesting papers in their `recent_not_relevant` cluster unless the researcher explicitly asks.
   - When asked "what should I read next?" prefer papers in `recent_save_later` ("읽을 예정") OR pull from the latest weekly batch in `archive_weekly_picks`.
   - If the researcher describes a NEW project area not in `projects`, treat it as a real diff and suggest they update their `csnl_research.projects` row at session end.

5. Do not write to the DB from this command. The context is read-only here; updates flow through the interview command (`paper-interview`).

### Boundaries

- Korean researcher-facing text only. The `--human` output is already Korean; preserve scientific English terms (e.g., "efficient coding", "RSA") verbatim.
- Never reveal internal codes (F-NIM, M-RSA, S-FAC, U-HUM) in researcher-facing messages. Translate to Korean labels.
- Never page another researcher's context from this session. `RID` is locked to `$ARGUMENTS`.

Begin by running the script.
