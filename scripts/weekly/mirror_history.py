#!/usr/bin/env python3
"""
scripts/weekly/mirror_history.py — auto-mirror the interview-result reading list
into the "논문 리스트" Notion database.

Single source of truth = csnl_paper_rec.archive_responses (every interview MCQ
answer AND every weekly Notion capture land here). This script reflects the
subset a researcher will-read / has-read into Notion:

    save_later   → 상태 "읽을 예정"  · 읽음 ☐ (unchecked)
    already_read → 상태 "이미 읽음"  · 읽음 ☑ (checked)

(not_relevant / skipped are intentionally NOT mirrored — the list is the
reading list only.) The 읽음 checkbox is the at-a-glance toggle; group the
Notion view by 상태 (or 읽음) for collapsible 읽을 예정 / 이미 읽음 sections.

Full two-way-consistent sync (idempotent): creates new pages, updates a page
whose status flipped (e.g. 읽을 예정 → 이미 읽음), and ARCHIVES pages whose paper
no longer qualifies (answer changed to not_relevant, or the response was
removed). Reads Postgres (archive_responses + archive_papers) READ-ONLY and
writes only Notion — no prod-DB write. Operator-run / cron; --apply gates the
Notion writes.

Usage:
    python3 scripts/weekly/mirror_history.py            # dry-run
    python3 scripts/weekly/mirror_history.py --apply     # sync
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _notion as N           # noqa: E402
import render                 # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
from _db import load_env, query_json, ledger_schema  # noqa: E402

# History DB property names — must match provision_notion.py _desired_schemas.
P_TITLE, P_RESEARCHER, P_STATUS, P_READ, P_DOI, P_CID = (
    "Title", "Researcher", "상태", "읽음", "DOI", "canonical_id")
STATUS_TO_READ, STATUS_READ = "읽을 예정", "이미 읽음"


def _desired(sch: str) -> dict[tuple, dict]:
    """{(researcher_id, canonical_id): {is_read, paper}} for every save_later /
    already_read response. Uses the archive_paper_status view (save_later→
    to_read, already_read→read)."""
    rows = query_json(f"""
        SELECT s.researcher_id, s.canonical_id, s.paper_status,
               p.title, p.authors_json, p.year, p.venue, p.doi
          FROM {sch}.archive_paper_status s
          JOIN {sch}.archive_papers p ON p.canonical_id = s.canonical_id
         WHERE s.paper_status IN ('to_read','read')
         ORDER BY s.researcher_id, s.paper_status
    """)
    out: dict[tuple, dict] = {}
    for r in rows:
        out[(r["researcher_id"], r["canonical_id"])] = {
            "is_read": r["paper_status"] == "read",
            "paper": r,
        }
    return out


def _existing(hid: str) -> dict[tuple, dict]:
    """{(researcher_id, canonical_id): {page_id, status, read}} from the DB."""
    out: dict[tuple, dict] = {}
    for pg in N.query_database(hid):
        cid = N.read_rich_text(pg, P_CID).strip()
        rid = N.read_select(pg, P_RESEARCHER) or ""
        if cid:
            out[(rid, cid)] = {
                "page_id": pg.get("id"),
                "status":  N.read_select(pg, P_STATUS),
                "read":    N.read_checkbox(pg, P_READ),
            }
    return out


def _props(rid: str, cid: str, is_read: bool, paper: dict) -> dict:
    return {
        P_TITLE:      N.p_title(render.apa_citation(paper)),
        P_RESEARCHER: N.p_select(rid),
        P_STATUS:     N.p_select(STATUS_READ if is_read else STATUS_TO_READ),
        P_READ:       N.p_checkbox(is_read),
        P_DOI:        N.p_url(render.doi_url(paper)),
        P_CID:        N.p_rich_text(cid),
    }


def _validate(hid: str) -> list[str]:
    db = N.retrieve_database(hid)
    have = {n: m.get("type") for n, m in (db.get("properties") or {}).items()}
    want = {P_RESEARCHER: "select", P_STATUS: "select", P_READ: "checkbox",
            P_DOI: "url", P_CID: "rich_text"}
    problems = []
    if "title" not in have.values():
        problems.append("no title property")
    for name, typ in want.items():
        if name not in have:
            problems.append(f"missing '{name}' ({typ})")
        elif have[name] != typ:
            problems.append(f"'{name}' is {have[name]}, want {typ}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    load_env()
    sch = ledger_schema()
    hid = N.history_db_id()
    if not hid:
        print("[mirror] NOTION_HISTORY_DB_ID not set — nothing to mirror.",
              file=sys.stderr)
        return 2
    problems = _validate(hid)
    if problems:
        print("[mirror] ABORT — 논문 리스트 schema invalid:", file=sys.stderr)
        for p in problems:
            print(f"    - {p}", file=sys.stderr)
        print("    fix: python3 scripts/weekly/provision_notion.py --db history --apply",
              file=sys.stderr)
        return 2

    desired = _desired(sch)
    # Transparency: a qualifying response whose canonical_id has no
    # archive_papers row cannot be rendered (no title), so _desired's JOIN drops
    # it. Surface the gap rather than silently under-mirroring.
    total_q = int(query_json(
        f"SELECT count(*) n FROM {sch}.archive_paper_status "
        f"WHERE paper_status IN ('to_read','read')")[0]["n"])
    if len(desired) < total_q:
        print(f"[mirror] ⚠ {total_q - len(desired)} qualifying response(s) skipped "
              f"— no matching archive_papers row (cannot render a title).")
    existing = _existing(hid)

    creates = [k for k in desired if k not in existing]
    updates, archives = [], []
    for k, e in existing.items():
        if k not in desired:
            archives.append(k)
            continue
        want_read = desired[k]["is_read"]
        want_status = STATUS_READ if want_read else STATUS_TO_READ
        if e["read"] != want_read or e["status"] != want_status:
            updates.append(k)

    def _split(keys):
        rr = sum(1 for k in keys if desired.get(k, {}).get("is_read"))
        return f"{len(keys)} ({rr} 이미읽음 / {len(keys)-rr} 읽을예정)"

    print(f"[mirror] desired={len(desired)}  existing={len(existing)}  → "
          f"create {_split(creates)} · update {len(updates)} · archive {len(archives)}")
    if not args.apply:
        for k in creates[:6]:
            rid, cid = k
            print(f"  + {rid} {'☑' if desired[k]['is_read'] else '☐'} "
                  f"{render.apa_citation(desired[k]['paper'])[:80]}")
        if len(creates) > 6:
            print(f"  … and {len(creates)-6} more creates")
        print("[mirror] dry-run only. Re-run with --apply to sync Notion.")
        return 0

    created = updated = archived = failed = 0
    for k in creates:
        rid, cid = k
        try:
            N.create_page(hid, _props(rid, cid, desired[k]["is_read"], desired[k]["paper"]))
            created += 1
        except Exception as e:
            failed += 1
            print(f"[mirror] create FAIL {rid}/{cid[:10]}: {type(e).__name__}: "
                  f"{str(e)[:160]}", file=sys.stderr)
    for k in updates:
        rid, cid = k
        try:
            N.update_page(existing[k]["page_id"],
                          _props(rid, cid, desired[k]["is_read"], desired[k]["paper"]))
            updated += 1
        except Exception as e:
            failed += 1
            print(f"[mirror] update FAIL {rid}/{cid[:10]}: {type(e).__name__}: "
                  f"{str(e)[:160]}", file=sys.stderr)
    for k in archives:
        try:
            N._request("PATCH", f"/pages/{existing[k]['page_id']}",
                       json_body={"archived": True})
            archived += 1
        except Exception as e:
            failed += 1
            print(f"[mirror] archive FAIL {k}: {type(e).__name__}: {str(e)[:160]}",
                  file=sys.stderr)
    print(f"[mirror] done — created={created} updated={updated} "
          f"archived={archived} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
