#!/usr/bin/env python3
"""
scripts/archive/ingest_survey.py — parse the v13 research-profile survey into
the P28 structured Postgres memory (archive_survey_* tables).

WHAT IT DOES
  Reads a researcher's survey as a Notion BLOCK TREE and extracts the
  structured rows the connection-based recommender (P28b) consumes:
    profile · aims (the connection anchor) · negatives (the veto) · keywords
    (+ operational defs) · methods · models · PIs.
  Every value carries a confidence derived from the survey flag
  ([자동]=high · 【확인필요】=medium · (직접 작성)/blank=low) and from whether
  the researcher CHECKED the confirm box. The verbatim section text is kept in
  raw_jsonb so any [자동] can be traced back to its source.

SOURCES (--source)
  prefill  : synthesise the EXACT published block tree from the pre-fill
             markdown (state/archive/surveys/<INIT>.md) via
             notion_survey_pages.build_survey_blocks. This is the default —
             it lets the parser be built/tested offline against the same
             blocks the researchers are editing, WITHOUT touching live Notion.
  fixture  : load a hand-authored edited-sample block tree from JSON
             (--fixture PATH) — used to test researcher-edit tolerance + the
             LLM-assist hook.
  notion   : read the LIVE researcher-edited pages (read-only retrieve +
             recursive block-children). GATED: only run after the operator
             confirms the surveys are complete (researchers are still editing;
             reading mid-edit is harmless but premature). Needs NOTION_API_KEY.

BOUNDARY
  * Default is DRY-RUN: parse + print + validate, write NOTHING.
  * --apply UPSERTs into csnl_paper_rec.archive_survey_* (operator-run via `!`,
    or under an explicit session grant). archive_responses + csnl_research are
    never touched. This script NEVER writes Notion.
  * The live-Notion path performs read-only GETs only.

LLM-ASSIST (the survey appendix asks for it)
  The deterministic parser handles every template-conformant cell (all of the
  pre-fill, and most researcher edits). A cell it cannot confidently structure
  (e.g. a researcher rewrote a B-요약 phenomenon cell as free prose, or added an
  exclude row without the contrast template) is recorded with needs_review=true
  and its raw text preserved; --emit-needs-review writes those to a JSONL for an
  operator-attended Opus structuring pass (the orchestrator fans out Opus
  sub-agents — NOT an in-script API call; no key lives here).

CLI
  python3 scripts/archive/ingest_survey.py                      # DRY-RUN, all 7, prefill
  python3 scripts/archive/ingest_survey.py --only JOP --json    # one, dump structured JSON
  python3 scripts/archive/ingest_survey.py --source fixture --fixture sample.json --only JOP
  python3 scripts/archive/ingest_survey.py --only JOP --emit-needs-review state/archive/_tmp/jop_review.jsonl
  ! python3 scripts/archive/ingest_survey.py --source notion --apply   # operator, surveys complete
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Optional

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "weekly"))
sys.path.insert(0, str(_HERE))

SURVEY_VERSION = "survey-v13"
RESEARCHERS = ["BHL", "BYL", "JOP", "JYK", "MSY", "SMJ", "SYJ"]

# Live page IDs (docs/MIGRATION-P28-PROMPT.md §1). Only used by --source notion.
NOTION_PAGE_IDS: dict[str, str] = {
    "BHL": "3762a38e-4f5f-8155-baff-d7282261ab29",
    "BYL": "3762a38e-4f5f-8163-9594-e9ffb7755431",
    "JOP": "3762a38e-4f5f-81d5-a4c6-c7b454a8a04c",
    "JYK": "3762a38e-4f5f-8180-9000-e402f1542518",
    "MSY": "3762a38e-4f5f-81c3-afdc-f18adadb88a3",
    "SMJ": "3762a38e-4f5f-8192-9dc8-f2ee905adcec",
    "SYJ": "3762a38e-4f5f-8175-99a4-fe5cd61596fe",
}

_TMP_DIR = _REPO_ROOT / "state" / "archive" / "_tmp"

# --------------------------------------------------------------- section names
# heading_2 text → internal section key. Matched by substring (the survey
# headers carry trailing ★최우선 / (선택) / (PI) decorations).
SECTION_KEYS: list[tuple[str, str]] = [
    ("들어가며", "intro"),
    ("기본 정보", "profile"),
    ("연구 프로젝트별", "aims"),
    ("빼고 싶은", "negatives"),
    ("검색 키워드", "keywords"),
    ("실험 장비", "infra"),
    ("관심 있는 연구자", "pis"),
    ("계산 모델", "models"),
    ("관심 방법론", "methods"),
]

# --------------------------------------------------------------- flag tokens
FLAG_AUTO = "[자동]"
FLAG_CHECK = "【확인필요】"
FLAG_WRITE = "(직접 작성)"
_FLAG_RE = re.compile(r"\[자동\]|【확인필요】|\(직접 작성\)")
# scaffolding that the publisher's scrubber may leave or the researcher may
# type — stripped from extracted values.
_PLACEHOLDER = "여기에 작성"
_BLANK_RUN_RE = re.compile(r"_{2,}")


# ===========================================================================
# Block-tree access (works on BOTH the POST shape that build_survey_blocks
# emits — table rows under block['table']['children'] — and the live retrieve
# shape — table rows fetched as the table block's children).
# ===========================================================================

def _btype(b: dict) -> str:
    return b.get("type") or ""


def _rt_text(rt: list) -> str:
    """Join a rich_text array to plain text. Prefers 'plain_text' (live
    retrieve) and falls back to text.content (POST shape)."""
    out = []
    for seg in rt or []:
        if not isinstance(seg, dict):
            continue
        if seg.get("plain_text") is not None:
            out.append(seg["plain_text"])
        else:
            out.append((seg.get("text") or {}).get("content", "") or "")
    return "".join(out)


def _block_text(b: dict) -> str:
    """Full text of a text-bearing block (paragraph/heading/to_do/callout/
    quote/list item). Empty for structural blocks (divider/table)."""
    t = _btype(b)
    obj = b.get(t)
    if isinstance(obj, dict) and isinstance(obj.get("rich_text"), list):
        return _rt_text(obj["rich_text"])
    return ""


def _todo_checked(b: dict) -> bool:
    return bool((b.get("to_do") or {}).get("checked"))


def _value_is_template(b: dict) -> bool:
    """True iff the confirm-field VALUE (rich_text after the first colon) is
    entirely italic — the survey wraps its example/placeholder option lists in
    *italics* (e.g. '*필수*: _orientation / spatial frequency / … position …_'),
    while a real [자동] answer or a researcher edit is NOT italic. This is the
    reliable signal that a field was left as the blank template (MSY) and must
    be treated as empty rather than ingested as a fabricated answer."""
    t = _btype(b)
    rt = (b.get(t) or {}).get("rich_text") or []
    joined = ""
    spans: list[tuple[int, int, dict]] = []
    for seg in rt:
        s = seg.get("plain_text")
        if s is None:
            s = (seg.get("text") or {}).get("content", "") or ""
        spans.append((len(joined), len(joined) + len(s), seg))
        joined += s
    m = re.search(r"[:：]", joined)
    if not m:
        return False
    cpos = m.end()
    any_content = False
    for lo, hi, seg in spans:
        if hi <= cpos:
            continue
        sub = joined[max(lo, cpos):hi]
        if _clean_value(sub).strip() == "":
            continue          # whitespace / flag-only run
        any_content = True
        if not (seg.get("annotations") or {}).get("italic"):
            return False      # a non-italic content run ⇒ a real answer
    return any_content


def _table_rows(b: dict, fetch_children: Optional[Callable[[str], list]]) -> list[list[str]]:
    """Return a table block's rows as list-of-cell-strings.
    POST shape: rows under b['table']['children']. Live shape: fetch via
    fetch_children(b['id'])."""
    tbl = b.get("table") or {}
    rows = tbl.get("children")
    if rows is None and fetch_children and b.get("id"):
        rows = fetch_children(b["id"])
    out: list[list[str]] = []
    for r in rows or []:
        if _btype(r) != "table_row":
            continue
        cells = (r.get("table_row") or {}).get("cells") or []
        out.append([_rt_text(c) for c in cells])
    return out


# ===========================================================================
# Source loaders
# ===========================================================================

def load_blocks_prefill(init: str) -> list[dict]:
    """Synthesise the published block tree from the pre-fill markdown — the
    exact blocks the researcher is editing — with zero network access."""
    import notion_survey_pages as nsp  # noqa: E402
    blocks, _warnings = nsp.build_survey_blocks(init)
    return blocks


def load_blocks_fixture(path: Path) -> list[dict]:
    data = json.loads(path.read_text("utf-8"))
    # Accept either a bare list of blocks or {"blocks": [...]}.
    return data["blocks"] if isinstance(data, dict) else data


def load_blocks_notion(init: str) -> tuple[list[dict], Callable[[str], list]]:
    """LIVE read-only. Returns (top_level_blocks, fetch_children). GATED:
    only call after the operator confirms surveys are complete."""
    import _notion  # noqa: E402
    page_id = NOTION_PAGE_IDS.get(init)
    if not page_id:
        raise SystemExit(f"no Notion page id for {init}")
    top = _notion.list_block_children(page_id)
    return top, _notion.list_block_children


# ===========================================================================
# Sectionizer
# ===========================================================================

def _section_key(heading_text: str) -> Optional[str]:
    for needle, key in SECTION_KEYS:
        if needle in heading_text:
            return key
    return None


def sectionize(blocks: list[dict]) -> dict[str, list[dict]]:
    """Partition top-level blocks by heading_2 into {section_key: [blocks]}.
    The page title (heading_1) and the summary callout precede the first
    heading_2 and are ignored."""
    out: dict[str, list[dict]] = {}
    cur: Optional[str] = None
    for b in blocks:
        if _btype(b) == "heading_2":
            cur = _section_key(_block_text(b))
            if cur:
                out.setdefault(cur, [])
            continue
        if cur:
            out[cur].append(b)
    return out


def split_project_blocks(aim_blocks: list[dict]) -> tuple[list[tuple[dict, list[dict]]], list[dict]]:
    """Within the 연구 프로젝트별 section, split by heading_3 into
    [(heading_block, [body_blocks]), ...] for the per-project blocks, and
    return the '프로젝트 한눈에 보기' body separately (the B-요약 table)."""
    projects: list[tuple[dict, list[dict]]] = []
    summary_body: list[dict] = []
    cur_head: Optional[dict] = None
    cur_body: list[dict] = []
    in_summary = False

    def _flush():
        nonlocal cur_head, cur_body
        if cur_head is not None:
            projects.append((cur_head, cur_body))
        cur_head, cur_body = None, []

    for b in aim_blocks:
        if _btype(b) == "heading_3":
            txt = _block_text(b)
            if "한눈에 보기" in txt:
                _flush()
                in_summary = True
                continue
            _flush()
            in_summary = False
            cur_head = b
            cur_body = []
            continue
        if in_summary:
            summary_body.append(b)
        elif cur_head is not None:
            cur_body.append(b)
    _flush()
    return projects, summary_body


# ===========================================================================
# Value cleaning + confidence
# ===========================================================================

def _confidence(raw: str, checked: bool = False) -> str:
    """Derive field confidence from survey flags + confirm-checkbox state."""
    if checked:
        return "high"
    has_auto = FLAG_AUTO in raw
    has_check = FLAG_CHECK in raw
    has_write = FLAG_WRITE in raw
    if has_check:
        return "medium"
    if has_write:
        return "low"
    if has_auto:
        return "high"
    # researcher-written value with no flag, or empty
    return "high" if _clean_value(raw) else "low"


def _clean_value(s: str) -> str:
    """Strip flag tokens, placeholder, blank-runs, and tidy whitespace."""
    if not s:
        return ""
    s = _FLAG_RE.sub("", s)
    s = s.replace(_PLACEHOLDER, "")
    s = _BLANK_RUN_RE.sub("", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ;·—-\t")
    return s.strip()


def _split_label_value(text: str) -> tuple[str, str]:
    """Split a confirm-field 'label: value' on the FIRST colon (full-width or
    ASCII). Field labels in the survey never contain a colon, so first-colon is
    safe and keeps a value's interior ';'/'：' intact."""
    m = re.search(r"[:：]", text)
    if not m:
        return text.strip(), ""
    return text[:m.start()].strip(), text[m.end():].strip()


