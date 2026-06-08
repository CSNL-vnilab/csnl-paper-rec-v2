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

INTERACTIVITY (the point of this converter): markdown form elements become
NATIVE interactive Notion blocks instead of a wall of literal "[ ]" / "☐" /
"______" text —
  1. checkbox / multiple-choice option lines (any line with ☐/☑/☒, e.g.
     "B4. 동시측정: ☐행동 ☐fMRI…", "1. 이론 프레임: ☐efficient coding…",
     "- 사용/관심: ☐한다 ☐안한다…") → a **bold label paragraph** + one
     **to_do** per option (checked iff its marker is ☑/☒/✓/[x]);
  2. pre-filled "confirm" fields (the [자동]/【확인필요】 scalar fields shown as
     "… → value [ ]" bullets, B1–B10, plus the whole A. 기본정보 table) → one
     **to_do** per field (unchecked = needs-confirm, label carries the value);
  3. free-text blanks (______ with no option, e.g. "H1. in-scope: ______",
     "G1. 키워드 목록: ______", B9/B10 배경/seed, "자주 추천되지만 … PI: ______")
     → a **bold label paragraph** + a tinted **callout** answer box (pre-filled
     value if any, else a faint "여기에 작성" placeholder);
  4. genuine multi-column tables (B-요약 / §H 제외 / §G2 / §D PI / §E model) →
     kept as native Notion **tables** (editable cells); a redundant [✓/✗]
     confirm column is dropped (its job moves to the to_do checkboxes).

Other markdown handled unchanged: # / ## / ### headings, > quotes (consecutive
lines merged), --- dividers, plain - / * bullets, ``` code fences, and
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
# `code` (literal, no nested formatting), then __bold__/**bold**, then the
# matching *italic*/_italic_.
#
# re.DOTALL is REQUIRED: quote/paragraph blocks merge their source lines with
# '\n', so an emphasis span can straddle a newline — e.g. the survey's
# '**연결 기준점\n(connection anchor)**' or '*phenomenon /\nresearch-focus 가 …*'.
# Without DOTALL the bold '.+?' (and a star-italic) cannot cross the newline, the
# span fails to match, and ONE delimiter is orphaned as a literal '*' that leaks
# all the way to Notion. DOTALL lets the whole span match so both markers are
# consumed.
#
# Underscore emphasis (__bold__ / _italic_) is matched too — the surveys use it
# for whole-cell italics like '_(있다면) … single-unit)_' and
# '_신경전형 성인 / … / 인간만_'. It is GUARDED by word-boundary lookarounds so an
# identifier underscore is never mistaken for emphasis: the opening '_' must not
# follow a word char/underscore and must be followed by a non-space non-
# underscore; the closing '_' must not be followed by a word char/underscore.
# '\w' is Unicode-aware (matches Greek too), so this protects 'BDM_Magnitude',
# 'prev_est', 'σ_abs_leak', 'D_ij', 'n_ori', and the '______' write-here blanks
# (a run of underscores never satisfies the body), while still converting genuine
# '_…_' emphasis.
_INLINE_RE = re.compile(
    r"(?P<code>`[^`]+?`)"
    r"|(?P<bold>\*\*.+?\*\*)"
    r"|(?P<ubold>(?<!\w)__(?=\S)(?!_).+?__(?!\w))"
    r"|(?P<italic>\*(?!\s)(?:[^*]|\*\*)+?\*)"
    r"|(?P<uitalic>(?<!\w)_(?=\S)(?!_)(?:[^_]|__)+?_(?!\w))",
    re.DOTALL,
)

# Residual UNMATCHED emphasis markers that must never reach Notion as literal
# text. Applied to PLAIN runs only (the text outside every matched span) so a
# dangling '*'/'`' opener — or an orphaned half of a broken span — is removed
# rather than rendered raw. This survey has NO legitimate literal '*' or '`', so
# both are stripped unconditionally from plain runs.
_STRAY_STAR_RE = re.compile(r"\*")
_STRAY_BACKTICK_RE = re.compile(r"`")
# A LONE markdown underscore: one NOT forming part of an identifier (a word char
# on both sides) and NOT part of an underscore run (──> '______' blanks; those
# are handled by the scaffolding strip). '\w' is Unicode-aware so 'σ_abs',
# 'prev_est', 'D_ij' are preserved. Matches a single '_' that has a non-word char
# OR a string edge on at least one side and no word char on the other — i.e. a
# dangling emphasis marker, including one at the start/end of the run ('_leading'
# / 'trailing_'). Applied in a fixed-point loop so a partially-stripped '__'
# residue is fully removed.
_LONE_UNDERSCORE_RE = re.compile(
    r"(?<!\w)_(?!\w)"                 # _ flanked by non-word/edge on both sides
    r"|(?:^|(?<=[^\w]))_(?=\w)"       # (edge|non-word) _ word  (leading emphasis)
    r"|(?<=\w)_(?:(?=[^\w])|$)"       # word _ (non-word|edge)   (trailing emph.)
)


def _strip_residual_markers(s: str) -> str:
    """Remove residual UNMATCHED emphasis markers ('*', stray '`', lone '_')
    from a PLAIN text run (one already outside any matched bold/italic/code
    span). Belt-and-suspenders so no literal emphasis marker ever reaches
    Notion. Identifier underscores ('prev_est', 'σ_abs') and write-here blank
    runs ('______') are preserved. Idempotent (loops underscore removal until
    stable so a leftover '__' half can't survive)."""
    if not s:
        return s
    s = _STRAY_STAR_RE.sub("", s)
    s = _STRAY_BACKTICK_RE.sub("", s)
    # Loop: stripping one lone '_' can expose another (e.g. an orphaned '__').
    # A '______' blank is NOT a lone underscore (interior '_' have '_' neighbours)
    # so it is left for the scaffolding strip; the loop terminates quickly.
    for _ in range(8):
        new = _LONE_UNDERSCORE_RE.sub("", s)
        if new == s:
            break
        s = new
    return s


# ===========================================================================
# Flag-token colour-coding + URL/DOI link detection (rich_text decoration)
# ===========================================================================
# Two decorations applied to EVERY emitted rich_text segment (paragraph, to_do,
# callout, table cell, heading, quote, bullet) as a final post-pass, AFTER the
# scaffolding strip so the counts reflect what actually ships:
#
#   (1) Flag colour-coding — the three reviewer-facing status tokens get their
#       own rich_text segment carrying a Notion *_background colour, while the
#       surrounding text keeps default colour. This COMPOSES with bold/italic:
#       we split each already-built segment (preserving its bold/italic/code
#       annotations) and merge the colour into the matched token's sub-segment,
#       so a token that landed inside a **bold** span is coloured AND stays bold
#       rather than being double-processed.
#         [자동]      → green_background   (auto-filled from memory)
#         【확인필요】 → yellow_background  (synthesised — needs confirmation)
#         (직접 작성) → red_background     (blank — researcher writes it)
#
#   (2) Link detection — bare http(s)/www URLs, doi.org URLs, and bare DOI
#       strings (10.NNNN/…) become clickable via a Notion text.link. A bare DOI
#       resolves through https://doi.org/. Identifier/formula text is NEVER
#       touched: the URL/DOI patterns cannot match Unicode math like 'σ_abs',
#       'θ̂−θ', 'α=±3.3', 'var(θ̂)', and the residual-'_'-strip still guards
#       identifiers separately, so those pass through verbatim.

# token  -> Notion background-colour annotation value
_FLAG_COLORS: dict[str, str] = {
    "[자동]": "green_background",
    "【확인필요】": "yellow_background",
    "(직접 작성)": "red_background",
}
# The three tokens, longest first so alternation is unambiguous, each escaped
# (they contain regex metacharacters: '[', ']', '(', ')').
_FLAG_RE = re.compile(
    "|".join(re.escape(tok) for tok in
             sorted(_FLAG_COLORS, key=len, reverse=True)))


# A bare URL: http(s):// or a bare 'www.'/'doi.org/' host, run up to whitespace
# or a closing wrapper. Trailing sentence punctuation is trimmed back (below).
# 'doi.org/…' is included so a scheme-less DOI URL is captured as ONE link
# rather than leaving a stranded 'doi.org/' before the bare-DOI match.
_URL_RE = re.compile(r"(?:https?://|www\.|doi\.org/)[^\s<>()\[\]{}「」『』《》]+",
                     re.IGNORECASE)
# A bare DOI: '10.' + registrant + '/' + suffix. The suffix runs until
# whitespace or a closing wrapper / Korean particle boundary. DOIs never contain
# spaces; this stops at the first whitespace so trailing prose is not swallowed.
# Anchored on a non-digit/start so it does not fire mid-number.
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s<>()\[\]{}「」『』《》,;]+",
                     re.IGNORECASE)
# Punctuation that should not be part of a captured URL/DOI when it sits at the
# very end (sentence/clause tail). Stripped off and kept as plain text.
_URL_TAIL = ".,;:·。、)]}>』」》"
# A markdown inline link: [label](url). label may hold anything but ']'; url
# anything but ')'. Detected on plain runs only.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+|"
                         r"(?:www\.|doi\.org/)[^)\s]+|10\.\d{4,9}/[^)\s]+)\)")


