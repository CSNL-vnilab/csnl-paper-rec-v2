#!/usr/bin/env python3
"""
scripts/weekly/publish_md_page.py — publish an arbitrary Markdown file as a
single Notion content page under the integration-owned "CSNL 논문 추천" parent.

Reuses notion_survey_pages' Markdown→Notion-block converter (headings, tables,
bullets, quotes, dividers, inline bold/italic/code, flag colour-coding, link
detection) so operator-facing analysis pages render natively. Idempotent:
re-running replaces the same-titled child page's content (clear + re-append),
so it never duplicates.

BOUNDARY: this writes to the OPERATOR's Notion workspace (the same "CSNL 논문
추천" parent the surveys are staged under — NOT shared with researchers, NOT a
send). Default is DRY-RUN; --apply is the only path that POSTs. No DB writes.

CLI:
    python3 scripts/weekly/publish_md_page.py FILE.md --title "..."          # dry-run
    python3 scripts/weekly/publish_md_page.py FILE.md --title "..." --apply  # create/replace
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import _notion                       # noqa: E402
import notion_survey_pages as N      # noqa: E402 — reuse the md→blocks converter


def build_blocks(md: str) -> tuple[list, list]:
    warnings: list[str] = []
    blocks = N.markdown_to_blocks(md, warnings)   # NOTE: no preprocess() — keep full doc
    N._scrub_block_scaffolding(blocks)            # clean residue + colour flags + linkify
    blocks = N._insert_section_dividers(blocks)   # divider before each h2/h3
    return blocks, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("md_file")
    ap.add_argument("--title", required=True)
    ap.add_argument("--container", default=None,
                    help="Optional container page title under the CSNL parent; "
                         "the doc is nested under it (created if absent).")
    ap.add_argument("--icon", default=None,
                    help="Optional emoji to set as the page icon (e.g. 🗺️).")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    md = Path(args.md_file).read_text("utf-8")
    blocks, warnings = build_blocks(md)
    types: dict[str, int] = {}
    for b in blocks:
        types[b.get("type")] = types.get(b.get("type"), 0) + 1
    print(f"[publish] {args.md_file} → {len(blocks)} blocks  {types}")
    if warnings:
        print(f"[publish] {len(warnings)} converter warning(s):")
        for w in warnings[:8]:
            print("    -", w)

    if not args.apply:
        where = f"container {args.container!r}" if args.container else "the 'CSNL 논문 추천' parent"
        print(f"[publish] DRY-RUN — would create/replace page {args.title!r} "
              f"under {where}. Re-run with --apply.")
        return 1 if warnings else 0

    pid, ptitle = N.resolve_parent_page()
    print(f"[publish] CSNL parent: {pid}  {ptitle!r}")
    if args.container:
        cont = _notion.find_child_page_by_title(pid, args.container)
        if cont:
            pid = cont["id"]
            print(f"[publish] container exists: {pid}  {args.container!r}")
        else:
            pid = _notion.create_child_page(pid, args.container)["id"]
            print(f"[publish] container created: {pid}  {args.container!r}")
    existing = _notion.find_child_page_by_title(pid, args.title)
    if existing:
        page_id = existing["id"]
        removed = N._clear_page(page_id)
        print(f"[publish] replacing {page_id} (cleared {removed} blocks)")
    else:
        created = _notion.create_child_page(pid, args.title)
        page_id = created["id"]
        print(f"[publish] created {page_id}")
    if args.icon:
        _notion.set_page_icon(page_id, args.icon)
        print(f"[publish] set page icon {args.icon!r}")
    appended = N._append_blocks_chunked(page_id, blocks)
    print(f"[publish] appended {appended} blocks → page {page_id}")
    print(f"[publish] done: {args.title!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