def _is_blank(v: str) -> bool:
    return _clean_value(v) == ""


# ===========================================================================
# Per-section extractors
# ===========================================================================

# project-block confirm-field label → aim column
_AIM_FIELD_MATCHERS: list[tuple[str, str]] = [
    ("Domain", "domain"),
    ("Population", "population"),
    ("Task", "task"),
    ("Phenomenon", "phenomenon"),
    ("비교 조건", "condition"),
    ("정량화 지표", "metric"),
    ("(유형1) 가설", "hypothesis"),
    ("(유형2) 탐구", "hypothesis"),
    ("메커니즘", "mechanism"),
    ("배경", "background"),
    ("Seed", "seed_paper"),
]

# 동시측정 option label substring → measure slug
_MEASURE_SLUGS: list[tuple[str, str]] = [
    ("행동", "behavior"),
    ("fMRI", "fmri"),
    ("EEG", "eeg_meg"),
    ("eye-tracking", "eye"),
    ("pupillometry", "pupil"),
    ("tES", "tes"),
]

_PROJECT_HEAD_RE = re.compile(r"프로젝트\s*(\d+)")
_TYPE_RE = re.compile(r"\[?\s*(유형[12])")
_CODENAME_RE = re.compile(r"프로젝트\s*\d+\s*\(([^)]+)\)")