def _link_url(raw: str) -> str:
    """Normalise a detected link token into an absolute href.
    'www.x' -> https; a bare DOI -> the doi.org resolver; otherwise as-is."""
    low = raw.lower()
    if low.startswith(("http://", "https://")):
        return raw
    if low.startswith("www."):
        return "https://" + raw
    if low.startswith("doi.org/"):
        return "https://" + raw
    if low.startswith("10."):
        return "https://doi.org/" + raw
    return raw


def _split_flags(seg: dict) -> list[dict]:
    """Split one rich_text segment so each flag token becomes its own segment
    carrying the matching *_background colour, while inheriting the host
    segment's bold/italic/code annotations. Non-text segments and segments with
    no flag token pass through unchanged. The host's existing colour (if any) is
    preserved on the surrounding runs."""
    if seg.get("type") != "text":
        return [seg]
    content = seg.get("text", {}).get("content", "")
    if not content or not _FLAG_RE.search(content):
        return [seg]
    base_ann = seg.get("annotations") or {}
    base_link = seg.get("text", {}).get("link")
    out: list[dict] = []
    pos = 0

    def _emit(piece: str, color: Optional[str]) -> None:
        if piece == "":
            return
        ann = dict(base_ann)
        if color:
            ann["color"] = color
        txt: dict[str, Any] = {"content": piece}
        if base_link:
            txt["link"] = base_link
        o: dict[str, Any] = {"type": "text", "text": txt}
        if ann:
            o["annotations"] = ann
        out.append(o)

    for m in _FLAG_RE.finditer(content):
        if m.start() > pos:
            _emit(content[pos:m.start()], None)
        _emit(m.group(0), _FLAG_COLORS[m.group(0)])
        pos = m.end()
    if pos < len(content):
        _emit(content[pos:], None)
    return out


def _split_links(seg: dict) -> list[dict]:
    """Split one rich_text segment so each bare URL / DOI / markdown link gets a
    Notion text.link (clickable), inheriting the host segment's annotations.
    A markdown '[label](url)' becomes a single linked 'label' segment. Segments
    that already carry a link, non-text segments, or runs with no URL/DOI pass
    through unchanged. Identifier/formula text is never matched."""
    if seg.get("type") != "text":
        return [seg]
    txt = seg.get("text", {})
    if txt.get("link"):                       # already linked (e.g. md link)
        return [seg]
    content = txt.get("content", "")
    if not content:
        return [seg]
    base_ann = seg.get("annotations") or {}

    # Build a list of (start, end, label, href) matches across the three forms,
    # markdown links first (they consume the bracketed label), then bare URL,
    # then bare DOI; overlapping later matches are skipped.
    spans: list[tuple[int, int, str, str]] = []
    for m in _MD_LINK_RE.finditer(content):
        spans.append((m.start(), m.end(), m.group(1), _link_url(m.group(2))))
    for rx in (_URL_RE, _DOI_RE):
        for m in rx.finditer(content):
            s, e = m.start(), m.end()
            tok = m.group(0)
            # trim trailing sentence punctuation back out of the link
            while tok and tok[-1] in _URL_TAIL:
                tok = tok[:-1]
                e -= 1
            if not tok:
                continue
            spans.append((s, e, tok, _link_url(tok)))
    if not spans:
        return [seg]
    spans.sort(key=lambda t: (t[0], -(t[1] - t[0])))

    out: list[dict] = []
    pos = 0

    def _plain(piece: str) -> None:
        if piece == "":
            return
        o: dict[str, Any] = {"type": "text", "text": {"content": piece}}
        if base_ann:
            o["annotations"] = dict(base_ann)
        out.append(o)

    def _linked(label: str, href: str) -> None:
        if label == "":
            return
        o: dict[str, Any] = {"type": "text",
                             "text": {"content": label, "link": {"url": href}}}
        if base_ann:
            o["annotations"] = dict(base_ann)
        out.append(o)

    for s, e, label, href in spans:
        if s < pos:               # overlaps an already-consumed span — skip
            continue
        if s > pos:
            _plain(content[pos:s])
        _linked(label, href)
        pos = e
    if pos < len(content):
        _plain(content[pos:])
    return out


def _decorate_rich_text(rt: list[dict]) -> list[dict]:
    """Apply link detection then flag colour-coding to a rich_text array.
    Order: links first (so a flag token never lands inside a URL — they don't
    overlap, but this keeps the split boundaries clean), then flags. Each pass
    expands segments; both preserve host annotations/links. Returns a new list."""
    linked: list[dict] = []
    for seg in rt:
        linked.extend(_split_links(seg))
    flagged: list[dict] = []
    for seg in linked:
        flagged.extend(_split_flags(seg))
    return flagged


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


# Intra-block soft-wrap join (BUG B). A single logical block (paragraph, bullet,
# to_do label, callout, quote) merges its source lines with '\n' before reaching
# here; those newlines are markdown line-WRAPPING, not real breaks, so each must
# collapse to a single SPACE so the sentence flows (Korean wraps at 어절 bounda-
# ries → space-join is correct, e.g. '메모리(Postgres)에\n반영하기' →
# '메모리(Postgres)에 반영하기'). After the join, runs of 2+ horizontal whitespace
# are collapsed to one space (the join can abut an existing trailing/leading
# space). This only ever sees within-block text: code fences use _code() (never
# this path) and table cells are single-line, so no real block boundary is merged.
_SOFTWRAP_WS_RE = re.compile(r"[ 	]{2,}")


def _join_softwrap(text: str) -> str:
    """Convert intra-block soft-wrap newlines to single spaces, then collapse any
    resulting double horizontal-whitespace. Used at the start of inline_rich_text
    so every text-bearing block (NOT code; table cells are single-line) reads as
    flowing prose instead of literal mid-sentence line breaks."""
    if "\n" not in text:
        return text
    text = text.replace("\n", " ")
    return _SOFTWRAP_WS_RE.sub(" ", text)


def inline_rich_text(text: str) -> list[dict]:
    """Convert a line of inline markdown into a Notion rich_text array.

    Handles **bold**/__bold__, *italic*/_italic_, `code`, including spans that
    straddle a merged newline. Every PLAIN run (text outside a matched span) is
    passed through _strip_residual_markers so a dangling/unmatched '*', stray
    '`', or lone markdown '_' is removed rather than rendered literally. Always
    returns a list (possibly empty for an empty cell)."""
    if text is None:
        return []
    text = text.replace("\r", "")
    # BUG B: collapse intra-block soft-wrap newlines to a single space FIRST, so
    # the existing DOTALL emphasis spans (which already cross newlines) now match
    # across a space and no '\n' survives as a literal Notion line break. Recursive
    # calls on inner spans re-run this harmlessly (idempotent: inner has no '\n').
    text = _join_softwrap(text)
    out: list[dict] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            out.extend(_rt(_strip_residual_markers(text[pos:m.start()])))
        if m.group("code") is not None:
            out.extend(_rt(m.group("code")[1:-1], code=True))
        elif m.group("bold") is not None:
            # Allow nested italic inside **bold** by recursing, then OR-in bold.
            inner = m.group("bold")[2:-2]
            out.extend(_mark(inline_rich_text(inner), bold=True))
        elif m.group("ubold") is not None:
            inner = m.group("ubold")[2:-2]
            out.extend(_mark(inline_rich_text(inner), bold=True))
        elif m.group("italic") is not None:
            inner = m.group("italic")[1:-1]
            out.extend(_mark(inline_rich_text(inner), italic=True))
        elif m.group("uitalic") is not None:
            inner = m.group("uitalic")[1:-1]
            out.extend(_mark(inline_rich_text(inner), italic=True))
        pos = m.end()
    if pos < len(text):
        out.extend(_rt(_strip_residual_markers(text[pos:])))
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


def _todo(text: str, checked: bool = False) -> dict:
    return _notion.block_to_do(inline_rich_text(text), checked)


_PLACEHOLDER = "여기에 작성"


def _callout(text: str) -> dict:
    """Free-text answer box. Empty/blank value → a faint placeholder so it still
    reads as a 'type here' area (Notion has no real placeholder for callouts)."""
    text = (text or "").strip()
    if not text:
        return _notion.block_callout(_rt(_PLACEHOLDER, italic=True))
    return _notion.block_callout(inline_rich_text(text))


# ===========================================================================
# Form-element parsing (checkbox options / confirm fields / blanks)
# ===========================================================================

# Checkbox markers. CHECKED → the option was pre-selected; UNCHECKED → empty box.
_CHECKED_MARKS = "☑☒"          # filled box variants used in the surveys
_UNCHECKED_MARKS = "☐"         # empty box
_ALL_MARKS = _CHECKED_MARKS + _UNCHECKED_MARKS
_HAS_MARK_RE = re.compile(f"[{_ALL_MARKS}]")
# An option = a marker then its text, up to (but not including) the next marker.
_OPTION_RE = re.compile(f"([{_ALL_MARKS}])\\s*([^{_ALL_MARKS}]*)")

