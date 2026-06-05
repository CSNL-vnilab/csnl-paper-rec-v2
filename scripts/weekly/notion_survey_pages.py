#!/usr/bin/env python3
"""
scripts/weekly/notion_survey_pages.py — publish each researcher's research-
profile survey (state/archive/surveys/<INIT>.md) as an individual Notion page.

WHY: the 7 surveys are authored in Markdown but need to live where researchers
actually look — under the "CSNL 논문 추천" Notion page the integration owns. This
script converts Markdown → Notion blocks and (only under --apply) creates one
child page per researcher, idempotently, beneath a shared container page.

BOUNDARY (same ethos as the rest of the weekly harness):
  - Default is DRY-RUN. It parses + reports and creates NOTHING.
  - --apply is the only path that POSTs to Notion. There is no DB write here at
    all (surveys are files; Notion is the only external target).
  - .env is read-only; the token is never printed.

The RESEARCHER-FACING page is intro + sections A–I only:
  - all HTML comments  <!-- ... -->  are stripped (operator notes / version log)
  - the operator appendix (from the "## 부록 (운영자용)" heading to EOF) is dropped

Markdown handled: # / ## / ### headings, > quotes (consecutive lines merged),
--- dividers, - / * bullets, 1. numbered items, ``` code fences, | … | tables
(→ Notion table block with table_row children, has_column_header=true), and
paragraphs. Inline: **bold**, *italic*, `code`. Notion limits are respected
(≤100 blocks/append, table_row children batched, rich-text ≤2000 chars/segment).

CLI:
    python3 scripts/weekly/notion_survey_pages.py            # DRY-RUN (all 7)
    python3 scripts/weekly/notion_survey_pages.py --only JOP # DRY-RUN one
    python3 scripts/weekly/notion_survey_pages.py --apply    # create/update
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import _notion  # noqa: E402

_REPO_ROOT = _HERE.parent.parent
_SURVEY_DIR = _REPO_ROOT / "state" / "archive" / "surveys"

RESEARCHERS = ["BHL", "BYL", "JOP", "JYK", "MSY", "SMJ", "SYJ"]

CONTAINER_TITLE = "연구 프로파일 설문 (2026-06)"
PAGE_TITLE_FMT = "연구 프로파일 설문 — {init}"

# Notion hard limits we must respect.
MAX_BLOCKS_PER_APPEND = 100          # children per page/append request
MAX_TABLE_ROWS_PER_APPEND = 100      # table_row children per append
MAX_RICH_TEXT_LEN = 2000             # chars per rich_text text segment

# The operator appendix begins at this heading; everything from here to EOF is
# operator-only and must not reach the researcher page.
_APPENDIX_RE = re.compile(r"^##\s+부록")
# HTML comments, including multi-line ones.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


# ===========================================================================
# Markdown preprocessing
# ===========================================================================

def preprocess(md: str) -> str:
    """Strip HTML comments and truncate at the operator appendix.

    Returns the researcher-facing slice (intro + sections A–I)."""
    md = _HTML_COMMENT_RE.sub("", md)
    out_lines: list[str] = []
    for line in md.splitlines():
        if _APPENDIX_RE.match(line):
            break
        out_lines.append(line)
    return "\n".join(out_lines)


# ===========================================================================
# Inline rich-text
# ===========================================================================

# One regex, alternation ordered so the longest/most-specific delimiter wins:
# `code` (literal, no nested formatting), then **bold**, then *italic*.
_INLINE_RE = re.compile(
    r"(?P<code>`[^`]+?`)"
    r"|(?P<bold>\*\*.+?\*\*)"
    r"|(?P<italic>\*(?!\s)(?:[^*]|\*\*)+?\*)"
)


def _split_len(s: str, n: int = MAX_RICH_TEXT_LEN) -> list[str]:
    """Split a string into ≤n-char chunks (Notion caps each text segment)."""
    if len(s) <= n:
        return [s]
    return [s[i:i + n] for i in range(0, len(s), n)]


def _rt(content: str, *, bold: bool = False, italic: bool = False,
        code: bool = False) -> list[dict]:
    """Build one-or-more rich_text objects for `content`, splitting on the
    2000-char cap and carrying the annotations. Empty string → []."""
    if content == "":
        return []
    ann: dict[str, Any] = {}
    if bold:
        ann["bold"] = True
    if italic:
        ann["italic"] = True
    if code:
        ann["code"] = True
    objs: list[dict] = []
    for piece in _split_len(content):
        o: dict[str, Any] = {"type": "text", "text": {"content": piece}}
        if ann:
            o["annotations"] = dict(ann)
        objs.append(o)
    return objs


def inline_rich_text(text: str) -> list[dict]:
    """Convert a line of inline markdown into a Notion rich_text array.

    Handles **bold**, *italic*, `code`. Unmatched/escaped stars fall through as
    literal text. Always returns a list (possibly empty for an empty cell)."""
    if text is None:
        return []
    text = text.replace("\r", "")
    out: list[dict] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            out.extend(_rt(text[pos:m.start()]))
        if m.group("code") is not None:
            out.extend(_rt(m.group("code")[1:-1], code=True))
        elif m.group("bold") is not None:
            # Allow *italic* nested inside **bold** by recursing, then OR-in bold.
            inner = m.group("bold")[2:-2]
            out.extend(_mark(inline_rich_text(inner), bold=True))
        elif m.group("italic") is not None:
            inner = m.group("italic")[1:-1]
            out.extend(_mark(inline_rich_text(inner), italic=True))
        pos = m.end()
    if pos < len(text):
        out.extend(_rt(text[pos:]))
    return out


def _mark(segs: list[dict], *, bold: bool = False,
          italic: bool = False) -> list[dict]:
    """Apply an annotation flag onto already-built rich_text segments."""
    for seg in segs:
        ann = seg.setdefault("annotations", {})
        if bold:
            ann["bold"] = True
        if italic:
            ann["italic"] = True
    return segs


# ===========================================================================
# Block builders
# ===========================================================================

def _para(text: str) -> dict:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": inline_rich_text(text)}}


def _heading(level: int, text: str) -> dict:
    key = f"heading_{level}"
    return {"object": "block", "type": key,
            key: {"rich_text": inline_rich_text(text)}}


def _quote(text: str) -> dict:
    return {"object": "block", "type": "quote",
            "quote": {"rich_text": inline_rich_text(text)}}


def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": inline_rich_text(text)}}


def _numbered(text: str) -> dict:
    return {"object": "block", "type": "numbered_list_item",
            "numbered_list_item": {"rich_text": inline_rich_text(text)}}


def _code(text: str, language: str = "plain text") -> dict:
    # Notion code blocks also cap each rich_text segment at 2000 chars.
    rt = []
    for piece in _split_len(text):
        rt.append({"type": "text", "text": {"content": piece}})
    if not rt:
        rt = [{"type": "text", "text": {"content": ""}}]
    return {"object": "block", "type": "code",
            "code": {"rich_text": rt, "language": language}}


def _table_cell(raw: str) -> list[dict]:
    """A table cell is itself a rich_text array."""
    return inline_rich_text(raw.strip())


def _table(rows: list[list[str]]) -> dict:
    """rows[0] is the header (separator row already removed)."""
    width = max((len(r) for r in rows), default=1)
    children = []
    for r in rows:
        cells = [_table_cell(c) for c in r]
        # Pad/truncate ragged rows to a consistent width.
        while len(cells) < width:
            cells.append([])
        cells = cells[:width]
        children.append({"object": "block", "type": "table_row",
                         "table_row": {"cells": cells}})
    return {"object": "block", "type": "table",
            "table": {"table_width": width,
                      "has_column_header": True,
                      "has_row_header": False,
                      "children": children}}


# ===========================================================================
# Markdown → blocks (line-based state machine)
# ===========================================================================

_FENCE_RE = re.compile(r"^```(\S*)\s*$")
_H_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_NUM_RE = re.compile(r"^\s*\d+\.\s+(.*)$")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$")


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _parse_table_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def markdown_to_blocks(md: str, warnings: list[str]) -> list[dict]:
    """Convert preprocessed markdown into a flat list of Notion block dicts.

    `warnings` collects any constructs the converter had to approximate."""
    blocks: list[dict] = []
    lines = md.split("\n")
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # blank line -> separator only (no empty paragraph spam)
        if stripped == "":
            i += 1
            continue

        # fenced code block
        mfence = _FENCE_RE.match(line)
        if mfence:
            lang = mfence.group(1) or "plain text"
            body: list[str] = []
            i += 1
            closed = False
            while i < n:
                if _FENCE_RE.match(lines[i]):
                    closed = True
                    i += 1
                    break
                body.append(lines[i])
                i += 1
            if not closed:
                warnings.append("unterminated code fence (```), "
                                "captured to EOF")
            blocks.append(_code("\n".join(body), _norm_lang(lang)))
            continue

        # divider
        if stripped == "---" or stripped == "***" or stripped == "___":
            blocks.append(_divider())
            i += 1
            continue

        # table: a row line immediately followed by a separator row
        if _is_table_row(line) and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            header = _parse_table_row(line)
            rows = [header]
            i += 2  # skip header + separator
            while i < n and _is_table_row(lines[i]):
                rows.append(_parse_table_row(lines[i]))
                i += 1
            blocks.extend(_emit_table(rows, warnings))
            continue

        # a stray table row with NO separator -> treat as paragraph, warn once
        if _is_table_row(line) and not (
                i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1])):
            # only warn if it really looks like an intended table (header-ish)
            warnings.append(f"pipe row without header separator treated as "
                            f"paragraph: {stripped[:60]!r}")
            blocks.append(_para(stripped))
            i += 1
            continue

        # heading
        mh = _H_RE.match(line)
        if mh:
            level = min(len(mh.group(1)), 3)  # Notion has only h1..h3
            if len(mh.group(1)) > 3:
                warnings.append(f"heading depth {len(mh.group(1))} clamped to "
                                f"h3: {mh.group(2)[:50]!r}")
            blocks.append(_heading(level, mh.group(2).strip()))
            i += 1
            continue

        # blockquote: merge consecutive '>' lines into one quote block
        if _QUOTE_RE.match(line):
            qlines: list[str] = []
            while i < n and _QUOTE_RE.match(lines[i]):
                qlines.append(_QUOTE_RE.match(lines[i]).group(1))
                i += 1
            blocks.append(_quote("\n".join(qlines).strip("\n")))
            continue

        # bulleted list item
        mb = _BULLET_RE.match(line)
        if mb:
            blocks.append(_bullet(mb.group(1).strip()))
            i += 1
            continue

        # numbered list item
        mn = _NUM_RE.match(line)
        if mn:
            blocks.append(_numbered(mn.group(1).strip()))
            i += 1
            continue

        # paragraph: gather following non-special, non-blank lines
        para_lines = [stripped]
        i += 1
        while i < n:
            nxt = lines[i]
            ns = nxt.strip()
            if ns == "":
                break
            if (_H_RE.match(nxt) or _QUOTE_RE.match(nxt) or _BULLET_RE.match(nxt)
                    or _NUM_RE.match(nxt) or _FENCE_RE.match(nxt)
                    or _is_table_row(nxt) or ns in ("---", "***", "___")):
                break
            para_lines.append(ns)
            i += 1
        blocks.append(_para("\n".join(para_lines)))
    return blocks


def _norm_lang(lang: str) -> str:
    """Map a fence language to a Notion-accepted code language."""
    lang = (lang or "").strip().lower()
    aliases = {"": "plain text", "text": "plain text", "txt": "plain text",
               "sh": "shell", "bash": "shell", "py": "python",
               "js": "javascript", "ts": "typescript", "md": "markdown",
               "yml": "yaml"}
    return aliases.get(lang, lang)


def _emit_table(rows: list[list[str]], warnings: list[str]) -> list[dict]:
    """Build a table block; if it has >100 rows, Notion's single-append cap on
    table_row children would be exceeded — split into multiple table blocks of
    ≤100 rows each, repeating the header. (None of the surveys hit this, but it
    keeps the converter honest.)"""
    if len(rows) <= MAX_TABLE_ROWS_PER_APPEND:
        return [_table(rows)]
    warnings.append(f"table with {len(rows)} rows split into "
                    f"{MAX_TABLE_ROWS_PER_APPEND}-row chunks")
    header = rows[0]
    body = rows[1:]
    out = []
    for start in range(0, len(body), MAX_TABLE_ROWS_PER_APPEND - 1):
        chunk = [header] + body[start:start + MAX_TABLE_ROWS_PER_APPEND - 1]
        out.append(_table(chunk))
    return out


# ===========================================================================
# Append helpers (respect the ≤100 children/request limit, incl. tables)
# ===========================================================================

def _append_blocks_chunked(page_id: str, blocks: list[dict]) -> int:
    """Append `blocks` to a page in ≤100-block requests. Table blocks are
    sent on their own (their table_row children also count) to stay well under
    Notion's nested-children limits. Returns total blocks appended."""
    appended = 0
    buf: list[dict] = []

    def flush():
        nonlocal appended, buf
        if not buf:
            return
        _notion.append_block_children(page_id, buf)
        appended += len(buf)
        buf = []

    for blk in blocks:
        if blk.get("type") == "table":
            flush()                       # tables go in their own request
            _notion.append_block_children(page_id, [blk])
            appended += 1
            continue
        buf.append(blk)
        if len(buf) >= MAX_BLOCKS_PER_APPEND:
            flush()
    flush()
    return appended