def _aim_field_for(label: str) -> Optional[str]:
    for needle, col in _AIM_FIELD_MATCHERS:
        if label.startswith(needle) or needle in label[:24]:
            return col
    return None


def parse_aims(project_blocks: list[tuple[dict, list[dict]]],
               summary_rows: list[list[str]],
               needs: list[dict], init: str) -> list[dict]:
    """Project blocks + the B-요약 table → aim rows. The B-요약 tuple
    (domain/phenomenon/task/mechanism) is authoritative for the connection
    anchor; the block supplies metric/condition/direction/hypothesis/
    background/seed/population/measures."""
    # summary_rows[0] is the header; map ordinal -> {domain,phenomenon,task,mech}
    sum_by_ord: dict[str, dict] = {}
    for row in summary_rows[1:] if summary_rows else []:
        cells = [c.strip() for c in row]
        if not cells or not cells[0]:
            continue
        m = re.search(r"\d+", cells[0])
        if not m:
            continue
        o = m.group(0)
        sum_by_ord[o] = {
            "domain":     _clean_value(cells[1]) if len(cells) > 1 else "",
            "phenomenon": _clean_value(cells[2]) if len(cells) > 2 else "",
            "task":       _clean_value(cells[3]) if len(cells) > 3 else "",
            "mechanism":  _clean_value(cells[4]) if len(cells) > 4 else "",
            "_raw":       cells,
        }

    aims: list[dict] = []
    for head, body in project_blocks:
        htext = _block_text(head)
        m = _PROJECT_HEAD_RE.search(htext)
        if not m:
            continue
        ordn = m.group(1)
        # aim_id is the PK together with researcher_id, so it MUST be unique per
        # researcher. The project-heading ordinal is NOT reliably unique — a
        # malformed or duplicated heading (BYL's v13 survey carries two '프로젝트 2'
        # blocks, the 2nd a low-confidence parse artifact) collides and violates
        # archive_survey_aims_pkey, aborting the whole load. Use a running counter
        # over successfully-parsed aims; the heading ordinal stays in raw_jsonb.
        aim_id = f"P{len(aims) + 1}"
        tym = _TYPE_RE.search(htext)
        hyp_type = tym.group(1) if tym else "unknown"
        cnm = _CODENAME_RE.search(htext)
        aim_label = cnm.group(1).strip() if cnm else None

        fields: dict[str, str] = {}
        field_conf: dict[str, str] = {}
        measures: dict[str, bool] = {}
        raw_fields: dict[str, str] = {}
        in_measures = False

        for b in body:
            t = _btype(b)
            txt = _block_text(b)
            if t == "paragraph" and txt.strip().rstrip("：:").endswith("동시측정") \
                    or (t == "paragraph" and txt.strip().startswith("동시측정")):
                in_measures = True
                continue
            if t == "to_do" and in_measures:
                # measure option (until the next confirm-field-shaped to_do)
                label = txt
                col = _aim_field_for(_split_label_value(txt)[0])
                if col:  # a confirm field — measures group ended
                    in_measures = False
                else:
                    slug = next((s for needle, s in _MEASURE_SLUGS if needle in label), None)
                    if slug:
                        measures[slug] = _todo_checked(b)
                    continue
            if t in ("to_do", "bulleted_list_item", "paragraph"):
                label, value = _split_label_value(txt)
                col = _aim_field_for(label)
                if not col:
                    continue
                if in_measures:
                    in_measures = False
                checked = _todo_checked(b) if t == "to_do" else False
                # An italic-only value is the survey's blank template example
                # (MSY) — treat as empty, never ingest as a fabricated answer.
                if t in ("to_do", "bulleted_list_item") and _value_is_template(b):
                    value = ""
                # hypothesis: keep whichever type slot is non-blank
                if col == "hypothesis" and not _is_blank(fields.get("hypothesis", "")):
                    if _is_blank(value):
                        continue
                raw_fields[col] = txt
                fields[col] = _clean_value(value)
                field_conf[col] = "low" if _is_blank(value) else _confidence(txt, checked)

        s = sum_by_ord.get(ordn, {})
        # anchor = B-요약 table value, else block value
        domain = s.get("domain") or fields.get("domain", "")
        phenomenon = s.get("phenomenon") or fields.get("phenomenon", "")
        task = s.get("task") or fields.get("task", "")
        mechanism = s.get("mechanism") or fields.get("mechanism", "")
        # direction: pulled from the 유형1 hypothesis tail or 유형2 speculation
        direction = ""
        # overall aim confidence = min over anchor fields
        anchor_confs = [field_conf.get(k, "low") for k in ("domain", "phenomenon", "task")]
        order = {"high": 2, "medium": 1, "low": 0}
        aim_conf = min(anchor_confs, key=lambda c: order.get(c, 0)) if anchor_confs else "low"

        # validation: a real aim needs at least a domain OR a phenomenon (both
        # are *필수*; phenomenon is THE connection criterion). A block with both
        # blank is the untouched template (MSY) — skip, never fabricate an aim.
        # (task alone can survive the italic test via its 'paradigm …; 자극 …;
        # 시행구조' scaffolding words, so it is not a sufficient signal.)
        if _is_blank(domain) and _is_blank(phenomenon):
            continue
        for col in ("domain", "phenomenon", "task"):
            val = {"domain": domain, "phenomenon": phenomenon, "task": task}[col]
            if _is_blank(val):
                needs.append({"researcher": init, "aim_id": aim_id, "section": "aims",
                              "field": col, "reason": "anchor field blank",
                              "raw": raw_fields.get(col, "")})

        aims.append({
            "researcher_id": init, "aim_id": aim_id, "aim_label": aim_label,
            "hyp_type": hyp_type, "domain": domain, "phenomenon": phenomenon,
            "task": task, "mechanism": mechanism,
            "population": fields.get("population", ""),
            "metric": fields.get("metric", ""),
            "condition": fields.get("condition", ""),
            "direction": direction,
            "hypothesis": fields.get("hypothesis", ""),
            "background": fields.get("background", ""),
            "seed_paper": fields.get("seed_paper", ""),
            "measures": measures,
            "confidence": aim_conf,
            "raw_jsonb": {"fields": raw_fields, "summary_row": s.get("_raw"),
                          "field_confidence": field_conf, "head": htext,
                          "project_ordinal": ordn},
        })
    return aims


