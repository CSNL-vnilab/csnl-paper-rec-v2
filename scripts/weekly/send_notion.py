#!/usr/bin/env python3
"""
scripts/weekly/send_notion.py — push staged digest rows into the Notion
"이번 주 논문 추천" database and write the resulting page id back to
archive_weekly_digests.notion_page_id.

Design §6. Runs right after build_digest.py in the weekly chain. Reads digest
rows with notion_page_id IS NULL, renders each into an APA title + Korean
rationale (scripts/weekly/render.py — deterministic, no LLM), creates one
Notion row per paper, and links it back.

Idempotency / self-heal: before creating, it loads every existing page in the
digest DB and keys them by (researcher, week, canonical_id). A staged row whose
page already exists (e.g. a prior run created the page but crashed before the
DB write-back) is RELINKED rather than duplicated. So re-running never sends a
researcher the same paper twice in a week.

Boundary: writes to Notion (researcher-facing) — gated behind --apply, like
Slack delivery. The page-id write-back touches only archive_weekly_digests;
archive_responses is never touched here. Operator-run.

Tone lint (rules/01_tone.md): this is the only channel that currently reaches a
researcher, so it carries the mechanical BANNED_TERMS backstop that rules/01:12
declares (see scripts/weekly/_tone_lint.py — all five older parser copies sit on
the retired Slack path). Every researcher-visible string is checked BEFORE the
Notion write. A hit in harness-authored prose (the Korean rationale, assembled
from the Opus-generated synopsis) ABORTS that row loudly and leaves it unsent;
a hit confined to verbatim bibliographic metadata is reported as a warning,
because rules/01:16 curates the set precisely so it "never false-positives on a
legitimate paper title or author" — pass --strict-lint to abort on those too.
If rules/01_tone.md cannot be read the whole run aborts (rc=2, fail-closed).

Usage:
    python3 scripts/weekly/send_notion.py                 # dry-run (all unsent)
    python3 scripts/weekly/send_notion.py --apply         # create + link
    python3 scripts/weekly/send_notion.py --week 2026-W23 --apply
    python3 scripts/weekly/send_notion.py --strict-lint   # metadata hits fatal too
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _notion as N           # noqa: E402
import _tone_lint as T        # noqa: E402
import render                 # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
from _db import load_env, query_json, exec_many, ledger_schema  # noqa: E402

_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")


def _unsent_rows(sch: str, week: str | None) -> list[dict]:
    wclause = ""
    if week:
        # real validation (not assert — survives python -O); week is inlined.
        if not _WEEK_RE.match(week):
            raise ValueError(f"unsafe week (not /^\\d{{4}}-W\\d{{2}}$/): {week!r}")
        wclause = f"AND w.week_iso = '{week}'"
    return query_json(f"""
        SELECT w.digest_id, w.week_iso, w.researcher_id, w.canonical_id,
               w.tier_at_send, w.rank_in_digest, w.sent_at,
               COALESCE(c.channel_target, w.researcher_id) AS researcher_label,
               p.title, p.year, p.venue, p.doi, p.authors_json, p.abstract,
               s.core_question, s.key_findings, s.connecting_signals, s.frameworks
          FROM {sch}.archive_weekly_digests w
          JOIN {sch}.archive_papers p
            ON p.canonical_id = w.canonical_id
          LEFT JOIN {sch}.archive_paper_synopses s
            ON s.canonical_id = w.canonical_id
          LEFT JOIN {sch}.archive_researcher_channels c
            ON c.researcher_id = w.researcher_id AND c.channel_type = 'notion'
         WHERE w.notion_page_id IS NULL
           {wclause}
         ORDER BY w.week_iso, w.researcher_id, w.rank_in_digest
    """)


def _existing_page_map(db_id: str, props: dict) -> dict[tuple, str]:
    """{(researcher_label, canonical_id): page_id} for every page already in the
    digest DB — used to avoid duplicate creates (one page per researcher×paper
    in the rolling model)."""
    out: dict[tuple, str] = {}
    for page in N.query_database(db_id):
        cid = N.read_rich_text(page, props["canonical_id"]).strip()
        researcher = N.read_select(page, props["researcher"]) or ""
        if cid:
            out[(researcher, cid)] = page.get("id")
    return out


def _properties(row: dict, props: dict) -> dict:
    # Bibliographic fields split: Title (paper title) / 저자 / APA. The
    # researcher action is the 읽음 checkbox — created unchecked.
    return {
        props["title"]:          N.p_title(render.paper_title(row)),
        props["authors"]:        N.p_rich_text(render.authors_str(row)),
        props["apa"]:            N.p_rich_text(render.apa_citation(row)),
        props["researcher"]:     N.p_select(row.get("researcher_label")),
        props["tier"]:           N.p_select(row.get("tier_at_send")),
        props["read"]:           N.p_checkbox(False),
        props["present"]:        N.p_checkbox(False),
        props["recommendation"]: N.p_rich_text(render.recommendation_ko(row)),
        props["doi"]:            N.p_url(render.doi_url(row)),
        props["sent_at"]:        N.p_date((row.get("sent_at") or None)),
        props["canonical_id"]:   N.p_rich_text(row.get("canonical_id") or ""),
    }


# --------------------------------------------------------------- tone backstop

def _visible_text(row: dict) -> tuple[dict[str, str], dict[str, str]]:
    """Split every researcher-visible string on this row into
    (authored, verbatim).

    authored — text this harness composed. `recommendation_ko` is assembled from
      the per-paper synopsis, which an Opus fan-out wrote (P21/P22c), so it is
      exactly where a model self-reference or AI-jargon token could leak.
    verbatim — third-party bibliographic data reproduced as-is. rules/01:16
      exempts these from the hard set (an author named Claude, a paper about
      GPT), so hits here warn instead of blocking, unless --strict-lint.
    """
    authored = {
        "추천 근거": render.recommendation_ko(row),
    }
    verbatim = {
        "title":  render.paper_title(row),
        "저자":    render.authors_str(row),
        "APA":    render.apa_citation(row),
    }
    return authored, verbatim


def _lint_row(row: dict, banned: list[str], strict: bool
              ) -> tuple[list[T.Violation], list[T.Violation]]:
    """-> (fatal, advisory). Pure; no I/O."""
    authored, verbatim = _visible_text(row)
    fatal = T.check_fields(authored, banned)
    meta = T.check_fields(verbatim, banned)
    if strict:
        return fatal + meta, []
    return fatal, meta


def _row_label(row: dict) -> str:
    return f"{row.get('researcher_id')}/{str(row.get('canonical_id') or '')[:10]}"


def _writeback(sch: str, digest_id: int, page_id: str) -> None:
    exec_many(
        f"UPDATE {sch}.archive_weekly_digests SET notion_page_id = %s "
        f"WHERE digest_id = %s",
        [(page_id, digest_id)],
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--week", default=None,
                    help="Restrict to one ISO week (e.g. 2026-W23). Default: ALL "
                         "unsent rows — in the rolling model every staged-but-"
                         "unsent row is an active slot that needs a Notion page, "
                         "so leaving any unsent would wedge that slot.")
    ap.add_argument("--strict-lint", action="store_true",
                    help="Treat a BANNED_TERMS hit in verbatim bibliographic "
                         "metadata (title/authors/APA) as fatal too. Default: "
                         "those warn only — rules/01:16 curates the set to not "
                         "false-positive on legitimate paper metadata.")
    args = ap.parse_args()
    load_env()
    sch = ledger_schema()
    db_id = N.digest_db_id()
    props = N.digest_props()

    # Tone backstop (rules/01_tone.md). Fail CLOSED: an unreadable rules file
    # must never degrade into "lint skipped" on the one live researcher channel.
    try:
        banned = T.load_banned_terms()
    except T.ToneLintUnavailable as e:
        print(f"[send] ABORT — tone lint unavailable: {e}", file=sys.stderr)
        print("    rules/01_tone.md must carry a fenced ```BANNED_TERMS``` block "
              "(rules/01:76). Refusing to write to Notion without the backstop.",
              file=sys.stderr)
        return 2
    print(f"[send] tone lint armed — {len(banned)} banned term(s) from "
          f"rules/01_tone.md" + (" [strict]" if args.strict_lint else ""))

    # Hard gate: never POST against a mis-shaped DB. Also learn the response
    # property's actual type so we build the right value shape.
    ok, problems = N.validate_digest_db(db_id)
    if not ok:
        print("[send] ABORT — digest DB schema invalid:", file=sys.stderr)
        for p in problems:
            print(f"    - {p}", file=sys.stderr)
        print("    fix with: python3 scripts/weekly/provision_notion.py --db digest --apply",
              file=sys.stderr)
        return 2

    week = args.week   # None = ALL unsent rows (rolling-model default)
    rows = _unsent_rows(sch, week)
    scope = "all weeks" if week is None else f"week {week}"
    if not rows:
        print(f"[send] no unsent digest rows ({scope}, notion_page_id IS NULL). "
              f"Nothing to do.")
        return 0
    print(f"[send] {len(rows)} unsent row(s) for {scope}.")

    if not args.apply:
        for r in rows[:8]:
            print(f"  • {r['researcher_id']} #{r['rank_in_digest']} "
                  f"[{r['tier_at_send']}] {render.apa_citation(r)[:90]}")
        if len(rows) > 8:
            print(f"  … and {len(rows) - 8} more")
        # Lint every row (not just the previewed 8) so the operator sees what
        # --apply would block before running it.
        n_fatal = n_warn = 0
        for r in rows:
            fatal, advisory = _lint_row(r, banned, args.strict_lint)
            if fatal:
                n_fatal += 1
                print(f"[send] LINT BLOCK {_row_label(r)}", file=sys.stderr)
                print(T.format_violations(fatal), file=sys.stderr)
            if advisory:
                n_warn += 1
                print(f"[send] lint warn (metadata) {_row_label(r)}",
                      file=sys.stderr)
                print(T.format_violations(advisory), file=sys.stderr)
        print(f"[send] tone lint: {n_fatal} row(s) would be BLOCKED, "
              f"{n_warn} with metadata warnings.")
        print("[send] dry-run only. Re-run with --apply to create Notion rows.")
        return 1 if n_fatal else 0

    existing = _existing_page_map(db_id, props)
    created = relinked = failed = blocked = 0
    for r in rows:
        key = (r.get("researcher_label") or "", r["canonical_id"])
        # Tone backstop — runs BEFORE any Notion write and before the relink
        # write-back, so a blocked row is neither published nor blessed. The row
        # stays notion_page_id IS NULL and is retried next run; the fix is to
        # correct the source synopsis, not to bypass this.
        fatal, advisory = _lint_row(r, banned, args.strict_lint)
        if advisory:
            print(f"[send] lint warn (metadata) {_row_label(r)}", file=sys.stderr)
            print(T.format_violations(advisory), file=sys.stderr)
        if fatal:
            blocked += 1
            print(f"[send] LINT BLOCK — NOT SENT {_row_label(r)} "
                  f"(rules/01_tone.md BANNED_TERMS)", file=sys.stderr)
            print(T.format_violations(fatal), file=sys.stderr)
            if key in existing:
                print(f"    NOTE: a Notion page already exists for this row "
                      f"({existing[key]}); it was NOT relinked. Remove or edit "
                      f"that page manually.", file=sys.stderr)
            continue
        try:
            if key in existing:
                page_id = existing[key]
                _writeback(sch, r["digest_id"], page_id)
                relinked += 1
            else:
                page = N.create_page(db_id, _properties(r, props))
                page_id = page.get("id")
                if not page_id:
                    raise N.NotionError("create returned no page id")
                _writeback(sch, r["digest_id"], page_id)
                created += 1
        except Exception as e:  # keep going; one bad row must not abort the batch
            failed += 1
            print(f"[send] FAIL {r['researcher_id']}/{r['canonical_id'][:10]}: "
                  f"{type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
    print(f"[send] done — created={created} relinked={relinked} "
          f"failed={failed} lint_blocked={blocked}")
    return 1 if (failed or blocked) else 0


if __name__ == "__main__":
    raise SystemExit(main())
