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
Notion write, but WHAT blocks is narrow, on two axes (P34/L3-1):

  * by severity — only identity/signature markers (`— claude`, `anthropic`,
    `gpt-5`, `as an ai`, coined internal-ops tokens) are fatal. Style/hype words
    (`robust`, `holistic`, `leverage`, `훌륭`) warn and send.
  * by provenance — only text this harness/its offline agents composed is
    eligible to be fatal. The paper's own title, authors, venue and abstract
    excerpt are quoted third-party science: reported, never blocking, exactly as
    rules/01:16 intends ("never false-positives on a legitimate paper title or
    author" — an author named Claude, a paper about GPT).

Pass --strict-lint to promote every advisory hit to fatal (operator triage; it
re-creates the over-blocking, so it is off by default).
If rules/01_tone.md cannot be read the whole run aborts (rc=2, fail-closed).

Usage:
    python3 scripts/weekly/send_notion.py                 # dry-run (all unsent)
    python3 scripts/weekly/send_notion.py --apply         # create + link
    python3 scripts/weekly/send_notion.py --week 2026-W23 --apply
    python3 scripts/weekly/send_notion.py --strict-lint   # advisory hits fatal too
    python3 scripts/weekly/send_notion.py --self-test     # offline; no DB/Notion
"""
from __future__ import annotations

import argparse
import json
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

# Prefixes render.recommendation_ko() uses when it has NO synopsis and falls
# back to the paper's own abstract (render.py:163-171). Belt-and-braces: if the
# rendered text starts with one of these, it is quoted source text no matter
# what the row columns say.
_QUOTE_PREFIXES = ("📄 초록 발췌:", "(시놉시스")


def _seq(v) -> list:
    """list-ish view of a jsonb column (psycopg2 gives a list, the psql
    fallback a JSON string). Local, so this file does not reach into render's
    privates."""
    if v is None:
        return []
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return [v]
    return list(v) if isinstance(v, (list, tuple)) else [v]


def _rationale_is_synopsis(row: dict) -> bool:
    """True when render.recommendation_ko() renders SYNOPSIS text.

    Mirrors that function's own condition (render.py:154-172): it emits synopsis
    lines when any of core_question / key_findings / frameworks /
    connecting_signals has content, and otherwise falls back to a verbatim
    abstract excerpt (or a static "no information" notice).

    That distinction is the whole provenance question. The synopsis was written
    by the offline Opus fan-out (P21/P22c) — harness-side prose, so an identity
    leak there is real. The abstract excerpt is the paper's own words quoted, so
    it must never be able to block a send: rules/01:16 explicitly protects "a
    paper about GPT", and 18 live queue rows currently take that fallback path.
    """
    if (row.get("core_question") or "").strip():
        return True
    for key in ("key_findings", "connecting_signals"):
        if any(str(x).strip() for x in _seq(row.get(key))):
            return True
    return any(isinstance(fw, dict) and (fw.get("name") or "").strip()
               for fw in _seq(row.get("frameworks")))


def _visible_text(row: dict) -> tuple[dict[str, str], dict[str, str]]:
    """Split every researcher-visible string on this row into (authored, quoted).

    authored — text this harness or its offline agents composed: the Korean
      rationale, when it is rendered from the per-paper synopsis. This is where
      a model self-reference could actually leak, so it is the only bucket that
      may produce a FATAL violation.
    quoted — third-party text reproduced as-is: the paper's own title, author
      list, APA citation (which carries the title and venue), and the abstract
      excerpt when the rationale falls back to it. rules/01:16 exempts these
      from the hard set, so hits here warn instead of blocking unless
      --strict-lint.
    """
    quoted = {
        "title":  render.paper_title(row),
        "저자":    render.authors_str(row),
        "APA":    render.apa_citation(row),
    }
    authored: dict[str, str] = {}
    rationale = render.recommendation_ko(row)
    # If render.py ever changes its fallback condition, fail toward "quoted":
    # a mis-bucketed row must warn, never wedge a researcher's slot.
    if _rationale_is_synopsis(row) and not rationale.startswith(_QUOTE_PREFIXES):
        authored["추천 근거"] = rationale
    else:
        quoted["추천 근거(초록 인용)"] = rationale
    return authored, quoted


def _lint_row(row: dict, banned, strict: bool = False
              ) -> tuple[list[T.Violation], list[T.Violation]]:
    """-> (fatal, advisory). Pure; no I/O.

    fatal    = identity/signature markers in agent-authored prose ONLY.
    advisory = everything else: style/hype words anywhere, and any hit at all in
               quoted bibliographic text. Reported, never blocking.

    `banned` may be a raw term list or a pre-computed T.TermSplit.
    """
    sp = T.as_split(banned)
    authored, quoted = _visible_text(row)
    fatal = T.check_fields(authored, sp.fatal, severity=T.FATAL)
    advisory = (T.check_fields(authored, sp.advisory)
                + T.check_fields(quoted, sp.all))
    if strict:
        return fatal + advisory, []
    return fatal, advisory


def _row_label(row: dict) -> str:
    return f"{row.get('researcher_id')}/{str(row.get('canonical_id') or '')[:10]}"


def _self_test() -> int:
    """Offline assertions for the lint provenance/severity contract (P34/L3-1).

    No DB, no Notion, no .env — safe to run anywhere, including CI.
    """
    if not __debug__:                 # python -O strips every assert below
        print("[send] --self-test refuses to run under -O/PYTHONOPTIMIZE: the "
              "assertions would be stripped and it would 'pass' vacuously.",
              file=sys.stderr)
        return 2
    banned = T.split_terms()          # fail-closed if rules/01 is unreadable
    assert banned.fatal and banned.advisory

    # A real row from the live board that the first port of the lint BLOCKED.
    real = {
        "researcher_id": "SMJ", "canonical_id": "0" * 32, "tier_at_send": "S",
        "title": "FixGrower: An efficient and robust curriculum for shaping "
                 "fixation behaviour",
        "authors_json": ["Jane Roe"], "year": 2024, "venue": "eLife",
        "doi": "10.0000/x",
        "core_question": "고정 행동을 형성하는 커리큘럼이 robust 한가?",
        "key_findings": ["holistic 한 학습 곡선이 관찰됨"],
        "connecting_signals": ["fixation", "curriculum"],
        "frameworks": [{"name": "holistic matching model",
                        "role": "primary_lens"}],
    }
    fatal, adv = _lint_row(real, banned)
    assert fatal == [], f"paper vocabulary must not block a send: {fatal}"
    assert {v.term for v in adv} >= {"robust", "holistic"}, adv
    assert all(v.severity == T.ADVISORY for v in adv), adv

    # …but an identity leak in the agent-authored rationale still hard-blocks.
    for probe in ("\n— Claude", " As an AI, I cannot ", " anthropic ",
                  " (claude) ", " 언어모델로서 ", " q_hash "):
        leak = dict(real, core_question=real["core_question"] + probe)
        f, _ = _lint_row(leak, banned)
        assert f, f"identity leak not blocked: {probe!r}"
        assert all(v.severity == T.FATAL and v.field == "추천 근거" for v in f), f

    # The paper's OWN title/authors/abstract can carry any term without blocking
    # (rules/01:16 — an author named Claude, a paper about GPT).
    meta = dict(real,
                title="GPT-4 as a robust holistic model of anthropic reasoning",
                authors_json=["Claude Shannon", "Anthropic Author"])
    f, a = _lint_row(meta, banned)
    assert f == [], f"quoted bibliographic text must never block: {f}"
    assert {v.term for v in a} >= {"gpt-4", "anthropic", "robust"}, a

    # No synopsis -> the rationale IS the paper's abstract, quoted. Still no block.
    fallback = {"researcher_id": "X", "canonical_id": "1" * 32, "title": "T",
                "authors_json": [], "abstract": "We show a robust, holistic "
                "evaluation of GPT-4 and Claude Opus as an AI reviewer."}
    assert not _rationale_is_synopsis(fallback)
    authored, quoted = _visible_text(fallback)
    assert authored == {}, authored
    assert "추천 근거(초록 인용)" in quoted, quoted
    f, a = _lint_row(fallback, banned)
    assert f == [], f"abstract excerpt must never block a send: {f}"
    assert {v.term for v in a} >= {"claude opus", "gpt-4", "as an ai"}, a
    assert _rationale_is_synopsis(real)

    # Drift alarm: our column-level condition must agree with what render.py
    # actually emits, on both branches (marker prefixes = the fallback path).
    for _row in (real, fallback, {}, {"key_findings": ["x"]},
                 {"frameworks": [{"name": "n"}]}, {"connecting_signals": ["s"]},
                 {"frameworks": [{"role": "context"}]}):
        _txt = render.recommendation_ko(_row)
        assert _rationale_is_synopsis(_row) is not _txt.startswith(_QUOTE_PREFIXES), (
            "render.recommendation_ko() no longer agrees with "
            f"_rationale_is_synopsis(); re-sync the provenance split: {_row}")
    assert _seq('["a","b"]') == ["a", "b"] and _seq(None) == [] and _seq("x") == ["x"]

    # --strict-lint remains available as the operator's escape hatch.
    f, a = _lint_row(real, banned, strict=True)
    assert f and a == [], (f, a)

    # A raw term list is still accepted (back-compat with T.load_banned_terms()).
    f2, a2 = _lint_row(real, T.load_banned_terms())
    assert f2 == [] and {v.term for v in a2} == {v.term for v in adv}

    print("send_notion.py self-test OK — fatal set is identity-only and scoped "
          "to agent-authored prose; quoted paper text cannot block a send.")
    return 0


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
                    help="Promote every advisory hit (style/hype words, and any "
                         "hit in quoted title/authors/APA/abstract) to fatal. "
                         "Default: only identity/signature markers in "
                         "agent-authored prose block a send — rules/01:16 "
                         "curates the set to not false-positive on legitimate "
                         "paper metadata, and blocking a real paper wedges the "
                         "researcher's slot (P34/L3-1).")
    ap.add_argument("--self-test", action="store_true",
                    help="Run the offline lint-provenance assertions and exit. "
                         "Touches no DB, no Notion, no .env.")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()
    load_env()
    sch = ledger_schema()
    db_id = N.digest_db_id()
    props = N.digest_props()

    # Tone backstop (rules/01_tone.md). Fail CLOSED: an unreadable rules file
    # must never degrade into "lint skipped" on the one live researcher channel.
    try:
        banned = T.split_terms()
    except T.ToneLintUnavailable as e:
        print(f"[send] ABORT — tone lint unavailable: {e}", file=sys.stderr)
        print("    rules/01_tone.md must carry a fenced ```BANNED_TERMS``` block "
              "(rules/01:76). Refusing to write to Notion without the backstop.",
              file=sys.stderr)
        return 2
    print(f"[send] tone lint armed — {len(banned)} banned term(s) from "
          f"rules/01_tone.md: {len(banned.fatal)} fatal (identity/signature, "
          f"agent-authored text only) / {len(banned.advisory)} advisory"
          + (" [strict: advisory hits block too]" if args.strict_lint else ""))

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
                print(f"[send] lint warn (non-blocking) {_row_label(r)}",
                      file=sys.stderr)
                print(T.format_violations(advisory), file=sys.stderr)
        print(f"[send] tone lint: {n_fatal} row(s) would be BLOCKED, "
              f"{n_warn} with non-blocking warnings.")
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
            print(f"[send] lint warn (non-blocking) {_row_label(r)}",
                  file=sys.stderr)
            print(T.format_violations(advisory), file=sys.stderr)
        if fatal:
            blocked += 1
            print(f"[send] LINT BLOCK — NOT SENT {_row_label(r)} "
                  f"(rules/01_tone.md BANNED_TERMS)", file=sys.stderr)
            print(T.format_violations(fatal), file=sys.stderr)
            print("    This row keeps notion_page_id IS NULL, and build_digest "
                  "still counts it as an ACTIVE slot — the researcher's board "
                  "silently runs short until the source synopsis is fixed. Do "
                  "not clear it by relaxing the lint.", file=sys.stderr)
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