# H — negatives -----------------------------------------------------------
# Bracketed placeholder tokens that only appear in the survey's TEMPLATE
# example rows ('내 [domain]의 [phenomenon]', '[domain′]', '[X]') — never in a
# real filled contrast (those carry concrete domain/phenomenon names).
_NEG_TEMPLATE_BRACKET_RE = re.compile(
    r"\[\s*(?:내\s+)?(?:domain|phenomenon|x)['′]?\s*\]", re.IGNORECASE)


def _is_template_neg(cells: list[str]) -> bool:
    """True ONLY for the survey's literal example/template rows — NOT for a real
    anti-example a researcher filled in. JYK e.g. KEEPS the '(있다면)' prompt and
    appends an actual mis-recommended title + a concrete reason; that must be
    kept. So: skip the '예: …' worked example, any row whose REASON is a
    bracketed placeholder ('내 [domain]의 [phenomenon]'), and an unfilled
    '(있다면) …' prompt whose reason is blank/too-short."""
    topic = (cells[0] if cells else "").strip()
    reason = _clean_value(cells[-1] if len(cells) > 1 else "")
    if topic.startswith("예"):
        return True
    if _NEG_TEMPLATE_BRACKET_RE.search(reason):
        return True
    if "(있다면)" in topic and len(reason) < 12:
        return True
    return False


_PROMPT_PREFIX_RE = re.compile(r"^\s*\(있다면\)\s*(?:실제로\s*추천됐는데\s*아니었던\s*논문\s*제목)?\s*")


def parse_negatives(neg_rows: list[list[str]], needs: list[dict],
                    init: str) -> list[dict]:
    out: list[dict] = []
    nid = 0
    for row in neg_rows[1:] if neg_rows else []:
        cells = [c.strip() for c in row]
        topic = _clean_value(cells[0]) if cells else ""
        ntype = _clean_value(cells[1]) if len(cells) > 1 else ""
        reason = _clean_value(cells[2]) if len(cells) > 2 else ""
        if _is_blank(topic) and _is_blank(reason):
            continue
        if _is_template_neg(cells):
            continue
        # strip a retained '(있다면) 실제로 추천됐는데 …' prompt prefix so the stored
        # topic is just the researcher's actual mis-recommended title (JYK).
        topic = _PROMPT_PREFIX_RE.sub("", topic).strip()
        # A negative without a concrete contrast cannot veto anything safely
        # (the survey itself bans abstract reasons) — flag it and SKIP, so a
        # left-as-template row (e.g. the '(있다면) … 논문 제목' anti-example with a
        # blank reason) never becomes a live veto.
        if _is_blank(reason) or len(reason) < 12:
            needs.append({"researcher": init, "section": "negatives",
                          "field": "contrast_reason",
                          "reason": "exclude row missing concrete contrast — skipped",
                          "raw": " | ".join(cells)})
            continue
        nid += 1
        conf = _confidence(" ".join(cells))
        out.append({
            "researcher_id": init, "neg_id": nid, "excl_topic": topic,
            "neg_type": ntype or None, "contrast_reason": reason,
            "confidence": conf,
            "raw_jsonb": {"cells": cells},
        })
    return out


# G — keywords ------------------------------------------------------------

