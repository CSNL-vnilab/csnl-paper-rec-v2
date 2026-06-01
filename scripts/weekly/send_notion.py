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

Usage:
    python3 scripts/weekly/send_notion.py                 # dry-run (all unsent)
    python3 scripts/weekly/send_notion.py --apply         # create + link
    python3 scripts/weekly/send_notion.py --week 2026-W23 --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _notion as N           # noqa: E402
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
        props["recommendation"]: N.p_rich_text(render.recommendation_ko(row)),
        props["doi"]:            N.p_url(render.doi_url(row)),
        props["sent_at"]:        N.p_date((row.get("sent_at") or None)),
        props["canonical_id"]:   N.p_rich_text(row.get("canonical_id") or ""),
    }


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
    args = ap.parse_args()
    load_env()
    sch = ledger_schema()
    db_id = N.digest_db_id()
    props = N.digest_props()

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
        print("[send] dry-run only. Re-run with --apply to create Notion rows.")
        return 0

    existing = _existing_page_map(db_id, props)
    created = relinked = failed = 0
    for r in rows:
        key = (r.get("researcher_label") or "", r["canonical_id"])
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
    print(f"[send] done — created={created} relinked={relinked} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