# Trailing scaffolding to strip from a confirm-field label/value:
#   "[ ]" / "[x]" tick box · "→ ______" correction arrow · bare "______" ·
#   the "(직접 작성)" write-here hint when it is the ONLY value.
_TICKBOX_RE = re.compile(r"\[\s*[xX✓]?\s*\]")          # [ ] or [x] or [✓]
_ARROW_BLANK_RE = re.compile(r"(?:→|->)\s*_{3,}")        # → ______
_BLANK_RE = re.compile(r"_{3,}")                          # ______
# Annotation brackets the surveys append for operator/PI context, e.g.
# "〔behavior-only [자동]〕". Kept verbatim in the label (they're informative),
# but the trailing [ ] tickbox after them is dropped.
_WRITE_HINT = "(직접 작성)"


def _line_has_checkbox(line: str) -> bool:
    return bool(_HAS_MARK_RE.search(line))


def _is_checked(mark: str) -> bool:
    return mark in _CHECKED_MARKS


def _strip_tickbox_tail(s: str) -> str:
    """Drop a trailing '[ ]'/'[x]' confirm box and a trailing '→ ______' or
    bare '______' correction blank — the to_do checkbox now carries that job.
    Leaves [자동] / 【확인필요】 / 〔…〕 annotations intact."""
    s = s.rstrip()
    # Remove any number of trailing blank/arrow/tickbox tokens (they can stack,
    # e.g. "value [자동] → ______ [ ]").
    prev = None
    while prev != s:
        prev = s
        s = _TICKBOX_RE.sub("", s).rstrip()
        s = _ARROW_BLANK_RE.sub("", s).rstrip()
        # only strip a *trailing* bare blank, not one mid-string
        m = _BLANK_RE.search(s)
        if m and s[m.end():].strip() == "":
            s = s[:m.start()].rstrip()
    return s.rstrip(" ;·").rstrip()


def _split_label_value(text: str) -> tuple[str, str]:
    """Split a 'label: value' field on the FIRST top-level colon (one not inside
    markdown bold). Handles a markdown-bold label like '**B1. Domain** *필수*: x'
    and a full-width '：'. Returns (label, value); no separator → (text, '')."""
    idx = _first_colon(text)
    if idx < 0:
        return text.strip(), ""
    return text[:idx].strip(), text[idx + 1:].strip()


def _split_freetext_label(text: str) -> tuple[str, str]:
    """Robust split for a FREE-TEXT field into (label, value).

    The label may itself contain a parenthetical colon — e.g.
    '보유/사용 장비·환경 (예: 7T fMRI@OO …): ______' or
    '… (예 — RDK · tDCS): ______'. A naive first-colon split would break the
    label at the parenthetical and lose the rest. So split on the LAST top-level
    colon that precedes the answer scaffold (the '______'/value tail): everything
    up to that colon is the label; everything after is the (usually blank) value.

    If there is no answer scaffold, fall back to the last top-level colon overall
    (the field's own 'label: value' colon, parentheticals notwithstanding).
    No top-level colon at all → (text, '')."""
    # Locate the answer scaffold so we only consider colons that introduce it.
    mblank = _BLANK_RE.search(text)
    scope_end = mblank.start() if mblank else len(text)
    idx = _last_colon(text, 0, scope_end)
    if idx < 0:
        # No colon before the scaffold; consider the whole line (e.g. the colon
        # sits *after* a leading blank, which shouldn't happen, but be safe).
        idx = _last_colon(text, 0, len(text))
    if idx < 0:
        return text.strip(), ""
    return text[:idx].strip(), text[idx + 1:].strip()


def _first_colon(text: str) -> int:
    """Index of the first ':' (or '：') that is NOT inside markdown bold/inline
    code, so '**B3. Task** : paradigm = …' splits on the right colon and a colon
    inside `**…**` (rare) is skipped. Returns -1 if none."""
    bold = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if text.startswith("**", i):
            bold = not bold
            i += 2
            continue
        if ch in (":", "：") and not bold:
            return i
        i += 1
    return -1


def _last_colon(text: str, lo: int = 0, hi: Optional[int] = None) -> int:
    """Index of the LAST ':' (or '：') in text[lo:hi] that is NOT inside markdown
    bold/inline code. Mirrors _first_colon but returns the rightmost match, used
    by the free-text splitter so a parenthetical colon in the label does not win
    over the field's own 'label: value' colon. Returns -1 if none."""
    if hi is None:
        hi = len(text)
    bold = False
    i = 0
    n = len(text)
    found = -1
    while i < n:
        if text.startswith("**", i):
            bold = not bold
            i += 2
            continue
        ch = text[i]
        if ch in (":", "：") and not bold and lo <= i < hi:
            found = i
        i += 1
    return found


def _options_from(text: str) -> list[tuple[bool, str]]:
    """Extract (checked, option_text) tuples from the part of a line that holds
    ☐/☑ markers. Option text is trimmed of the trailing tickbox/blank scaffold
    and of empty '기타_' placeholders' underscores."""
    out: list[tuple[bool, str]] = []
    for m in _OPTION_RE.finditer(text):
        mark = m.group(1)
        body = m.group(2).strip()
        body = _strip_tickbox_tail(body)
        # "기타_" / "기타 _" trailing underscore is a fill-in marker, keep label
        body = re.sub(r"_+$", "", body).rstrip()
        # an interior "______" fill slot (e.g. "기타: ______ 【확인필요…】") — the
        # to_do itself is the fill affordance, so drop the literal blank but keep
        # surrounding text/annotations.
        body = _BLANK_RE.sub("", body)
        body = re.sub(r"\s{2,}", " ", body).strip()
        body = body.strip(" ;·")
        if body == "":
            # a marker with no following text (e.g. dangling "☐" before EOL):
            # skip — nothing actionable to show as its own checkbox.
            continue
        out.append((_is_checked(mark), body))
    return out


def _emit_checkbox_group(label: str, options_src: str,
                         blocks: list[dict]) -> None:
    """Emit a bold label paragraph (if any label) + one to_do per option."""
    label = _strip_tickbox_tail(label).strip()
    if label:
        # bold the whole label so it reads as a field header
        blocks.append({"object": "block", "type": "paragraph",
                       "paragraph": {"rich_text":
                                     _mark(inline_rich_text(label), bold=True)}})
    for checked, opt in _options_from(options_src):
        blocks.append(_todo(opt, checked))


# Free-text label lines that carry NO options and whose value is a blank/hint:
# emit a bold label paragraph + a callout answer box.
def _emit_freetext(label: str, value: str, blocks: list[dict]) -> None:
    label = _strip_tickbox_tail(label).strip()
    if label:
        blocks.append({"object": "block", "type": "paragraph",
                       "paragraph": {"rich_text":
                                     _mark(inline_rich_text(label), bold=True)}})
    blocks.append(_callout(value))


def _value_is_blank(value: str) -> bool:
    """True if a field value is effectively empty — only a blank/arrow/tickbox
    or the '(직접 작성)' hint, with no real content."""
    v = _strip_tickbox_tail(value).strip()
    if v in ("", _WRITE_HINT):
        return True
    # e.g. just '(직접 작성)' possibly wrapped, or only punctuation left
    if v.replace(_WRITE_HINT, "").strip(" ()·;—-") == "":
        return True
    return False


# ---- field routing -------------------------------------------------------
# A confirm field is a bullet whose value carries a confirm tickbox "[ ]" /
# correction arrow "→ ______" (B1–B10). A free-text field is a label whose
# value is just a blank/hint (H1, G1, B9/B10 when empty, "자주 추천… PI"). A
# checkbox group is any logical line bearing ☐/☑ markers.

# Labels that should always render as a free-text callout (label paragraph +
# answer box), whether or not the value is pre-filled (matched on the label
# prefix). H1/G1 = single free-text fields; 자주 추천 = the negative-PI bullet;
# 보유/사용 장비 = §C 실험 인프라 (one-line free text).
_FREETEXT_LABEL_HINTS = ("H1.", "G1.", "자주 추천", "보유/사용 장비")

# A bullet whose value ends in a confirm tickbox (possibly after [자동]/annot).
_HAS_TICKBOX_RE = re.compile(r"\[\s*[xX✓]?\s*\]\s*$")
# …or ends in a correction arrow blank.
_HAS_ARROW_BLANK_RE = re.compile(r"(?:→|->)\s*_{3,}\s*\[\s*\]?\s*$")


def _label_text(label: str) -> str:
    """Strip markdown bold and the '*필수*'/'(무엇을 …)' qualifiers from a label
    so prefix-matching ('H1.', 'B3.') is robust."""
    s = label.replace("**", "")
    return s.strip()