def parse_keywords(g1_text: str, g2_rows: list[list[str]], init: str) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    # G1 list: "키워드 목록 (...) [자동]: a · b · c"
    _, listpart = _split_label_value(g1_text)
    listpart = _clean_value(listpart)
    for kw in re.split(r"[·,\n]", listpart):
        k = kw.strip()
        if not k or k.lower() in seen:
            continue
        seen.add(k.lower())
        out.append({"researcher_id": init, "keyword": k, "is_ambiguous": False,
                    "operational_def": None, "conflict_term": None,
                    "bound_aim": None, "raw_jsonb": {"src": "G1"}})
    # G2 ambiguous defs
    for row in g2_rows[1:] if g2_rows else []:
        cells = [c.strip() for c in row]
        term = _clean_value(cells[0]) if cells else ""
        if _is_blank(term) or term.startswith("예"):
            continue
        odef = _clean_value(cells[1]) if len(cells) > 1 else ""
        conflict = _clean_value(cells[2]) if len(cells) > 2 else ""
        key = term.lower()
        existing = next((r for r in out if r["keyword"].lower() == key), None)
        if existing:
            existing.update({"is_ambiguous": True,
                             "operational_def": odef or None,
                             "conflict_term": conflict or None})
            existing["raw_jsonb"] = {"src": "G1+G2", "cells": cells}
        else:
            seen.add(key)
            out.append({"researcher_id": init, "keyword": term, "is_ambiguous": True,
                        "operational_def": odef or None,
                        "conflict_term": conflict or None, "bound_aim": None,
                        "raw_jsonb": {"src": "G2", "cells": cells}})
    return out


# F — methods -------------------------------------------------------------
_METHOD_MODALITY: list[tuple[str, str]] = [
    ("행동", "behavior"),
    ("시선", "eye"),
    ("신경", "neural"),
    ("인공신경망", "ann"),
]


def parse_methods(method_blocks: list[dict], init: str) -> tuple[list[dict], str]:
    out: list[dict] = []
    other = ""
    cur_modality: Optional[str] = None
    for b in method_blocks:
        t = _btype(b)
        txt = _block_text(b)
        if t == "paragraph":
            stripped = txt.strip()
            if stripped.startswith("기타"):
                cur_modality = "_other"
                _, v = _split_label_value(stripped)
                other = _clean_value(v)
                continue
            mod = next((m for needle, m in _METHOD_MODALITY
                        if stripped.startswith(needle)), None)
            if mod:
                cur_modality = mod
                continue
            cur_modality = None
            continue
        if t == "callout" and cur_modality == "_other":
            other = _clean_value(txt)
            continue
        if t == "to_do" and cur_modality and cur_modality != "_other":
            if not _todo_checked(b):
                continue
            label = _clean_value(txt)
            approach = label.split("(")[0].strip().rstrip("/").strip()
            if not approach:
                continue
            out.append({"researcher_id": init, "modality": cur_modality,
                        "approach": approach, "raw_label": label})
    return out, other


# E — models --------------------------------------------------------------
_USAGE_TOKENS = ["적용", "검증", "확장", "반론"]
_STANCE_OPTIONS = ["한다", "안한다", "읽기만", "미정"]
_WANTS_OPTIONS = ["적극", "가끔", "거의"]


def parse_models(model_blocks: list[dict],
                 fetch_children: Optional[Callable[[str], list]],
                 init: str) -> tuple[list[dict], str, str]:
    models: list[dict] = []
    stance = ""
    wants = ""
    mode: Optional[str] = None  # 'stance' | 'wants'
    for b in model_blocks:
        t = _btype(b)
        txt = _block_text(b)
        if t == "paragraph":
            if "직접 하시나요" in txt:
                mode = "stance"
            elif "추천을 원하시나요" in txt:
                mode = "wants"
            else:
                mode = None
            continue
        if t == "to_do":
            if not _todo_checked(b):
                continue
            opt = _clean_value(txt)
            if mode == "stance":
                stance = next((o for o in _STANCE_OPTIONS if o in opt), stance)
            elif mode == "wants":
                wants = next((o for o in _WANTS_OPTIONS if o in opt), wants)
            continue
        if t == "table":
            for row in _table_rows(b, fetch_children)[1:]:
                cells = [c.strip() for c in row]
                name = _clean_value(cells[0]) if cells else ""
                if _is_blank(name):
                    continue
                applied = _clean_value(cells[1]) if len(cells) > 1 else ""
                usage_cell = cells[2] if len(cells) > 2 else ""
                usage = [u for u in _USAGE_TOKENS if u in usage_cell]
                models.append({
                    "researcher_id": init, "model_name": name,
                    "applied_aim": applied or None,
                    "usage_modes": usage,
                    "confidence": _confidence(" ".join(cells)),
                    "raw_jsonb": {"cells": cells},
                })
    stance_norm = {"한다": "한다", "안한다": "안한다", "읽기만": "읽기만",
                   "미정": "미정"}.get(stance, stance)
    wants_norm = {"적극": "적극", "가끔": "가끔", "거의": "거의불필요"}.get(wants, wants)
    return models, stance_norm, wants_norm


# D — PIs -----------------------------------------------------------------

_TRAIL_PAREN_RE = re.compile(r"\s*[\(（][^()（）]*[\)）]\s*$")


def _pi_name_affil(raw_name: str) -> tuple[str, Optional[str], bool]:
    """Split a PI cell into (name, affiliation, is_placeholder). The affiliation
    is the trailing '(…)' parenthetical; a write-in placeholder affiliation
    ('소속 직접 작성') is dropped to None but does NOT disqualify the PI — the NAME
    before it is real (SYJ: 'Saito (소속 직접 작성)' → name='Saito'). A cell whose
    name-minus-parenthetical is empty ('(직접 작성)', '(5–10명)', '(직접 작성 — 더
    추가)') IS a placeholder."""
    n = (raw_name or "").strip()
    core = _TRAIL_PAREN_RE.sub("", n).strip()
    if not core or core.startswith("(5") or core.strip("()[]·—-、, ") == "":
        return n, None, True
    affil = None
    am = re.search(r"[\(（]([^()（）]+)[\)）]\s*$", n)
    if am:
        a = am.group(1).strip()
        affil = None if ("직접 작성" in a or "미상" in a) else a
    return core, affil, False