def _clear_page(page_id: str) -> int:
    """Archive every existing child block of a page (so a re-applied page does
    not accumulate duplicate content). Returns blocks removed."""
    removed = 0
    for blk in _notion.list_block_children(page_id):
        bid = blk.get("id")
        if not bid:
            continue
        try:
            _notion.delete_block(bid)
            removed += 1
        except _notion.NotionError as e:
            print(f"    [warn] could not archive block {bid}: {e}",
                  file=sys.stderr)
    return removed


# ===========================================================================
# Parent lookup
# ===========================================================================

def resolve_parent_page() -> tuple[str, str]:
    """Find the page the survey container should live under: retrieve the digest
    database (NOTION_DIGEST_DB_ID) and read its parent page_id. Returns
    (page_id, page_title). Raises NotionError if the DB is not parented by a
    page (e.g. workspace-level) — surveys need a writable parent page."""
    db_id = _notion.digest_db_id()
    db = _notion.retrieve_database(db_id)
    pid = _notion.database_parent_page_id(db)
    if not pid:
        raise _notion.NotionError(
            "digest DB is not parented by a page (parent="
            f"{db.get('parent')!r}); cannot derive the 'CSNL 논문 추천' page. "
            "Move the digest DB under that page or set the parent explicitly.")
    try:
        parent = _notion.retrieve_page(pid)
        title = _page_title(parent)
    except _notion.NotionError:
        title = "(unknown)"
    return pid, title