def _route_field(text: str, blocks: list[dict], *, default: str,
                 section: str = "") -> None:
    """Classify one logical line and append the matching block(s):
      - bearing ☐/☑          → checkbox group (bold label + to_do per option)
      - confirm field         → single unchecked to_do "label: value"
      - free-text blank label → bold label + callout answer box
      - §C 실험 인프라 body     → callout (label-less one-line free text)
      - else                  → the `default` block (bullet/numbered/paragraph).
    `section` is the current ## heading text (used to spot §C bodies)."""
    raw = text.rstrip()
    if raw.strip() == "":
        return

    # 0) §C 실험 인프라 — a one-line free-text body that sits directly under the
    #    heading. Some files prefix it with a '보유/사용 장비·환경(예: …):' prompt
    #    (→ keep as the label), others start straight into the value. Strip the
    #    trailing "[자동] ______ [ ]" scaffold and show as a callout answer box.
    if default == "paragraph" and "실험 인프라" in section \
            and not _line_has_checkbox(raw):
        c_label, c_value = "", _strip_tickbox_tail(raw)
        # The §C body is usually 'prompt (예: …): ______'. Some files start the
        # prompt with '보유/사용 장비', others with '보유하거나 사용하시는 …' — match
        # the common stem and split on the LAST colon before the blank so a
        # parenthetical colon in the example list never breaks the label.
        if raw.lstrip().startswith("보유"):
            lab, val = _split_freetext_label(raw)
            c_label, c_value = lab, _strip_tickbox_tail(val)
        _emit_freetext(c_label, c_value, blocks)
        return

    # 1) checkbox / multiple-choice group -------------------------------------
    if _line_has_checkbox(raw):
        # Split into label (before first marker) and the options region.
        first = _HAS_MARK_RE.search(raw)
        label = raw[:first.start()]
        opts = raw[first.start():]
        # Drop a leading list-bullet/number that survived in `label` (only when
        # routing a paragraph default; bullet/numbered branches already strip).
        label = _LEADING_LIST_RE.sub("", label)
        # A label that is only a connector ('-', ':') → no label paragraph.
        if _label_text(label).strip(" -:·") == "":
            label = ""
        _emit_checkbox_group(label, opts, blocks)
        return

    label, value = _split_label_value(raw)
    ltext = _label_text(label)

    # 2) free-text blank label (H1 / G1 / 자주 추천 / any label w/ blank value) --
    is_freetext_hint = any(ltext.startswith(h) for h in _FREETEXT_LABEL_HINTS)
    if value and _value_is_blank(value) and (is_freetext_hint or _is_field_label(label)):
        # Re-split robustly: the label may carry a parenthetical colon, so the
        # callout's bold label must keep everything up to the LAST colon before
        # the '______' scaffold (FIX: parenthetical colons must not truncate it).
        ft_label, _ = _split_freetext_label(raw)
        _emit_freetext(ft_label, "", blocks)
        return
    if is_freetext_hint:
        # H1/G1 WITH a pre-filled value → label + callout(value). Robust split so
        # a parenthetical colon in the prompt does not break the label/value.
        ft_label, ft_value = _split_freetext_label(raw)
        _emit_freetext(ft_label, _strip_tickbox_tail(ft_value), blocks)
        return

    # 3) confirm field (B1–B10 etc.): a labelled bullet whose value carries a
    #    confirm tickbox or correction-arrow blank → single unchecked to_do.
    looks_confirm = (
        default == "bullet"
        and _is_field_label(label)
        and (_HAS_TICKBOX_RE.search(raw) or _HAS_ARROW_BLANK_RE.search(raw)
             or "[ ]" in raw)
    )
    if looks_confirm:
        clean_value = _strip_tickbox_tail(value)
        body = f"{label}: {clean_value}" if clean_value else label
        blocks.append(_todo(body, False))
        return

    # 4) default block ---------------------------------------------------------
    if default == "bullet":
        blocks.append(_bullet(raw.strip()))
    elif default == "numbered":
        blocks.append(_numbered(raw.strip()))
    else:
        blocks.append(_para(raw))


_LEADING_LIST_RE = re.compile(r"^\s*(?:[-*]\s+|\d+\.\s+)")
# A "field label" is a short bold-prefixed header like '**B3. Task + 자극**' or
# '**H1. in-scope 한 단락**'. Heuristic: starts with bold, OR matches a Bn/Hn/Gn
# code, and is reasonably short.
_FIELD_CODE_RE = re.compile(r"^\s*(?:\*\*)?\s*(?:B\d+′?|H\d+|G\d+|A\d+)\b")


def _is_field_label(label: str) -> bool:
    if _FIELD_CODE_RE.search(label):
        return True
    lt = _label_text(label)
    return label.strip().startswith("**") and len(lt) <= 60


def _gather_logical(lines: list[str], i: int, n: int,
                    first_text: str) -> tuple[str, int]:
    """From a list-item start (text already stripped of its '- '/'1.' marker),
    pull in continuation lines: indented lines, and bare-blank/option/quote-
    example continuations that belong to the same item. Stops at a blank line,
    a new list item, heading, table, fence, or divider.

    Returns (joined_logical_text, next_index)."""
    parts = [first_text.rstrip()]
    i += 1
    while i < n:
        nxt = lines[i]
        ns = nxt.strip()
        if ns == "":
            break
        # a new top-level list item / structural element ends this one
        if (_H_RE.match(nxt) or _FENCE_RE.match(nxt) or _is_table_row(nxt)
                or ns in ("---", "***", "___")):
            break
        # a NEW list item (not a continuation) ends this one. Continuation
        # lines in the surveys are indented (2+ spaces) — a flush-left '- ' or
        # '1.' starts a new item.
        if (_BULLET_RE.match(nxt) or _NUM_RE.match(nxt)) and not nxt.startswith(
                (" ", "\t")):
            break
        # '>' example sub-lines under B8′ (MSY) are part of the item's prose;
        # keep them as plain text (strip the quote marker so they don't become
        # a separate quote block mid-field). These continuation quotes are
        # *indented* (e.g. '  > ▸ "…"'), so the line-anchored _QUOTE_RE misses
        # them and they would otherwise land in the plain branch with a literal
        # '>'/'▸' intact — strip the leading quote/sub-bullet marker either way.
        parts.append(_strip_quote_marker(ns))
        i += 1
    return "\n".join(parts), i


# Leading quote/sub-bullet scaffold on a gathered continuation line:
#   '> '  blockquote marker (possibly repeated, possibly after indent) ·
#   '▸ '  example sub-bullet glyph used inside B8′ host prose.
# Stripped so no literal '>'/'▸' survives into the emitted field text.
_LEADING_QUOTE_RE = re.compile(r"^(?:[>▸]\s*)+")


def _strip_quote_marker(s: str) -> str:
    """Drop leading '>'/'▸' quote / example-bullet markers from a gathered
    continuation line (B8′ multi-line example sub-quotes). Idempotent."""
    return _LEADING_QUOTE_RE.sub("", s).lstrip()


# ---- A. 기본정보 table → to_do list --------------------------------------

def _is_basic_info_table(header: list[str]) -> bool:
    """The A. 기본정보 table has header 항목 | 사전기입 | [✓/✗] | (수정)."""
    h = [c.strip() for c in header]
    return len(h) >= 2 and h[0] == "항목" and "사전기입" in h[1]


def _basic_info_to_todos(rows: list[list[str]]) -> list[dict]:
    """Each A-table data row → one unchecked to_do '**항목**: 사전기입' (the
    researcher checks to confirm, edits the text to correct)."""
    out: list[dict] = []
    for r in rows[1:]:  # skip header
        cells = [c.strip() for c in r]
        item = cells[0] if cells else ""
        prefill = cells[1] if len(cells) > 1 else ""
        prefill = _strip_tickbox_tail(prefill)
        if not item and not prefill:
            continue
        if _value_is_blank(prefill):
            body = f"**{item}**: " if item else ""
            # blank pre-fill still gets a checkbox to fill in beside the label
            out.append(_todo(body + _WRITE_HINT, False) if not body
                       else _todo(f"**{item}**: {_WRITE_HINT}", False))
        else:
            out.append(_todo(f"**{item}**: {prefill}", False))
    return out


def _drop_confirm_column(rows: list[list[str]]) -> list[list[str]]:
    """Drop a redundant confirm column whose header is exactly '[✓/✗]' (it
    carried only a per-row '[ ]' tickbox; the to_do checkboxes replace it).
    Other columns are preserved. No-op if no such column exists."""
    if not rows:
        return rows
    header = [c.strip() for c in rows[0]]
    drop = [idx for idx, h in enumerate(header) if h in ("[✓/✗]", "[✓/✗ ]")]
    if not drop:
        return rows
    dropset = set(drop)
    return [[c for idx, c in enumerate(r) if idx not in dropset] for r in rows]


# ===========================================================================
# Final-pass scaffolding strip (belt-and-suspenders, ALL text-bearing blocks)
# ===========================================================================
# The field router (_route_field / _options_from / _strip_tickbox_tail) cleans
# scaffolding only from lines it recognises as form fields. Prose, quotes, and
# native table cells slip past it, so a literal '[ ]' inside a quote note, a
# '[✓/✗]' inside a heading, or '☐적용 …' inside a kept data table would reach
# Notion verbatim. This final pass walks EVERY emitted block's rich_text (incl.
# table cells) and removes residual NON-FUNCTIONAL markers, while preserving the
# meaningful flags [자동] / 【확인필요】 and any consumed [x] checked semantics.