def parse_pis(pi_rows: list[list[str]], neg_pi_text: str,
              needs: list[dict], init: str) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for row in pi_rows[1:] if pi_rows else []:
        cells = [c.strip() for c in row]
        raw_name = _clean_value(cells[0]) if cells else ""
        name, affil, is_ph = _pi_name_affil(raw_name)
        if is_ph:
            continue
        conn = _clean_value(cells[1]) if len(cells) > 1 else ""
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"researcher_id": init, "pi_name": name, "affiliation": affil,
                    "connection": conn or None, "polarity": "+",
                    "raw_jsonb": {"cells": cells}})
    # Negative-PI free text. If it is an operator 【확인필요 …】 note (= unconfirmed
    # "환영/제외 여부 직접 확정"), DON'T fabricate a hard veto — flag needs_review.
    raw_neg = (neg_pi_text or "").strip()
    if FLAG_CHECK[:4] in raw_neg or "확인필요" in raw_neg:
        needs.append({"researcher": init, "section": "pis", "field": "negative_pi",
                      "reason": "negative-PI is an unconfirmed 【확인필요】 note "
                                "(환영/제외 여부 미확정) — not ingested as a veto",
                      "raw": raw_neg})
        return out
    neg = _clean_value(raw_neg)
    if neg and neg not in ("", _PLACEHOLDER):
        for nm in re.split(r"[·,\n;]", neg):
            n = nm.strip()
            nm_name, nm_affil, nm_ph = _pi_name_affil(n)
            if not n or nm_name.lower() in seen or nm_ph:
                continue
            seen.add(nm_name.lower())
            out.append({"researcher_id": init, "pi_name": nm_name, "affiliation": nm_affil,
                        "connection": None, "polarity": "-",
                        "raw_jsonb": {"src": "negative-PI"}})
    return out


# A/C/H-preamble — profile ------------------------------------------------
_PROFILE_FIELD_MATCHERS: list[tuple[str, str]] = [
    ("이름", "name"),
    ("직책", "role"),
    ("소속", "lab"),
    ("active 프로젝트", "n_projects"),
    ("주 연구", "summary"),
]


def parse_profile(profile_blocks: list[dict], infra_blocks: list[dict],
                  neg_blocks: list[dict], stance: str, wants: str,
                  other_methods: str, needs: list[dict], init: str) -> dict:
    prof: dict[str, Any] = {"researcher_id": init}
    raw: dict[str, str] = {}
    for b in profile_blocks:
        if _btype(b) != "to_do":
            continue
        label, value = _split_label_value(_block_text(b))
        for needle, col in _PROFILE_FIELD_MATCHERS:
            if label.startswith(needle) or needle in label[:18]:
                v = _clean_value(value)
                raw[col] = _block_text(b)
                if col == "n_projects":
                    nm = re.search(r"\d+", v)
                    prof[col] = int(nm.group(0)) if nm else None
                else:
                    prof[col] = v or None
                break
    # §C infra: paragraph "보유...: value" or a callout under 실험 장비·환경
    infra = ""
    for b in infra_blocks:
        t = _btype(b)
        txt = _block_text(b)
        if t == "paragraph" and txt.strip().startswith("보유하거나 사용") \
                and ("：" in txt or ":" in txt):
            _, v = _split_label_value(txt)
            infra = _clean_value(v)
        elif t == "callout":
            cv = _clean_value(txt)
            if cv:
                infra = cv
    prof["infra"] = infra or None
    # §H preamble research_scope: paragraph "내 연구 범위 한 단락: value"
    scope = ""
    for b in neg_blocks:
        txt = _block_text(b)
        if txt.strip().startswith("내 연구 범위"):
            _, v = _split_label_value(txt)
            scope = _clean_value(v)
            break
    prof["research_scope"] = scope or None
    prof["modeling_stance"] = stance or None
    prof["wants_modeling_recs"] = wants or None
    prof["other_methods"] = other_methods or None
    prof["raw_jsonb"] = raw
    if not prof.get("summary"):
        needs.append({"researcher": init, "section": "profile", "field": "summary",
                      "reason": "주 연구 한 문장 ★필수 is blank",
                      "raw": raw.get("summary", "")})
    return prof


# ===========================================================================
# Assemble
# ===========================================================================

def _first_table(blocks: list[dict], fetch: Optional[Callable[[str], list]],
                 want_header_needle: Optional[str] = None) -> list[list[str]]:
    """Return the rows of the first table in `blocks` (optionally the first
    whose header contains a needle)."""
    for b in blocks:
        if _btype(b) != "table":
            continue
        rows = _table_rows(b, fetch)
        if want_header_needle is None:
            return rows
        if rows and any(want_header_needle in (c or "") for c in rows[0]):
            return rows
    return []


def ingest_one(init: str, blocks: list[dict],
               fetch: Optional[Callable[[str], list]] = None) -> dict:
    """Parse one researcher's block tree → structured survey memory."""
    sections = sectionize(blocks)
    needs: list[dict] = []

    projects, summary_body = split_project_blocks(sections.get("aims", []))
    summary_rows = _first_table(summary_body, fetch)
    aims = parse_aims(projects, summary_rows, needs, init)

    neg_blocks = sections.get("negatives", [])
    neg_rows = _first_table(neg_blocks, fetch, "왜 아닌가") or _first_table(neg_blocks, fetch)
    negatives = parse_negatives(neg_rows, needs, init)

    kw_blocks = sections.get("keywords", [])
    g1_text = ""
    for b in kw_blocks:
        if _btype(b) == "paragraph" and _block_text(b).strip().startswith("키워드 목록"):
            g1_text = _block_text(b)
            break
    g2_rows = _first_table(kw_blocks, fetch)
    keywords = parse_keywords(g1_text, g2_rows, init)

    methods, other_methods = parse_methods(sections.get("methods", []), init)
    models, stance, wants = parse_models(sections.get("models", []), fetch, init)

    pi_blocks = sections.get("pis", [])
    pi_rows = _first_table(pi_blocks, fetch)
    neg_pi = ""
    for b in pi_blocks:
        if _btype(b) == "callout":
            neg_pi = _block_text(b)
    pis = parse_pis(pi_rows, neg_pi, needs, init)

    profile = parse_profile(sections.get("profile", []), sections.get("infra", []),
                            neg_blocks, stance, wants, other_methods, needs, init)

    return {
        "researcher_id": init, "source_version": SURVEY_VERSION,
        "profile": profile, "aims": aims, "negatives": negatives,
        "keywords": keywords, "methods": methods, "models": models, "pis": pis,
        "needs_review": needs,
    }