def _page_title(page: dict) -> str:
    props = page.get("properties") or {}
    for meta in props.values():
        if meta.get("type") == "title":
            return "".join(t.get("plain_text", "")
                           for t in (meta.get("title") or []))
    return ""


# ===========================================================================
# Per-survey build + apply
# ===========================================================================

def build_survey_blocks(init: str) -> tuple[list[dict], list[str]]:
    path = _SURVEY_DIR / f"{init}.md"
    if not path.exists():
        raise FileNotFoundError(path)
    raw = path.read_text("utf-8")
    body = preprocess(raw)
    warnings: list[str] = []
    blocks = markdown_to_blocks(body, warnings)
    return blocks, warnings


def _count_tables(blocks: list[dict]) -> int:
    return sum(1 for b in blocks if b.get("type") == "table")


def _block_type_summary(blocks: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for b in blocks:
        counts[b["type"]] = counts.get(b["type"], 0) + 1
    return counts


def dry_run(targets: list[str]) -> int:
    print("=" * 72)
    print("DRY-RUN — parsing surveys, creating NOTHING in Notion")
    print("=" * 72)
    total_blocks = total_tables = 0
    any_warn = False
    for init in targets:
        try:
            blocks, warnings = build_survey_blocks(init)
        except FileNotFoundError as e:
            print(f"\n[{init}] MISSING survey file: {e}")
            any_warn = True
            continue
        ntables = _count_tables(blocks)
        total_blocks += len(blocks)
        total_tables += ntables
        first3 = [b["type"] for b in blocks[:3]]
        print(f"\n[{init}]  blocks={len(blocks)}  tables={ntables}")
        print(f"    first 3 block types: {first3}")
        summary = _block_type_summary(blocks)
        print(f"    block-type histogram: {summary}")
        # surface oversized table_row counts (Notion append nuance)
        big_tables = [b["table"]["table_width"] for b in blocks
                      if b.get("type") == "table"
                      and len(b["table"]["children"]) > MAX_TABLE_ROWS_PER_APPEND]
        if big_tables:
            print(f"    NOTE: {len(big_tables)} table(s) exceed "
                  f"{MAX_TABLE_ROWS_PER_APPEND} rows (auto-split on apply)")
        if warnings:
            any_warn = True
            print(f"    conversion warnings ({len(warnings)}):")
            for w in warnings:
                print(f"      - {w}")
        else:
            print("    conversion warnings: none")
    print("\n" + "-" * 72)
    print(f"TOTAL: {len(targets)} surveys, {total_blocks} blocks, "
          f"{total_tables} tables")
    # parent lookup (read-only)
    print("-" * 72)
    print("Parent-page lookup (read-only):")
    try:
        pid, title = resolve_parent_page()
        print(f"    digest DB id : {_notion.digest_db_id()}")
        print(f"    parent page  : {pid}")
        print(f"    parent title : {title!r}")
        print(f"    -> would create container page {CONTAINER_TITLE!r} here, "
              f"then 1 child page per researcher.")
    except _notion.NotionError as e:
        any_warn = True
        print(f"    [ERROR] parent lookup failed: {e}")
    print("-" * 72)
    print("DRY-RUN complete. No pages created. Re-run with --apply to publish.")
    return 1 if any_warn else 0


def apply(targets: list[str]) -> int:
    print("APPLY — creating/updating survey pages in Notion")
    pid, title = resolve_parent_page()
    print(f"    parent page: {pid}  {title!r}")

    # container page (idempotent)
    container = _notion.find_child_page_by_title(pid, CONTAINER_TITLE)
    if container:
        container_id = container["id"]
        print(f"    container exists: {container_id}  {CONTAINER_TITLE!r}")
    else:
        created = _notion.create_child_page(pid, CONTAINER_TITLE)
        container_id = created["id"]
        print(f"    container created: {container_id}  {CONTAINER_TITLE!r}")

    rc = 0
    for init in targets:
        page_title = PAGE_TITLE_FMT.format(init=init)
        try:
            blocks, warnings = build_survey_blocks(init)
        except FileNotFoundError as e:
            print(f"  [{init}] SKIP — missing file: {e}")
            rc = 1
            continue
        existing = _notion.find_child_page_by_title(container_id, page_title)
        if existing:
            page_id = existing["id"]
            removed = _clear_page(page_id)
            print(f"  [{init}] replacing {page_id} (cleared {removed} blocks)")
        else:
            created = _notion.create_child_page(container_id, page_title)
            page_id = created["id"]
            print(f"  [{init}] created {page_id}")
        appended = _append_blocks_chunked(page_id, blocks)
        msg = f"  [{init}] appended {appended} blocks"
        if warnings:
            msg += f"  ({len(warnings)} warning(s))"
        print(msg)
    print("APPLY complete.")
    return rc


# ===========================================================================
# CLI
# ===========================================================================

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Publish researcher surveys as individual Notion pages "
                    "(default: DRY-RUN, creates nothing).")
    ap.add_argument("--apply", action="store_true",
                    help="Actually create/update Notion pages "
                         "(default is dry-run).")
    ap.add_argument("--only", metavar="INIT", default=None,
                    help="Limit to one researcher (e.g. JOP).")
    args = ap.parse_args(argv)

    if args.only:
        only = args.only.strip().upper()
        if only not in RESEARCHERS:
            print(f"[error] --only {args.only!r}: unknown researcher. "
                  f"Choose from {RESEARCHERS}.", file=sys.stderr)
            return 2
        targets = [only]
    else:
        targets = list(RESEARCHERS)

    if args.apply:
        return apply(targets)
    return dry_run(targets)


if __name__ == "__main__":
    raise SystemExit(main())