# '[✓/✗]' confirm-column header marker (and a stray-space variant). Removed as a
# unit before tickbox handling so its inner ✓/✗ are not mistaken for content.
_SCAF_CHECK_COL_RE = re.compile(r"\[\s*✓\s*/\s*✗\s*\]")
# An EMPTY tickbox only — '[ ]' / '[]' / '[   ]'. A *checked* box ('[x]'/'[✓]')
# is meaningful and intentionally NOT matched here.
_SCAF_EMPTY_TICK_RE = re.compile(r"\[[ \t]*\]")
# A leftover correction arrow with no value, '→ ______' / '-> ___', anywhere.
_SCAF_ARROW_BLANK_RE = re.compile(r"(?:→|->)\s*_{2,}")
# A run of underscores used as a write-here blank ('______', also '___').
_SCAF_BLANK_RE = re.compile(r"_{2,}")
# An UNCHECKED box glyph (drop entirely; its trailing label text remains).
_SCAF_UNCHECKED_BOX_RE = re.compile(r"☐\s*")
# A CHECKED box glyph → keep the pre-selection as a textual '[x] ' (the task
# preserves "[x]/checked semantics already consumed") so a native table cell
# like '☑적용 ☐검증' becomes '[x] 적용 검증', losing no information and leaving
# zero box glyphs.
_SCAF_CHECKED_BOX_RE = re.compile(r"[☑☒]\s*")
# Collapse the double spaces / dangling separators a strip can leave behind.
_SCAF_MULTISPACE_RE = re.compile(r"[ \t]{2,}")
# Does this run contain ANY scaffolding token that the strip would remove? Used
# to gate the whitespace-mutating cleanups (BUG A): a run with NO scaffolding is
# returned with its original boundary whitespace intact, so genuine prose spaces
# and ' · ' separators around an adjacent **bold**/flag segment are never eaten.
# (The flag tokens [자동]/【확인필요】 are KEPT, not stripped, so they do NOT count.)
_SCAF_ANY_RE = re.compile(r"[☐☑☒]|\[\s*✓\s*/\s*✗\s*\]|\[[ \t]*\]|_{2,}"
                          r"|(?:→|->)\s*_{2,}")
# A run of leading / trailing horizontal whitespace (preserved verbatim across
# the scaffolding-strip so a boundary space between rich_text segments survives).
_LEAD_WS_RE = re.compile(r"^[ \t]+")
_TRAIL_WS_RE = re.compile(r"[ \t]+$")


def _strip_scaffolding_text(s: str, *, strip_markers: bool = False) -> str:
    """Remove residual non-functional form scaffolding from an arbitrary text
    run (prose, quote, heading, table cell, …). KEEPS [자동] / 【확인필요】 and a
    consumed checked marker; converts a leftover checked box glyph to '[x] '.
    Collapses the resulting whitespace. Idempotent.

    With strip_markers=True, ALSO removes residual UNMATCHED emphasis markers
    ('*', stray '`', lone '_'). The caller passes this only for PLAIN (non-
    annotated) text segments — a real `code`/bold/italic segment's content is
    left alone, so a legitimate '*' or '`' inside a code span is never harmed."""
    if not s:
        return s
    if strip_markers:
        s = _strip_residual_markers(s)
    # BUG A: a rich_text segment is only a PIECE of a larger logical line, so the
    # space (or ' · ') that lands at a segment's edge is the real character that
    # separates it from the adjacent **bold**/flag segment — it must NOT be eaten.
    # If this run carries NO scaffolding token, there is nothing to strip and no
    # stranded whitespace to tidy, so return it verbatim (boundary spaces intact).
    if not _SCAF_ANY_RE.search(s):
        return s
    # Scaffolding IS present. Capture the run's original outer whitespace so a real
    # boundary space adjacent to (but not part of) the scaffolding is restored
    # afterwards, then strip + tidy on the interior only.
    lead = _LEAD_WS_RE.match(s)
    lead_ws = lead.group(0) if lead else ""
    trail = _TRAIL_WS_RE.search(s)
    trail_ws = trail.group(0) if trail else ""
    s = _SCAF_CHECK_COL_RE.sub("", s)          # '[✓/✗]'  →  (drop)
    s = _SCAF_CHECKED_BOX_RE.sub("[x] ", s)     # '☑'/'☒' →  '[x] '
    s = _SCAF_UNCHECKED_BOX_RE.sub("", s)       # '☐'     →  (drop)
    s = _SCAF_ARROW_BLANK_RE.sub("", s)         # '→ ___' →  (drop, w/ arrow)
    s = _SCAF_BLANK_RE.sub("", s)               # '______'→  (drop)
    s = _SCAF_EMPTY_TICK_RE.sub("", s)          # '[ ]'   →  (drop)
    # Tidy whitespace + separators stranded by the removals.
    s = _SCAF_MULTISPACE_RE.sub(" ", s)
    # drop a now-dangling separator left where a marker used to sit
    s = re.sub(r"\s+([·;])\s*$", "", s)
    # a paren/bracket whose content was the stripped marker, e.g. '칸 (☐)' →
    # '칸' or '( )' → '': remove an emptied wrapper rather than leave '( )'.
    s = re.sub(r"[（(]\s*[)）]", "", s)
    s = re.sub(r"[\[【]\s*[\]】]", "", s)
    # a space that now sits just before a closing paren, e.g. '칸 )' → '칸)'.
    s = re.sub(r"\s+([)）\]】])", r"\1", s)
    # a space that now sits just after an opening paren, e.g. '( 예' → '(예'.
    s = re.sub(r"([(（\[【])\s+", r"\1", s)
    # Restore the ORIGINAL outer whitespace the run arrived with (the cleanups
    # above, incl. the old trailing rstrip, would otherwise drop a real boundary
    # space). The interior is fully tidied; only the edges are made verbatim.
    core = s.strip(" \t")
    return lead_ws + core + trail_ws


def _scrub_rich_text(rt: list[dict]) -> list[dict]:
    """Apply _strip_scaffolding_text to each segment's text content in place,
    preserving annotations. Drops a segment that became empty (so no empty
    text objects are sent), but always leaves at least nothing-extra."""
    out: list[dict] = []
    for seg in rt:
        if seg.get("type") != "text":
            out.append(seg)
            continue
        txt = seg.get("text", {})
        content = txt.get("content", "")
        # Strip residual emphasis markers only from PLAIN runs (no annotations):
        # an annotated code/bold/italic segment already had its delimiters
        # consumed and may legitimately hold a '*' (kept intact).
        plain = not seg.get("annotations")
        cleaned = _strip_scaffolding_text(content, strip_markers=plain)
        if cleaned == content:
            out.append(seg)
            continue
        if cleaned == "":
            # segment is now pure scaffolding — drop it entirely
            continue
        new_seg = dict(seg)
        new_seg["text"] = {**txt, "content": cleaned}
        out.append(new_seg)
    return out


def _scrub_and_decorate(rt: list[dict]) -> list[dict]:
    """Final per-run transform: strip residual scaffolding (drops pure-
    scaffolding segments), THEN decorate — apply URL/DOI links and flag colour-
    coding. Scaffolding runs first so the flag counts reflect what truly ships
    and a stripped '______'/'[ ]' never sits inside a coloured/linked span."""
    return _decorate_rich_text(_scrub_rich_text(rt))


def _scrub_block_scaffolding(blocks: list[dict]) -> None:
    """Final pass: scrub residual scaffolding AND decorate (colour the flag
    tokens + linkify URLs/DOIs) for EVERY text-bearing block — paragraph /
    heading_1..3 / quote / callout / bulleted_/numbered_list_item / to_do label
    / AND each table cell's rich_text. Mutates blocks in place."""
    for b in blocks:
        t = b.get("type")
        obj = b.get(t)
        if not isinstance(obj, dict):
            continue
        if t == "table":
            for row in obj.get("children", []):
                tr = row.get("table_row", {})
                tr["cells"] = [_scrub_and_decorate(cell)
                               for cell in tr.get("cells", [])]
            continue
        if "rich_text" in obj:
            obj["rich_text"] = _scrub_and_decorate(obj["rich_text"])


# ===========================================================================
# Visual separation pass (dividers between sections / per-project blocks)
# ===========================================================================
# The page should read as separated sections, not a wall of text. The source
# markdown already carries '---' dividers before most §A–§F section headings,
# but the per-project blocks (heading_3 '▣ 가설 #A1/#A2/#A3' + the 'B-요약' table)
# run back-to-back with no break. This pass inserts a divider BEFORE every
# heading_2 (major section) and every heading_3 (per-project sub-block) that is
# not already preceded by one — idempotent w.r.t. the source '---', and it does
# NOT over-divide (no divider after every to_do; only at heading boundaries).

def _insert_section_dividers(blocks: list[dict]) -> list[dict]:
    """Return a new block list with a divider inserted before each heading_2 /
    heading_3 that lacks one. Never inserts as the first block, never right
    after the page title (heading_1), and never creates consecutive dividers."""
    out: list[dict] = []
    for b in blocks:
        t = b.get("type")
        if t in ("heading_2", "heading_3"):
            prev = out[-1].get("type") if out else None
            # Insert a divider unless: nothing precedes (page top), the title
            # (heading_1) immediately precedes (keep title→intro tight), or a
            # divider already sits there (don't double the source '---').
            if prev is not None and prev not in ("divider", "heading_1"):
                out.append(_divider())
        out.append(b)
    return out


# ===========================================================================
# Top count-summary callout ("채움 현황 — …")
# ===========================================================================
# A single coloured callout placed right after the page-title heading_1 (before
# 들어가며) so the researcher sees, at a glance, how many fields are auto-filled
# vs need confirmation vs blank. Counts are taken from the FINAL decorated
# blocks — i.e. the number of segments actually carrying each flag colour — so
# they are exactly the tokens that ship (operator appendix + HTML comments were
# already stripped upstream by preprocess()).
#
# READABILITY note (honest limitation): the Notion API exposes NO per-block font
# size or line-height control (block typography is fixed by the Notion client /
# page theme), so we cannot widen line spacing or shrink fonts programmatically.
# What we CAN do for breathing room — and do — is structural: this top banner
# callout + the section/per-project dividers from _insert_section_dividers. We
# deliberately do NOT add a divider after every field (that over-divides and
# reads worse); dividers sit only at heading_2/heading_3 boundaries.