# ===========================================================================
# Validation report
# ===========================================================================

def validate(mem: dict) -> list[str]:
    """Cheap sanity rails beyond the needs_review trace."""
    issues: list[str] = []
    init = mem["researcher_id"]
    if not mem["profile"].get("summary"):
        issues.append(f"{init}: profile.summary blank (주 연구 한 문장 ★필수)")
    if not mem["aims"]:
        issues.append(f"{init}: NO aims parsed (blank survey or anchor all-blank)")
    for a in mem["aims"]:
        miss = [k for k in ("domain", "phenomenon", "task") if _is_blank(a.get(k, ""))]
        if miss:
            issues.append(f"{init}/{a['aim_id']}: anchor missing {miss}")
    return issues


# ===========================================================================
# DB upsert (operator --apply)
# ===========================================================================

def _upsert(mem_list: list[dict]) -> None:
    sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
    from _db import load_env, ledger_schema, _conn  # noqa: E402
    load_env()
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        raise SystemExit("ingest_survey --apply requires psycopg2-binary.")
    sch = ledger_schema()
    sv = SURVEY_VERSION
    conn = _conn()
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            for mem in mem_list:
                rid = mem["researcher_id"]
                p = mem["profile"]
                cur.execute(f"""
                    INSERT INTO {sch}.archive_survey_profile
                      (researcher_id,name,role,lab,n_projects,summary,infra,
                       research_scope,modeling_stance,wants_modeling_recs,
                       other_methods,source_version,raw_jsonb)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (researcher_id) DO UPDATE SET
                      name=EXCLUDED.name, role=EXCLUDED.role, lab=EXCLUDED.lab,
                      n_projects=EXCLUDED.n_projects, summary=EXCLUDED.summary,
                      infra=EXCLUDED.infra, research_scope=EXCLUDED.research_scope,
                      modeling_stance=EXCLUDED.modeling_stance,
                      wants_modeling_recs=EXCLUDED.wants_modeling_recs,
                      other_methods=EXCLUDED.other_methods,
                      source_version=EXCLUDED.source_version,
                      ingested_at=now(), raw_jsonb=EXCLUDED.raw_jsonb;
                """, (rid, p.get("name"), p.get("role"), p.get("lab"),
                      p.get("n_projects"), p.get("summary"), p.get("infra"),
                      p.get("research_scope"), p.get("modeling_stance"),
                      p.get("wants_modeling_recs"), p.get("other_methods"), sv,
                      json.dumps(p.get("raw_jsonb") or {}, ensure_ascii=False)))

                # replace child rows for this researcher (clean idempotent set)
                for tbl in ("archive_survey_aims", "archive_survey_negatives",
                            "archive_survey_keywords", "archive_survey_methods",
                            "archive_survey_models", "archive_survey_pis"):
                    cur.execute(f"DELETE FROM {sch}.{tbl} WHERE researcher_id=%s", (rid,))

                for a in mem["aims"]:
                    cur.execute(f"""
                        INSERT INTO {sch}.archive_survey_aims
                          (researcher_id,aim_id,aim_label,hyp_type,domain,phenomenon,
                           task,mechanism,population,metric,condition,direction,
                           hypothesis,background,seed_paper,measures,confidence,
                           source_version,raw_jsonb)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                %s::jsonb,%s,%s,%s::jsonb)
                    """, (rid, a["aim_id"], a.get("aim_label"), a.get("hyp_type"),
                          a.get("domain"), a.get("phenomenon"), a.get("task"),
                          a.get("mechanism"), a.get("population"), a.get("metric"),
                          a.get("condition"), a.get("direction"), a.get("hypothesis"),
                          a.get("background"), a.get("seed_paper"),
                          json.dumps(a.get("measures") or {}, ensure_ascii=False),
                          a.get("confidence"), sv,
                          json.dumps(a.get("raw_jsonb") or {}, ensure_ascii=False)))
                for nrow in mem["negatives"]:
                    cur.execute(f"""
                        INSERT INTO {sch}.archive_survey_negatives
                          (researcher_id,neg_id,excl_topic,neg_type,contrast_reason,
                           confidence,source_version,raw_jsonb)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    """, (rid, nrow["neg_id"], nrow.get("excl_topic"),
                          nrow.get("neg_type"), nrow.get("contrast_reason"),
                          nrow.get("confidence"), sv,
                          json.dumps(nrow.get("raw_jsonb") or {}, ensure_ascii=False)))
                for k in mem["keywords"]:
                    cur.execute(f"""
                        INSERT INTO {sch}.archive_survey_keywords
                          (researcher_id,keyword,is_ambiguous,operational_def,
                           conflict_term,bound_aim,source_version,raw_jsonb)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    """, (rid, k["keyword"], k.get("is_ambiguous", False),
                          k.get("operational_def"), k.get("conflict_term"),
                          k.get("bound_aim"), sv,
                          json.dumps(k.get("raw_jsonb") or {}, ensure_ascii=False)))
                for me in mem["methods"]:
                    cur.execute(f"""
                        INSERT INTO {sch}.archive_survey_methods
                          (researcher_id,modality,approach,raw_label,source_version)
                        VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT (researcher_id,modality,approach) DO NOTHING
                    """, (rid, me["modality"], me["approach"], me.get("raw_label"), sv))
                for mo in mem["models"]:
                    cur.execute(f"""
                        INSERT INTO {sch}.archive_survey_models
                          (researcher_id,model_name,applied_aim,usage_modes,
                           confidence,source_version,raw_jsonb)
                        VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb)
                        ON CONFLICT (researcher_id,model_name) DO UPDATE SET
                          applied_aim=EXCLUDED.applied_aim,
                          usage_modes=EXCLUDED.usage_modes,
                          confidence=EXCLUDED.confidence, ingested_at=now(),
                          raw_jsonb=EXCLUDED.raw_jsonb
                    """, (rid, mo["model_name"], mo.get("applied_aim"),
                          json.dumps(mo.get("usage_modes") or [], ensure_ascii=False),
                          mo.get("confidence"), sv,
                          json.dumps(mo.get("raw_jsonb") or {}, ensure_ascii=False)))
                for pi in mem["pis"]:
                    cur.execute(f"""
                        INSERT INTO {sch}.archive_survey_pis
                          (researcher_id,pi_name,affiliation,connection,polarity,
                           source_version,raw_jsonb)
                        VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)
                        ON CONFLICT (researcher_id,pi_name) DO UPDATE SET
                          affiliation=EXCLUDED.affiliation,
                          connection=EXCLUDED.connection, polarity=EXCLUDED.polarity,
                          ingested_at=now(), raw_jsonb=EXCLUDED.raw_jsonb
                    """, (rid, pi["pi_name"], pi.get("affiliation"),
                          pi.get("connection"), pi.get("polarity"), sv,
                          json.dumps(pi.get("raw_jsonb") or {}, ensure_ascii=False)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ===========================================================================
# CLI
# ===========================================================================

def _print_summary(mem: dict) -> None:
    init = mem["researcher_id"]
    p = mem["profile"]
    print(f"\n=== {init} ({mem['source_version']}) ===")
    print(f"  profile: name={p.get('name')!r} role={p.get('role')!r} "
          f"n_projects={p.get('n_projects')} stance={p.get('modeling_stance')!r} "
          f"wants={p.get('wants_modeling_recs')!r}")
    print(f"    summary: {(p.get('summary') or '')[:110]!r}")
    print(f"    infra:   {(p.get('infra') or '')[:80]!r}")
    print(f"  aims: {len(mem['aims'])}")
    for a in mem["aims"]:
        print(f"    {a['aim_id']} [{a['hyp_type']}] {a.get('aim_label') or ''} "
              f"(conf={a['confidence']})")
        print(f"       domain={a['domain'][:42]!r} phenom={a['phenomenon'][:48]!r}")
        print(f"       task={a['task'][:42]!r}")
        print(f"       mech={a['mechanism'][:60]!r}")
    print(f"  negatives: {len(mem['negatives'])}  "
          f"({', '.join((n['excl_topic'] or '')[:18] for n in mem['negatives'][:4])}…)")
    print(f"  keywords: {len(mem['keywords'])} "
          f"({sum(1 for k in mem['keywords'] if k['is_ambiguous'])} ambiguous w/ def)")
    print(f"  methods: {len(mem['methods'])}  "
          f"({', '.join(sorted(set(m['modality'] for m in mem['methods'])))})")
    print(f"  models: {len(mem['models'])}  pis: {len(mem['pis'])} "
          f"({sum(1 for x in mem['pis'] if x['polarity']=='-')} negative)")
    if mem["needs_review"]:
        print(f"  ⚠ needs_review: {len(mem['needs_review'])}")
        for n in mem["needs_review"][:6]:
            print(f"      - {n.get('section')}/{n.get('field')}: {n.get('reason')}")
    issues = validate(mem)
    for i in issues:
        print(f"  ‼ {i}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=("prefill", "fixture", "notion"),
                    default="prefill")
    ap.add_argument("--only", metavar="INIT", default=None)
    ap.add_argument("--fixture", metavar="PATH", default=None)
    ap.add_argument("--apply", action="store_true",
                    help="UPSERT into csnl_paper_rec.archive_survey_* (operator).")
    ap.add_argument("--json", action="store_true",
                    help="Dump the full structured memory as JSON.")
    ap.add_argument("--emit-needs-review", metavar="PATH", default=None)
    args = ap.parse_args()

    if args.source == "notion":
        print("[ingest] --source notion is GATED: only run after the operator "
              "confirms the surveys are complete (researchers are still "
              "editing). Proceeding read-only.", file=sys.stderr)

    targets = [args.only.strip().upper()] if args.only else list(RESEARCHERS)
    all_mem: list[dict] = []
    all_needs: list[dict] = []
    for init in targets:
        fetch = None
        if args.source == "prefill":
            blocks = load_blocks_prefill(init)
        elif args.source == "fixture":
            if not args.fixture:
                print("--source fixture requires --fixture PATH", file=sys.stderr)
                return 2
            blocks = load_blocks_fixture(Path(args.fixture))
        else:  # notion
            blocks, fetch = load_blocks_notion(init)
        mem = ingest_one(init, blocks, fetch)
        all_mem.append(mem)
        all_needs.extend(mem["needs_review"])
        if args.json:
            print(json.dumps(mem, ensure_ascii=False, indent=2))
        else:
            _print_summary(mem)

    if args.emit_needs_review and all_needs:
        outp = Path(args.emit_needs_review)
        outp.parent.mkdir(parents=True, exist_ok=True)
        with outp.open("w", encoding="utf-8") as f:
            for n in all_needs:
                f.write(json.dumps(n, ensure_ascii=False) + "\n")
        print(f"\n[ingest] wrote {len(all_needs)} needs-review items → {outp}")

    print(f"\n[ingest] parsed {len(all_mem)} researcher(s); "
          f"{sum(len(m['aims']) for m in all_mem)} aims, "
          f"{sum(len(m['negatives']) for m in all_mem)} negatives, "
          f"{sum(len(m['keywords']) for m in all_mem)} keywords, "
          f"{len(all_needs)} needs-review.")

    if args.apply:
        if args.source == "prefill":
            print("\n[ingest] REFUSING --apply with --source prefill: the "
                  "pre-fill is the pre-edit baseline, not researcher truth. "
                  "Use --source notion after surveys are complete.",
                  file=sys.stderr)
            return 2
        _upsert(all_mem)
        print(f"\n[ingest] UPSERT complete: {len(all_mem)} researcher(s).")
    else:
        print("[ingest] dry-run only. Re-run with --apply (operator, "
              "--source notion) to write archive_survey_*.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