# Order in the summary (matches the convention intro): green → yellow → red.
_FLAG_SUMMARY_ORDER = ["[자동]", "【확인필요】", "(직접 작성)"]
_COLOR_TO_FLAG = {v: k for k, v in _FLAG_COLORS.items()}


def _count_emitted_flags(blocks: list[dict]) -> dict[str, int]:
    """Count emitted flag tokens by scanning every block's rich_text (incl.
    table cells) for segments that ARE a flag token (text == the token and the
    matching *_background colour). One such segment == one emitted body token.

    Matching on the exact token text (not merely the colour) makes this
    idempotent: the top summary callout's own spans are shaped 'TOKEN N' (e.g.
    '[자동] 65'), so they are NOT recounted — the function returns the same body
    count whether called before or after the summary callout is inserted."""
    counts = {tok: 0 for tok in _FLAG_COLORS}

    def _scan(rt: list[dict]) -> None:
        for seg in rt:
            if seg.get("type") != "text":
                continue
            content = seg.get("text", {}).get("content", "")
            color = (seg.get("annotations") or {}).get("color")
            if content in _FLAG_COLORS and color == _FLAG_COLORS[content]:
                counts[content] += 1

    for b in blocks:
        t = b.get("type")
        obj = b.get(t, {}) or {}
        if t == "table":
            for row in obj.get("children", []):
                for cell in row.get("table_row", {}).get("cells", []):
                    _scan(cell)
        elif isinstance(obj.get("rich_text"), list):
            _scan(obj["rich_text"])
    return counts


def _summary_callout(counts: dict[str, int]) -> dict:
    """Build the '채움 현황 — [자동] N · 【확인필요】 M · (직접 작성) K' callout, each
    'TOKEN N' span carrying the token's matching background colour. Plain '채움
    현황 — ' lead and ' · ' separators keep default colour."""
    rt: list[dict] = [{"type": "text",
                       "text": {"content": "채움 현황 — "},
                       "annotations": {"bold": True}}]
    for i, tok in enumerate(_FLAG_SUMMARY_ORDER):
        if i:
            rt.append({"type": "text", "text": {"content": " · "}})
        rt.append({"type": "text",
                   "text": {"content": f"{tok} {counts.get(tok, 0)}"},
                   "annotations": {"bold": True,
                                   "color": _FLAG_COLORS[tok]}})
    # A neutral icon + soft gray ground so the summary reads as a banner, not a
    # to-fill answer box (those are gray with a ✍️ pen — use a different glyph).
    return _notion.block_callout(rt, icon="🧭", color="gray_background")


def _insert_summary_callout(blocks: list[dict]) -> list[dict]:
    """Insert the count-summary callout right after the FIRST heading_1 (the
    page title), before 들어가며. If there is no heading_1 (shouldn't happen),
    prepend it at the very top. Returns a new list; idempotent enough that a
    re-run would simply recompute identical counts."""
    counts = _count_emitted_flags(blocks)
    callout = _summary_callout(counts)
    out: list[dict] = []
    inserted = False
    for b in blocks:
        out.append(b)
        if not inserted and b.get("type") == "heading_1":
            out.append(callout)
            inserted = True
    if not inserted:
        out.insert(0, callout)
    return out


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
    current_section = ""   # text of the most recent ## heading (for §C routing)
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
            if _is_basic_info_table(header):
                # A. 기본정보 → one to_do per field (confirm-or-correct).
                blocks.extend(_basic_info_to_todos(rows))
            else:
                # Genuine data table — keep native, but drop a redundant
                # [✓/✗] confirm column (the to_do checkboxes carry that job).
                rows = _drop_confirm_column(rows)
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
            htext = mh.group(2).strip()
            if level == 2:
                current_section = htext
            blocks.append(_heading(level, htext))
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

        # bulleted list item — gather continuation lines into one logical line,
        # then route (checkbox group / confirm to_do / free-text / plain bullet)
        mb = _BULLET_RE.match(line)
        if mb:
            text, i = _gather_logical(lines, i, n, mb.group(1))
            _route_field(text, blocks, default="bullet")
            continue

        # numbered list item (§I 1./2. theory-frame & format-preference are
        # multi-line checkbox groups) — same gather + route.
        mn = _NUM_RE.match(line)
        if mn:
            text, i = _gather_logical(lines, i, n, mn.group(1))
            _route_field(text, blocks, default="numbered")
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
        _route_field("\n".join(para_lines), blocks, default="paragraph",
                     section=current_section)
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
    # Belt-and-suspenders: scrub residual scaffolding from EVERY block's text
    # (incl. prose/quotes/table cells the field router never inspected) so no
    # literal '[ ]' / '[✓/✗]' / '☐' / '______' — or residual '*'/'`'/lone '_'
    # emphasis marker — ever reaches Notion.
    _scrub_block_scaffolding(blocks)
    # Visual separation: dividers before each section / per-project heading.
    blocks = _insert_section_dividers(blocks)
    # Top banner: count-summary callout right after the title (after decoration,
    # so the flag colours it tallies are the ones that actually ship). Inserted
    # after the divider pass so it sits flush under the heading_1, before 들어가며.
    blocks = _insert_summary_callout(blocks)
    return blocks, warnings


def _count_tables(blocks: list[dict]) -> int:
    return sum(1 for b in blocks if b.get("type") == "table")


def _block_type_summary(blocks: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for b in blocks:
        counts[b["type"]] = counts.get(b["type"], 0) + 1
    return counts


# Columns reported in the per-file block-type table (order = interactivity first)
_REPORT_COLS = ["to_do", "callout", "table", "heading_1", "heading_2",
                "heading_3", "paragraph", "bulleted_list_item",
                "numbered_list_item", "quote", "divider", "code"]
_COL_LABEL = {"to_do": "to_do", "callout": "callout", "table": "table",
              "heading_1": "h1", "heading_2": "h2", "heading_3": "h3",
              "paragraph": "para", "bulleted_list_item": "bull",
              "numbered_list_item": "num", "quote": "quote",
              "divider": "div", "code": "code"}


def _block_text(b: dict, limit: int = 40) -> str:
    """First ~limit chars of a block's text (for the sample dumps)."""
    t = b["type"]
    obj = b.get(t, {})
    if t == "table":
        w = obj.get("table_width")
        rows = len(obj.get("children", []))
        return f"[{w}col x {rows}row]"
    if t == "divider":
        return "----"
    rt = obj.get("rich_text", [])
    s = "".join(x.get("text", {}).get("content", "") for x in rt)
    s = s.replace("\n", " ")
    prefix = ""
    if t == "to_do":
        prefix = "[x] " if obj.get("checked") else "[ ] "
    elif t == "callout":
        ital = (rt[0].get("annotations", {}).get("italic")) if rt else False
        prefix = "(placeholder) " if ital else ""
    out = prefix + s
    return out[:limit]


def _dump_chunk(blocks: list[dict], lo: int, hi: int, title: str) -> None:
    print(f"\n  --- {title}  (blocks {lo}..{hi - 1}) ---")
    for j in range(lo, min(hi, len(blocks))):
        b = blocks[j]
        print(f"    {j:3}  {b['type']:20} | {_block_text(b)}")


def _find_heading(blocks: list[dict], level: int, *needles: str,
                  start: int = 0) -> int:
    """Index of the first heading_<level> whose text contains any needle, else
    -1. Used to locate sample chunks for the eyeball dump."""
    key = f"heading_{level}"
    for j in range(start, len(blocks)):
        b = blocks[j]
        if b["type"] != key:
            continue
        txt = "".join(x.get("text", {}).get("content", "")
                      for x in b[key]["rich_text"])
        if any(nd in txt for nd in needles):
            return j
    return -1


def _print_sample_dumps(init: str, blocks: list[dict]) -> None:
    """Print the two representative converted chunks the operator asked to
    eyeball: (a) one B-block (#A1) and (b) §I + §E + §F of the file."""
    print(f"\n{'=' * 72}\nSAMPLE BLOCK DUMP — {init} (type + first ~40 chars)\n"
          f"{'=' * 72}")
    # (a) the #A1 B-block: from its heading_3 to the next heading_3.
    a1 = _find_heading(blocks, 3, "#A1", "A1", "가설 #A1")
    if a1 < 0:
        a1 = _find_heading(blocks, 3, "가설")  # blank template fallback
    if a1 >= 0:
        nxt = _find_heading(blocks, 3, "▣", start=a1 + 1)
        end = nxt if nxt > a1 else min(a1 + 18, len(blocks))
        _dump_chunk(blocks, a1, end, "(a) B-block #A1")
    else:
        print("    (a) could not locate an #A1 block")
    # (b) §E + §F + §I: from §E heading_2 to EOF (these are the trailing
    #     sections; dumping E→end captures E, F, I in order).
    e = _find_heading(blocks, 2, "E. Computational", "Computational modeling")
    if e >= 0:
        _dump_chunk(blocks, e, len(blocks), "(b) §E + §F + §I (to EOF)")
    else:
        print("    (b) could not locate §E heading")


# ===========================================================================
# Dry-run integrity scans (residual emphasis markers + form scaffolding)
# ===========================================================================
# Two guards on the emitted blocks. Both must report ZERO for a clean publish:
#   (1) residual emphasis markers — any '*' / '**', lone markdown '_', stray
#       '`' that survived inline parsing (this survey has NO legitimate literal
#       asterisk/backtick, and a lone '_' is always a broken emphasis marker);
#   (2) form scaffolding — literal '[ ]' / '☐' / '______' that should have been
#       converted into native to_do / callout blocks.
# A non-zero count prints the offending block texts so they can be fixed.

# A lone markdown underscore (NOT an identifier underscore — '\w' is Unicode-
# aware so 'σ_abs' is safe — and NOT part of a '______' run). Edge-aware so a
# leading/trailing emphasis marker ('_x' / 'x_') is also flagged.
_LONE_US_SCAN_RE = re.compile(
    r"(?<!\w)_(?!\w)"
    r"|(?:^|(?<=[^\w]))_(?=\w)"
    r"|(?<=\w)_(?:(?=[^\w])|$)"
)
# Empty tickbox '[ ]' and the write-here blank '______' (a checked '[x]' is a
# deliberately-kept textual mark and is NOT a leak).
_FORM_TICK_SCAN_RE = re.compile(r"\[[ \t]*\]")
_FORM_BLANK_SCAN_RE = re.compile(r"_{3,}")
_FORM_BOX_SCAN_RE = re.compile(r"[☐☑☒]")


def _iter_block_texts(blocks: list[dict]):
    """Yield (block_index, type_label, text) for EVERY text-bearing run across
    all blocks, including each individual table cell. Used by the dry-run
    integrity scans below."""
    for idx, b in enumerate(blocks):
        t = b.get("type")
        obj = b.get(t, {}) or {}
        if t == "table":
            for ri, row in enumerate(obj.get("children", [])):
                cells = row.get("table_row", {}).get("cells", [])
                for ci, cell in enumerate(cells):
                    s = "".join(x.get("text", {}).get("content", "")
                                for x in cell)
                    yield idx, f"table[r{ri}c{ci}]", s
            continue
        rt = obj.get("rich_text")
        if isinstance(rt, list):
            s = "".join(x.get("text", {}).get("content", "") for x in rt)
            yield idx, t, s


def _scan_residual_markers(built: dict[str, list[dict]]) -> int:
    """Scan EVERY emitted rich_text (incl. table cells) for residual emphasis
    markers — '*' / '**', lone markdown '_', stray '`'. Prints the count and,
    if non-zero, the offending block texts. Returns the total marker count."""
    print("\n" + "-" * 72)
    print("INTEGRITY SCAN (a) — residual emphasis markers (must be 0):")
    grand = 0
    offending: list[str] = []
    for init in sorted(built):
        n_star = n_us = n_tick = 0
        for idx, t, s in _iter_block_texts(built[init]):
            stars = s.count("*")
            ticks = s.count("`")
            us = len(_LONE_US_SCAN_RE.findall(s))
            if stars or ticks or us:
                offending.append(
                    f"    [{init}] #{idx} {t}: *={stars} `={ticks} "
                    f"lone_={us} :: {s[:70]!r}")
            n_star += stars
            n_us += us
            n_tick += ticks
        sub = n_star + n_us + n_tick
        grand += sub
        flag = "OK" if sub == 0 else "LEAK"
        print(f"    {init}: '*'={n_star}  lone'_'={n_us}  '`'={n_tick}  "
              f"-> {sub}  [{flag}]")
    print(f"    TOTAL residual markers across all 7 surveys: {grand}")
    if offending:
        print("    OFFENDING BLOCKS:")
        for line in offending:
            print(line)
    else:
        print("    -> bold/italic/code became annotations; nothing leaked. ✓")
    return grand


def _scan_form_markers(built: dict[str, list[dict]]) -> int:
    """Scan for literal form scaffolding that should have become native blocks:
    empty '[ ]' tickbox, ☐/☑/☒ box glyph, '______' blank. Returns total."""
    print("-" * 72)
    print("INTEGRITY SCAN (d) — form-marker scan ('[ ]' / ☐ / '______', "
          "must be 0):")
    grand = 0
    offending: list[str] = []
    for init in sorted(built):
        sub = 0
        for idx, t, s in _iter_block_texts(built[init]):
            n = (len(_FORM_TICK_SCAN_RE.findall(s))
                 + len(_FORM_BOX_SCAN_RE.findall(s))
                 + len(_FORM_BLANK_SCAN_RE.findall(s)))
            if n:
                offending.append(f"    [{init}] #{idx} {t}: {s[:70]!r}")
            sub += n
        grand += sub
        print(f"    {init}: {sub}  [{'OK' if sub == 0 else 'LEAK'}]")
    print(f"    TOTAL form markers across all 7 surveys: {grand}")
    if offending:
        print("    OFFENDING BLOCKS:")
        for line in offending:
            print(line)
    else:
        print("    -> all checkboxes/blanks are native to_do/callout blocks. ✓")
    return grand


def _scan_annotations(built: dict[str, list[dict]]) -> int:
    """Confirm bold/italic/code formatting survives AS ANNOTATIONS (not literal
    markers). Counts annotated segments; must be > 0. Returns the total."""
    print("-" * 72)
    print("INTEGRITY SCAN (c) — annotated segments (bold/italic/code, "
          "must be > 0):")
    grand = 0
    for init in sorted(built):
        nb = ni = nc = 0
        for b in built[init]:
            t = b.get("type")
            obj = b.get(t, {}) or {}
            cells = []
            if t == "table":
                for row in obj.get("children", []):
                    for cell in row.get("table_row", {}).get("cells", []):
                        cells.append(cell)
            elif isinstance(obj.get("rich_text"), list):
                cells.append(obj["rich_text"])
            for rt in cells:
                for seg in rt:
                    a = seg.get("annotations", {})
                    nb += 1 if a.get("bold") else 0
                    ni += 1 if a.get("italic") else 0
                    nc += 1 if a.get("code") else 0
        sub = nb + ni + nc
        grand += sub
        print(f"    {init}: bold={nb}  italic={ni}  code={nc}  -> {sub}")
    print(f"    TOTAL annotated segments across all 7 surveys: {grand} "
          f"[{'OK' if grand > 0 else 'FAIL — formatting lost!'}]")
    return grand


def _iter_block_segments(blocks: list[dict]):
    """Yield (block_index, type_label, segment_dict) for EVERY rich_text segment
    across all blocks, incl. each table cell. Used by the colour/link reports."""
    for idx, b in enumerate(blocks):
        t = b.get("type")
        obj = b.get(t, {}) or {}
        if t == "table":
            for ri, row in enumerate(obj.get("children", [])):
                for ci, cell in enumerate(row.get("table_row", {})
                                          .get("cells", [])):
                    for seg in cell:
                        yield idx, f"table[r{ri}c{ci}]", seg
            continue
        rt = obj.get("rich_text")
        if isinstance(rt, list):
            for seg in rt:
                yield idx, t, seg


def _report_flag_counts(built: dict[str, list[dict]]) -> None:
    """(a) Per-file flag-token counts — exactly the numbers the top callout
    shows (counted from the emitted coloured segments)."""
    print("-" * 72)
    print("REPORT (a) — per-file flag counts (the top '채움 현황' callout numbers):")
    print(f"    {'file':<5}  {'[자동]':>8}  {'【확인필요】':>10}  "
          f"{'(직접 작성)':>10}")
    tot = {tok: 0 for tok in _FLAG_COLORS}
    for init in sorted(built):
        c = _count_emitted_flags(built[init])
        for tok in tot:
            tot[tok] += c[tok]
        print(f"    {init:<5}  {c['[자동]']:>8}  {c['【확인필요】']:>10}  "
              f"{c['(직접 작성)']:>10}")
    print(f"    {'ALL':<5}  {tot['[자동]']:>8}  {tot['【확인필요】']:>10}  "
          f"{tot['(직접 작성)']:>10}")


# The top-summary banner emits coloured spans shaped 'TOKEN N' (e.g. '[자동] 42').
# The colour-bleed check accepts these so they are not mistaken for a body bleed.
_SUMMARY_SPAN_RE = re.compile(
    "(?:" + "|".join(re.escape(t) for t in _FLAG_COLORS) + r")\s+\d+")


def _report_flag_colors(built: dict[str, list[dict]]) -> int:
    """(b) Confirm colour annotations were applied — verify every flag token is
    emitted as its OWN segment carrying the matching *_background colour, and
    print a couple of sample coloured segments (text + colour) per file.
    Returns the count of MIS-coloured flag segments (must be 0)."""
    print("-" * 72)
    print("REPORT (b) — colour annotations applied (sample coloured segments):")
    mismatches = 0
    for init in sorted(built):
        samples: list[str] = []
        seen_colors: set[str] = set()
        for idx, t, seg in _iter_block_segments(built[init]):
            if seg.get("type") != "text":
                continue
            content = seg.get("text", {}).get("content", "")
            color = (seg.get("annotations") or {}).get("color")
            # A flag token must be its own segment with the right colour.
            if content in _FLAG_COLORS:
                want = _FLAG_COLORS[content]
                if color != want:
                    mismatches += 1
                    print(f"    [{init}] #{idx} {t}: token {content!r} colour="
                          f"{color!r} EXPECTED {want!r}  ** MISMATCH **")
                elif want not in seen_colors:
                    seen_colors.add(want)
                    bold = bool((seg.get("annotations") or {}).get("bold"))
                    samples.append(f"{content!r} -> {color}"
                                   + ("  (+bold)" if bold else ""))
            # A coloured segment whose text is NOT a flag token would be a split
            # bug (colour bled onto surrounding text) — EXCEPT the top summary
            # callout, whose coloured spans are intentionally 'TOKEN N' (token +
            # count). Recognise that shape and skip it.
            elif color in _COLOR_TO_FLAG:
                want_tok = _COLOR_TO_FLAG[color]
                if _SUMMARY_SPAN_RE.fullmatch(content):
                    continue  # the count-summary banner span, not a bleed
                mismatches += 1
                print(f"    [{init}] #{idx} {t}: colour {color!r} (for "
                      f"{want_tok!r}) on non-token text {content[:40]!r}  "
                      f"** BLEED **")
        n_seg = sum(1 for _ in _iter_block_segments(built[init]))
        print(f"    {init}: {len(seen_colors)}/3 flag colours present "
              f"({n_seg} segs); samples: "
              + (" | ".join(samples) if samples else "(none)"))
    if mismatches == 0:
        print("    -> every flag token is its own correctly-coloured segment; "
              "no colour bleed. ✓")
    return mismatches


def _report_links(built: dict[str, list[dict]]) -> None:
    """(c) Links detected per file — count of segments carrying a text.link plus
    one sample (label -> href)."""
    print("-" * 72)
    print("REPORT (c) — links detected (clickable URL/DOI segments):")
    for init in sorted(built):
        n = 0
        sample = ""
        for idx, t, seg in _iter_block_segments(built[init]):
            link = (seg.get("text") or {}).get("link")
            if isinstance(link, dict) and link.get("url"):
                n += 1
                if not sample:
                    label = seg.get("text", {}).get("content", "")
                    sample = f"{label[:50]!r} -> {link['url']}"
        print(f"    {init}: {n} link(s)" + (f"  e.g. {sample}" if sample else ""))


# Math/formula identifiers that MUST survive verbatim (never altered by the
# residual-'_'-strip or the URL/DOI linkifier). Keyed by the file where they
# occur; the report confirms each token still appears intact in some emitted
# rich_text run.
_MATH_TOKENS: dict[str, list[str]] = {
    "JOP": ["σ_abs", "σ_abs_leak", "(α)=±3.3"],
    "BYL": ["θ̂ − θ", "var(θ̂)"],
    "BHL": ["θ̂ − θ"],
    "JYK": ["θ̂−θ", "var(θ̂)"],
    "MSY": ["var(θ̂)"],
    "SYJ": ["θ̂−θ"],
}


def _report_math_identifiers(built: dict[str, list[dict]]) -> int:
    """(e) Confirm formula/identifier text passed through verbatim. For each
    known math token, check it appears in some emitted run of its file. Returns
    the number of MISSING tokens (must be 0)."""
    print("-" * 72)
    print("REPORT (e) — math identifiers survive verbatim "
          "(σ_abs, θ̂, var(θ̂), α=±3.3 …):")
    missing = 0
    for init in sorted(built):
        toks = _MATH_TOKENS.get(init)
        if not toks:
            continue
        haystacks = [s for _i, _t, s in _iter_block_texts(built[init])]
        joined = "\n".join(haystacks)
        results = []
        for tok in toks:
            ok = tok in joined
            if not ok:
                missing += 1
            results.append(f"{tok}{'✓' if ok else ' ✗MISSING'}")
        print(f"    {init}: " + "  ·  ".join(results))
    if missing == 0:
        print("    -> all formula/identifier tokens intact "
              "(underscores + Unicode math preserved). ✓")
    return missing


def dry_run(targets: list[str], dump: Optional[list[str]] = None,
            parent_lookup: bool = True) -> int:
    print("=" * 72)
    print("DRY-RUN — parsing surveys, creating NOTHING in Notion")
    print("=" * 72)
    total_blocks = total_tables = 0
    any_warn = False
    built: dict[str, list[dict]] = {}

    # Header for the per-file block-type table.
    hdr = "  ".join(f"{_COL_LABEL[c]:>5}" for c in _REPORT_COLS)
    print(f"\n{'file':<5}  {'TOTAL':>5}  {hdr}")
    print("-" * (7 + 7 + len(hdr) + 2))

    for init in targets:
        try:
            blocks, warnings = build_survey_blocks(init)
        except FileNotFoundError as e:
            print(f"{init:<5}  MISSING survey file: {e}")
            any_warn = True
            continue
        built[init] = blocks
        ntables = _count_tables(blocks)
        total_blocks += len(blocks)
        total_tables += ntables
        summary = _block_type_summary(blocks)
        row = "  ".join(f"{summary.get(c, 0):>5}" for c in _REPORT_COLS)
        print(f"{init:<5}  {len(blocks):>5}  {row}")
        # surface oversized table_row counts (Notion append nuance)
        big_tables = [b["table"]["table_width"] for b in blocks
                      if b.get("type") == "table"
                      and len(b["table"]["children"]) > MAX_TABLE_ROWS_PER_APPEND]
        if big_tables:
            print(f"       NOTE: {len(big_tables)} table(s) exceed "
                  f"{MAX_TABLE_ROWS_PER_APPEND} rows (auto-split on apply)")
        if warnings:
            any_warn = True
            for w in warnings:
                print(f"       WARN: {w}")

    print("-" * (7 + 7 + len(hdr) + 2))
    print(f"TOTAL: {len(built)} surveys, {total_blocks} blocks, "
          f"{total_tables} tables")

    # Two representative sample dumps (default: JOP, or each --dump target).
    dump_targets = dump if dump else (["JOP"] if "JOP" in built else
                                      (list(built)[:1] if built else []))
    for init in dump_targets:
        if init in built:
            _print_sample_dumps(init, built[init])

    # Feature reports (flag colours, links, math identifiers) — the deliverables
    # for this extension. (a) the top-callout numbers, (b) colour annotations
    # applied (+ samples), (c) links detected, (e) math identifiers survive.
    if built:
        _report_flag_counts(built)
        color_mis = _report_flag_colors(built)
        _report_links(built)
        math_missing = _report_math_identifiers(built)
        if color_mis > 0:
            any_warn = True
            print(f"\n  *** {color_mis} flag segment(s) MIS-COLOURED / colour "
                  f"bleed — fix before --apply. ***")
        if math_missing > 0:
            any_warn = True
            print(f"\n  *** {math_missing} math identifier(s) ALTERED/MISSING — "
                  f"fix before --apply. ***")

    # Integrity scans on the emitted blocks (the point of this fix). Both the
    # residual-marker scan and the form-marker scan must be 0; annotated
    # segments must be > 0. A non-zero leak (or zero formatting) is a hard fail.
    residual = _scan_residual_markers(built) if built else 0
    annotated = _scan_annotations(built) if built else 0
    form = _scan_form_markers(built) if built else 0
    if residual > 0:
        any_warn = True
        print(f"\n  *** {residual} RESIDUAL EMPHASIS MARKER(S) WOULD REACH "
              f"NOTION — fix before --apply. ***")
    if form > 0:
        any_warn = True
        print(f"\n  *** {form} FORM MARKER(S) ('[ ]'/☐/'______') WOULD REACH "
              f"NOTION — fix before --apply. ***")
    if built and annotated == 0:
        any_warn = True
        print("\n  *** NO bold/italic/code annotations emitted — formatting "
              "was lost. ***")

    # parent lookup (read-only; skippable for a fully offline report)
    print("-" * 72)
    if parent_lookup:
        print("Parent-page lookup (read-only):")
        try:
            pid, title = resolve_parent_page()
            print(f"    digest DB id : {_notion.digest_db_id()}")
            print(f"    parent page  : {pid}")
            print(f"    parent title : {title!r}")
            print(f"    -> would create container page {CONTAINER_TITLE!r} "
                  f"here, then 1 child page per researcher.")
        except _notion.NotionError as e:
            any_warn = True
            print(f"    [ERROR] parent lookup failed: {e}")
    else:
        print("Parent-page lookup: SKIPPED (--no-parent-lookup).")
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
    ap.add_argument("--dump", metavar="INIT", action="append", default=None,
                    help="Print the converted block sequence (sample chunks) "
                         "for this researcher; repeatable. Default: JOP.")
    ap.add_argument("--no-parent-lookup", action="store_true",
                    help="Skip the read-only Notion parent-page lookup "
                         "(offline block report only).")
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

    dumps = None
    if args.dump:
        dumps = []
        for d in args.dump:
            d = d.strip().upper()
            if d not in RESEARCHERS:
                print(f"[error] --dump {d!r}: unknown researcher.",
                      file=sys.stderr)
                return 2
            dumps.append(d)

    if args.apply:
        return apply(targets)
    return dry_run(targets, dump=dumps, parent_lookup=not args.no_parent_lookup)


if __name__ == "__main__":
    raise SystemExit(main())
