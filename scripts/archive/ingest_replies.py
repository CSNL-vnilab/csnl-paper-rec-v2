#!/usr/bin/env python3
"""
scripts/archive/ingest_replies.py — ingest researcher REPLIES to the 26
"researcher-only" survey questions into the P28 structured Postgres memory
(archive_survey_*), concretely / idempotently / reversibly / with supersede.

WHAT IT DOES (design: state/archive/_tmp/pages_final/p30_blueprint.md §3)
  A reply arrives on one of three channels (a Notion page edit, a free-text
  email, or an operator-pasted manual JSON fixture). The harness:
    1. loads the machine registry (question_registry.json) — per question_id:
       target_table / dispatch_op / answer_shape / value_extraction /
       precondition / depends_on.
    2. reads the channel into a flat list of Answer rows.
    3. map_answer(): DETERMINISTIC extraction per answer_shape →
       proposed_update {table, op, items:[{pk,set}]}. single/multi/list/checkbox
       are deterministic (high/medium). free / structurally-ambiguous answers
       are NOT structured here — they are recorded needs_review for an
       operator-attended Opus pass (NO LLM call lives in this script).
    4. emits proposal/gate/relevance/ops JSONL artifacts.
    5. only under an explicit operator gate (--stage / --apply / --revert) does
       it touch the DB, ROW-AT-A-TIME (never the survey-ingest DELETE-all set).

BOUNDARY (non-negotiable — p30_blueprint §3-1)
  * PURE DRY-RUN (the default) writes the JSONL artifacts ONLY. ZERO DB writes
    of any kind (not even a ledger 'proposed' row). It also WORKS OFFLINE: it
    tries a read-only SELECT to diff the current DB value, but if the DB is
    unavailable (or --offline) it just skips the diff (prior_snapshot=null) and
    builds the proposal anyway, after printing an 'offline dry-run' warning.
  * --stage (operator) inserts ledger rows status='staged'. --apply (operator)
    writes the body rows + ledger status='applied' + supersede chain, one
    transaction per researcher with an in-transaction prior_snapshot re-SELECT
    (TOCTOU-safe). --revert (operator) restores from prior_snapshot.
  * Notion is a READ-ONLY reply channel — this script NEVER writes Notion.
  * csnl_research, archive_responses and the filled survey pages are read-only
    truth; nothing here writes them. The ledger schema is validated via
    pipeline/_db.ledger_schema() (refuses csnl_research).
  * NEVER reuses ingest_survey._upsert (it DELETEs ALL child rows for a
    researcher then re-inserts, clobbering other applied replies). Every body
    write here is a row-level upsert / delete keyed on the table PK (or the
    normalized excl_topic for negatives).

CLI
  # offline dry-run smoke (manual fixture; zero DB):
  python3 scripts/archive/ingest_replies.py --source manual \
      --fixture state/archive/_tmp/reply_samples/BHL.json --only BHL --offline
  # dry-run with live DB diff (read-only):
  python3 scripts/archive/ingest_replies.py --source notion --only BHL
  python3 scripts/archive/ingest_replies.py --source email \
      --email-file state/archive/_tmp/inbox/BHL.eml --only BHL
  ! python3 scripts/archive/ingest_replies.py --source manual \
      --fixture .../BHL.json --stage           # operator: ledger staging
  ! python3 scripts/archive/ingest_replies.py --source manual \
      --fixture .../BHL.json --apply            # operator: body + ledger + supersede
  ! python3 scripts/archive/ingest_replies.py --revert 42        # operator: undo
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "weekly"))
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))

import ingest_survey as ing          # noqa: E402  (reuse _confidence / _clean_value)

RESEARCHERS = ["BHL", "BYL", "JOP", "JYK", "MSY", "SMJ", "SYJ"]
_TMP_DIR = _REPO_ROOT / "state" / "archive" / "_tmp"
_DEFAULT_REGISTRY = _TMP_DIR / "question_registry.json"

# Live page IDs for --source notion (read-only retrieve). Same map as
# ingest_survey.NOTION_PAGE_IDS — the survey pages double as the reply channel.
NOTION_PAGE_IDS: dict[str, str] = {
    "BHL": "3762a38e-4f5f-8155-baff-d7282261ab29",
    "BYL": "3762a38e-4f5f-8163-9594-e9ffb7755431",
    "JOP": "3762a38e-4f5f-81d5-a4c6-c7b454a8a04c",
    "JYK": "3762a38e-4f5f-8180-9000-e402f1542518",
    "MSY": "3762a38e-4f5f-81c3-afdc-f18adadb88a3",
    "SMJ": "3762a38e-4f5f-8192-9dc8-f2ee905adcec",
    "SYJ": "3762a38e-4f5f-8175-99a4-fe5cd61596fe",
}

# The 7 P28 base tables (read_tier) + P30 ledger (write_tier).
_BASE_TABLES = ("archive_survey_profile", "archive_survey_aims",
                "archive_survey_negatives", "archive_survey_keywords",
                "archive_survey_methods", "archive_survey_models",
                "archive_survey_pis")
_LEDGER_TABLE = "archive_survey_answers"
_BASELINE_SV = "survey-v13"   # registry.baseline_source_version (ingest_survey L67)

# ----------------------------------------------------------------- text markers
# Polarity of a PI mention. '+' (주목) is the default; a "자주 추천되지만 무관심"
# / "거리가 있다" framing flips it to '-' (deprioritise; composite ×0.7).
_NEG_PI_MARKERS = ["관심과는 거리", "관심과 거리", "거리가 있", "거리가멀", "거리가 멀",
                   "관심 없", "관심이 없", "관심 밖", "무관심", "제외", "빼주",
                   "빼고", "자주 추천되", "negative", "관심 밖이", "흥미 없"]
# keyword_delete keep / delete clause classifiers (BHL-Q8).
_KEEP_MARKERS = ["남겨", "유지", "keep", "제외하지", "빼지", "그대로",
                 "쓰는 용어", "실제로 쓰", "필요해", "남기"]
_DEL_MARKERS = ["삭제", "제거", "지워", "지우", "없애", "빼도", "drop", "remove", "빼주세요"]
_DEL_ALL_MARKERS = ["나머지", "모두", "전부", "다 삭제", "all"]
# negative exclude_mode (BYL-Q7 / JYK-Q2).
_ALWAYS_MARKERS = ["완전제외", "완전 제외", "항상", "무조건", "전부 제외", "절대 제외",
                   "아예 제외", "완전히 제외", "전면 제외"]
_COMP_MARKERS = ["비교", "조건부", "허용", "비교포함", "비교 포함", "맥락",
                 "참고용", "대조용", "연결되면", "연결 시"]
# restructure (BYL-Q1/Q3, JOP-Q4).
_KEEP_STRUCT = ["유지", "현행", "그대로", "두 갈래", "분리 유지", "따로", "각각", "구분"]
_CHANGE_STRUCT = ["통합", "병합", "합치", "하나로", "중복", "합쳐", "merge", "integrate"]
# "there are none / not applicable" — a PI question answered in the negative
# (BYL-Q8 '없음 -> no-op'); must NOT be fabricated into a PI row.
_NONE_MARKERS = ["없습니다", "없음", "없다", "없어요", "해당 없", "해당없", "특별히 없",
                 "딱히 없", "none", "n/a", "not applicable"]


# ===========================================================================
# DB access — read-only probes + parameterized reads. Pure dry-run uses these
# best-effort and tolerates the DB being absent; writes go through psycopg2.
# ===========================================================================

_DB_PROBED = False
_DB_OK = False
_HAVE_PG2 = False
_READ_CONN = None  # cached read-only psycopg2 connection for dry-run diffs


def _have_psycopg2() -> bool:
    try:
        import psycopg2  # noqa: F401
        return True
    except ImportError:
        return False


def db_probe(force_offline: bool = False) -> bool:
    """Probe DB reachability ONCE. Returns True iff a trivial read works.
    --offline (or any failure: missing .env creds, unreachable host) → False,
    which the dry-run path tolerates. No write is ever attempted here."""
    global _DB_PROBED, _DB_OK, _HAVE_PG2
    if _DB_PROBED:
        return _DB_OK
    _DB_PROBED = True
    _HAVE_PG2 = _have_psycopg2()
    if force_offline:
        _DB_OK = False
        return False
    try:
        from _db import load_env, query_json  # noqa: E402
        load_env()
        query_json("SELECT 1 AS ok")
        _DB_OK = True
    except Exception as e:  # noqa: BLE001 — any failure = treat as offline
        print(f"[reply] DB unavailable ({type(e).__name__}: {str(e)[:120]}) — "
              f"offline dry-run.", file=sys.stderr)
        _DB_OK = False
    return _DB_OK


def _schema() -> str:
    """Validated read-write ledger schema (never csnl_research)."""
    from _db import load_env, ledger_schema  # noqa: E402
    load_env()
    return ledger_schema()


def _read_conn():
    """Lazy, cached read-only psycopg2 connection for dry-run prior diffs.
    Parameterized reads only — never interpolates researcher free-text."""
    global _READ_CONN
    if _READ_CONN is None:
        from _db import _conn  # noqa: E402
        _READ_CONN = _conn()
        _READ_CONN.autocommit = True
    return _READ_CONN


def _close_read_conn() -> None:
    global _READ_CONN
    if _READ_CONN is not None:
        try:
            _READ_CONN.close()
        except Exception:
            pass
        _READ_CONN = None


def _cursor_one(cur, sql: str, params: tuple) -> Optional[dict]:
    """Run a parameterized SELECT on an open cursor; first row as dict or None."""
    cur.execute(sql, params)
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return {c: row[i] for i, c in enumerate(cols)}


def _read_one(sql: str, params: tuple) -> Optional[dict]:
    """Best-effort read-only parameterized SELECT for dry-run diffs. Returns a
    row dict, or None (absent OR DB/psycopg2 unavailable). NEVER writes."""
    if not (_DB_OK and _HAVE_PG2):
        return None
    try:
        with _read_conn().cursor() as cur:
            return _cursor_one(cur, sql, params)
    except Exception as e:  # noqa: BLE001
        print(f"[reply] WARN dry-run diff read failed: {type(e).__name__}",
              file=sys.stderr)
        return None


def _safe_query_json(sql: str) -> Optional[list]:
    """query_json (psql/psycopg2 read) for trusted SQL (no untrusted params) —
    information_schema / fixed-literal predicates only. None on failure."""
    if not _DB_OK:
        return None
    try:
        from _db import query_json  # noqa: E402
        return query_json(sql)
    except Exception as e:  # noqa: BLE001
        print(f"[reply] WARN read failed: {type(e).__name__}", file=sys.stderr)
        return None


# ===========================================================================
# Registry
# ===========================================================================

def load_registry(path: Path) -> dict:
    """Load question_registry.json and index questions by question_id. Returns
    the full registry dict with an added 'by_id' map."""
    reg = json.loads(path.read_text("utf-8"))
    by_id = {q["question_id"]: q for q in reg.get("questions", [])}
    reg["by_id"] = by_id
    return reg


# ===========================================================================
# Answer rows + channel readers
# ===========================================================================

@dataclass
class Answer:
    researcher_id: str
    question_id: str
    channel: str
    raw_answer: str
    raw_ref: dict = field(default_factory=dict)


def read_manual(path: Path, targets: set[str]) -> list[Answer]:
    """Manual channel — fully implemented. The fixture is one object (or a list
    of them) shaped:
        {"researcher_id": "BHL", "channel": "manual", "batch": "r1",
         "answers": [{"question_id": "BHL-Q7", "raw_answer": "...",
                      "raw_ref": {...}}, ...]}
    raw_answer is the verbatim reply text; the harness does the extraction."""
    data = json.loads(path.read_text("utf-8"))
    blobs = data if isinstance(data, list) else [data]
    out: list[Answer] = []
    for blob in blobs:
        rid = (blob.get("researcher_id") or "").strip().upper()
        if not rid or (targets and rid not in targets):
            continue
        ch = blob.get("channel") or "manual"
        for a in blob.get("answers") or []:
            qid = (a.get("question_id") or "").strip()
            if not qid:
                continue
            out.append(Answer(rid, qid, ch, a.get("raw_answer") or "",
                              a.get("raw_ref") or {}))
    return out


def read_email(path: Path, targets: set[str], reg: dict) -> list[Answer]:
    """Email channel — basic free-text parser. Reads a .eml, takes the
    text/plain body, and splits it on per-question labels: either a literal
    'BHL-Q7:' / 'Q7:' marker or the registry match_anchor.email_label_regex.
    The researcher is inferred from the question_ids found (or --only)."""
    import email  # local import keeps --help cheap
    from email import policy
    msg = email.message_from_bytes(path.read_bytes(), policy=policy.default)
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body += part.get_content()
    else:
        body = msg.get_content()

    # Candidate questions: those of the targeted researchers (or all).
    cand = [q for q in reg.get("questions", [])
            if not targets or q["researcher"] in targets]
    # Build (qid, rid, start_regex) markers.
    markers: list[tuple[str, str, re.Pattern]] = []
    for q in cand:
        qid = q["question_id"]
        anc = (q.get("match_anchor") or {}).get("email_label_regex") or ""
        pat = rf"(?:{re.escape(qid)}|{anc})" if anc else re.escape(qid)
        markers.append((qid, q["researcher"], re.compile(pat, re.IGNORECASE)))

    lines = body.splitlines()
    captured: dict[str, list[str]] = {}
    cur_qid: Optional[str] = None
    for ln in lines:
        hit = None
        for qid, _rid, rx in markers:
            if rx.search(ln) and re.search(r"[:：]", ln):
                hit = qid
                break
        if hit:
            cur_qid = hit
            _, _, val = ln.partition(":")
            if not val:
                _, _, val = ln.partition("：")
            captured.setdefault(cur_qid, [])
            if val.strip():
                captured[cur_qid].append(val.strip())
            continue
        if cur_qid is not None:
            if ln.strip():
                captured[cur_qid].append(ln.rstrip())
            elif captured[cur_qid]:
                cur_qid = None  # blank line ends a block
    msgid = (msg.get("Message-ID") or "").strip()
    out: list[Answer] = []
    qmap = {q["question_id"]: q for q in cand}
    for qid, txt_lines in captured.items():
        rid = qmap[qid]["researcher"]
        out.append(Answer(rid, qid, "email", "\n".join(txt_lines).strip(),
                          {"email_msgid": msgid, "source": str(path)}))
    return out


def read_notion(targets: set[str], reg: dict) -> list[Answer]:
    """Notion channel — READ-ONLY. Retrieves each targeted researcher's own
    page and walks the block tree for §4 answer areas, associating each answer
    callout/to_do with the nearest preceding question marker (the registry
    question text or the question_id). This module NEVER writes Notion."""
    import _notion  # noqa: E402
    out: list[Answer] = []
    for rid in sorted(targets or set(NOTION_PAGE_IDS)):
        page_id = NOTION_PAGE_IDS.get(rid)
        if not page_id:
            print(f"[reply] no Notion page id for {rid} — skipped",
                  file=sys.stderr)
            continue
        try:
            _ = _notion.retrieve_page(page_id)          # read-only GET
            blocks = _notion.list_block_children(page_id)  # read-only GET
        except Exception as e:  # noqa: BLE001
            print(f"[reply] WARN Notion read for {rid} failed: "
                  f"{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
            continue
        out.extend(_extract_notion_answers(rid, page_id, blocks, reg))
    return out


def _extract_notion_answers(rid: str, page_id: str, blocks: list[dict],
                            reg: dict) -> list[Answer]:
    """Best-effort: scan the page's blocks; when a block's text contains a
    question marker for this researcher, capture the following answer block's
    text as the raw_answer. Defensive — returns whatever it can resolve."""
    qs = [q for q in reg.get("questions", []) if q["researcher"] == rid]
    out: list[Answer] = []
    cur_q: Optional[dict] = None
    for b in blocks:
        txt = _notion_block_text(b)
        marker = None
        for q in qs:
            if q["question_id"] in txt:
                marker = q
                break
        if marker is not None:
            cur_q = marker
            continue
        if cur_q is not None and b.get("type") in ("callout", "paragraph", "to_do"):
            val = txt.strip()
            if val:
                out.append(Answer(rid, cur_q["question_id"], "notion", val,
                                  {"page_id": page_id, "block_id": b.get("id")}))
                cur_q = None
    return out


def _notion_block_text(b: dict) -> str:
    t = b.get("type") or ""
    obj = b.get(t)
    if isinstance(obj, dict) and isinstance(obj.get("rich_text"), list):
        return "".join(s.get("plain_text", "")
                       or (s.get("text") or {}).get("content", "")
                       for s in obj["rich_text"])
    return ""


# ===========================================================================
# Extraction result + small text helpers
# ===========================================================================

@dataclass
class Extract:
    """Outcome of deterministic map_answer extraction for one Answer."""
    target_table: Optional[str]
    target_op: str
    items: list[dict] = field(default_factory=list)   # [{"pk":{}, "set":{}, "mode":..}]
    confidence: Optional[str] = None
    needs_review: bool = False
    review_reason: str = ""
    noop: bool = False
    note: str = ""
    snapshot_kind: str = "items"                       # items|restructure|none
    relevance: list[dict] = field(default_factory=list)
    ops: list[dict] = field(default_factory=list)


def _tidy(s: str) -> str:
    """Light free-text clean: collapse whitespace, trim. Keeps interior
    punctuation (unlike ingest_survey._clean_value, which strips aggressively)."""
    return re.sub(r"\s{2,}", " ", (s or "").replace(" ", " ")).strip()


def _aim_id_for(entry: dict) -> str:
    """Resolve the target aim_id from the precondition / question text
    ('P3 natural-scene', '프로젝트2(SD)'), defaulting to P1."""
    for src in (entry.get("precondition") or "", entry.get("question") or ""):
        m = re.search(r"\bP([1-9])\b", src)
        if m:
            return f"P{m.group(1)}"
        m2 = re.search(r"프로젝트\s*([1-9])", src)
        if m2:
            return f"P{m2.group(1)}"
    return "P1"


def _parse_pi_lines(raw: str) -> list[dict]:
    """Parse a PI-list reply into [{pi_name, affiliation, connection, polarity}].
    Name = text before the first '(' / em-dash / colon; affiliation = the first
    parenthetical (unless a write-in placeholder); polarity from neg markers."""
    out: list[dict] = []
    for line in raw.splitlines():
        ln = line.strip().lstrip("-*•·0123456789.)• ").strip()
        if not ln or any(mk in ln for mk in _NONE_MARKERS):
            continue                       # blank or an explicit 'none' line
        has_paren = bool(re.search(r"[(（][^)）]+[)）]", ln))
        has_delim = bool(re.search(r"[—–]|\s-\s|[:：]", ln))
        affil = None
        am = re.search(r"[(（]([^)）]+)[)）]", ln)
        if am:
            a = am.group(1).strip()
            if "직접 작성" not in a and "미상" not in a and "소속" not in a:
                affil = a
        no_par = re.sub(r"[(（][^)）]*[)）]", "", ln).strip()
        parts = re.split(r"\s*[—–]\s*|\s+-\s+|\s*[:：]\s*", no_par, maxsplit=1)
        name = parts[0].strip(" ,·;")
        rationale = parts[1].strip() if len(parts) > 1 else ""
        # A name must look like a name: a delimiter/paren anchors it, or (with no
        # delimiter) it is a short token run — never a full sentence of prose.
        if not name or not re.search(r"[A-Za-z가-힣]", name):
            continue
        if not (has_paren or has_delim):
            if len(name.split()) > 4 or re.search(r"(다|요|죠|함|음)\.?$", name):
                continue                   # prose, not a name
        pol = "-" if any(mk in ln for mk in _NEG_PI_MARKERS) else "+"
        out.append({"pi_name": name, "affiliation": affil,
                    "connection": rationale or None, "polarity": pol})
    return out


# ===========================================================================
# Per-dispatch deterministic extractors
# ===========================================================================

def _raw_jsonb(entry: dict, channel: str, batch: str) -> dict:
    return {"src": "reply", "question_id": entry["question_id"],
            "channel": channel, "batch": batch}


def _x_pi_upsert(entry: dict, ans: Answer, ctx: "Ctx") -> Extract:
    """list → one pi row per parsed line (multi-row upsert handled by items[]).
    JOP-Q3 also detects per-PAPER holds → relevance_decision_reject artifact."""
    pis = _parse_pi_lines(ans.raw_answer)
    rj = _raw_jsonb(entry, ans.channel, ctx.batch)
    items = [{"pk": {"researcher_id": ans.researcher_id, "pi_name": p["pi_name"]},
              "set": {"affiliation": p["affiliation"], "connection": p["connection"],
                      "polarity": p["polarity"], "raw_jsonb": rj}}
             for p in pis]
    if not items:
        if any(mk in ans.raw_answer for mk in _NONE_MARKERS):
            return Extract("archive_survey_pis", "pi_upsert", [], confidence="high",
                           noop=True, note="answered 'none' — no PI to upsert")
        return Extract("archive_survey_pis", "pi_upsert", [], needs_review=True,
                       review_reason="no parseable PI line")
    ex = Extract("archive_survey_pis", "pi_upsert", items, confidence="high")
    # subop: relevance_decision_reject (JOP-Q3) — paper-level (not author) holds.
    if "relevance_decision_reject" in _subops(entry):
        for line in ans.raw_answer.splitlines():
            if any(w in line for w in ("보류", "hold", "제외 논문")) and \
               re.search(r"[\"“”'].+[\"“”']|논문|paper|et al", line):
                title = _tidy(re.sub(r".*?[:：]", "", line))
                ex.relevance.append({
                    "researcher_id": ans.researcher_id,
                    "question_id": entry["question_id"],
                    "canonical_id": None,                 # operator resolves title->cid
                    "title_hint": title[:200],
                    "relevance_type": "none", "veto_hit": None,
                    "gate_engine": "p28-connection",
                    "note": "operator resolve title->canonical_id"})
    return ex


def _x_pi_skip(entry: dict, ans: Answer, ctx: "Ctx") -> Extract:
    """SYJ-Q7 inactive branch: skip the insert + prune stale rows (operator)."""
    return Extract(None, "operator_action", [], confidence=None, noop=True,
                   snapshot_kind="none",
                   note="SYJ-Q2 INACTIVE → project-2 moot: skip PI insert; "
                        "prune any stale Donner/Serences rows (operator)",
                   ops=[{"researcher_id": ans.researcher_id,
                         "question_id": entry["question_id"],
                         "action": "pi_prune_stale", "raw_answer": ans.raw_answer,
                         "raw_ref": ans.raw_ref}])


def _x_keyword_delete(entry: dict, ans: Answer, ctx: "Ctx") -> Extract:
    """multi → delete the tf-idf junk tokens the researcher did NOT keep. The
    candidate junk set comes from the registry question's '(a/b/c)' list. The
    real fingerprint-side prune is an operator_action subop (FIX 6)."""
    deletes = _keyword_delete_targets(entry, ans.raw_answer)
    items = [{"pk": {"researcher_id": ans.researcher_id, "keyword": t}, "set": {}}
             for t in deletes]
    ex = Extract("archive_survey_keywords", "keyword_delete", items,
                 confidence="high" if items else None,
                 noop=not items,
                 note="DELETE no-op if token absent (preflight); "
                      "fingerprint-side prune is the real lever (operator)."
                      if items else "no junk token marked for deletion")
    if "operator_action:fingerprint_prune" in _subops(entry) and deletes:
        ex.ops.append({"researcher_id": ans.researcher_id,
                       "question_id": entry["question_id"],
                       "action": "fingerprint_prune", "tokens": deletes,
                       "target": "state/archive/fingerprints/"
                                 f"{ans.researcher_id}.json",
                       "raw_answer": ans.raw_answer, "raw_ref": ans.raw_ref})
    return ex


def _keyword_delete_targets(entry: dict, raw: str) -> list[str]:
    m = re.search(r"[(（]([^)）]*?/[^)）]*?)[)）]", entry.get("question", ""))
    candidates = [t.strip().lower()
                  for t in re.split(r"[/,·]", m.group(1)) if t.strip()] if m else []
    if not candidates:
        return []
    keep: set[str] = set()
    delete: set[str] = set()
    rest_delete = False
    for cl in re.split(r"[.\n。!?]", raw):
        low = cl.lower()
        toks = [c for c in candidates if c in low]
        has_keep = any(k in cl for k in _KEEP_MARKERS)
        has_del = any(k in cl for k in _DEL_MARKERS)
        if has_keep:
            keep.update(toks)
        elif has_del:
            delete.update(toks)
            if any(a in cl or a in low for a in _DEL_ALL_MARKERS):
                rest_delete = True
    if rest_delete:
        delete = set(candidates) - keep
    return [t for t in candidates if t in delete and t not in keep]


def _x_negative_upsert(entry: dict, ans: Answer, ctx: "Ctx") -> Extract:
    """single → exclude policy. excl_topic(s) come from the question; the answer
    picks exclude_mode (always vs comparison-allowed). Idempotent by normalized
    excl_topic (the dispatch CTE allocates neg_id only for a genuinely new one)."""
    mode = _exclude_mode(ans.raw_answer)
    if mode is None:
        return Extract("archive_survey_negatives", "negative_upsert", [],
                       needs_review=True,
                       review_reason="could not classify always vs comparison-allowed")
    exclude_mode, neg_type = mode
    topics = _negative_topics(entry)
    rj = _raw_jsonb(entry, ans.channel, ctx.batch)
    items = [{"pk": {"researcher_id": ans.researcher_id, "excl_topic": t},
              "set": {"excl_topic": t, "neg_type": neg_type,
                      "exclude_mode": exclude_mode,
                      "contrast_reason": _tidy(ans.raw_answer)[:600],
                      "confidence": "high", "raw_jsonb": rj}}
             for t in topics]
    note = ("exclude_mode is INERT until P28b veto wiring"
            if exclude_mode != "always" else "")
    return Extract("archive_survey_negatives", "negative_upsert", items,
                   confidence="high", note=note)


def _exclude_mode(raw: str) -> Optional[tuple[str, str]]:
    comp = any(m in raw for m in _COMP_MARKERS)
    alw = any(m in raw for m in _ALWAYS_MARKERS)
    if comp:
        return ("comparison-allowed", "adjacent")
    if alw:
        return ("always", "research-focus")
    return None


def _negative_topics(entry: dict) -> list[str]:
    q = entry.get("question", "")
    m = re.search(r"[(（]([^)）]+)[)）]", q)
    if m and any(sep in m.group(1) for sep in ("·", "/", ",")):
        return [t.strip() for t in re.split(r"[·/,]", m.group(1)) if t.strip()]
    for sw in ("완전제외", "제외", "정책", " vs ", "vs"):
        idx = q.find(sw)
        if idx > 0:
            cand = _tidy(q[:idx])
            if cand:
                return [cand]
    return [_tidy(q)] if q else []


def _x_model_upsert(entry: dict, ans: Answer, ctx: "Ctx") -> Extract:
    """single/free → INERT models row. Deterministic only when the operator
    supplies a model_name in raw_ref (avoids spawning a spurious inert row from
    fuzzy free prose); otherwise needs_review. recommend.py never reads models."""
    model_name = (ans.raw_ref or {}).get("model_name")
    if not model_name:
        return Extract("archive_survey_models", "model_upsert", [],
                       needs_review=True,
                       review_reason="model_name not structured (free prose); "
                                     "INERT — supply raw_ref.model_name to apply")
    usage = [u for u in ("적용", "검증", "확장", "반론") if u in ans.raw_answer]
    if "대안" in ans.raw_answer:
        usage = sorted(set(usage) | {"반론", "확장"})
    rj = _raw_jsonb(entry, ans.channel, ctx.batch)
    item = {"pk": {"researcher_id": ans.researcher_id, "model_name": model_name},
            "set": {"applied_aim": (ans.raw_ref or {}).get("applied_aim"),
                    "usage_modes": usage, "confidence": "medium", "raw_jsonb": rj}}
    return Extract("archive_survey_models", "model_upsert", [item],
                   confidence="medium", note="INERT — no recommendation effect")


# aim single-choice handlers keyed by question_id (deterministic field updates).
def _aim_syj_q3(entry, ans, ctx):
    raw = ans.raw_answer
    enc = any(m in raw for m in ("encoding", "부호화", "감각", "지각 단계", "초기 단계"))
    dec = any(m in raw for m in ("decision", "결정", "판단", "의사결정", "후기 단계"))
    if enc == dec:  # neither or both → ambiguous
        return None
    mech = ("encoding-stage bias (지각/부호화 단계)" if enc
            else "decision-stage bias (판단/결정 단계)")
    return {"mechanism": mech}, "high"


def _aim_jyk_q5(entry, ans, ctx):
    raw = ans.raw_answer
    orient_only = any(m in raw for m in ("한정", "국한", "orientation만", "방위만",
                                         "orientation 한정"))
    include = any(m in raw for m in ("포함", "타특징", "다른 특징", "하향", "우선순위"))
    if orient_only and not include:
        return {"domain": "orientation (자극 도메인 한정)"}, "high"
    if include and not orient_only:
        # No quantitative down-weight dial (domain_priority is bonus-only) — noop.
        return {}, "noop"
    return None


def _aim_smj_q2(entry, ans, ctx):
    """SMJ-Q2: P3 mechanism = efficient coding (확정) / blank (미정). P3 row MAY
    BE ABSENT → an admit-affecting update needs an anchor; if P3 is absent and
    no anchor is in the answer, this is needs_review (FIX 5: a bare mechanism
    UPDATE on an absent aim_id is a silent no-op)."""
    raw = ans.raw_answer
    confirmed = any(m in raw for m in ("확정", "맞", "efficient coding", "효율 부호화",
                                       "효율부호화", "그렇"))
    undecided = any(m in raw for m in ("미정", "아직", "확실하지", "모르"))
    if undecided and not confirmed:
        return {"mechanism": None, "confidence": "low"}, "low", True
    if not confirmed:
        return None
    aim_id = _aim_id_for(entry)
    present = _read_one(
        f"SELECT aim_id FROM {ctx.sch}.archive_survey_aims "
        f"WHERE researcher_id=%s AND aim_id=%s",
        (ans.researcher_id, aim_id)) if ctx.db_ok else None
    set_ = {"mechanism": "efficient coding (자연 장면 통계의 효율 부호화)",
            "confidence": "high"}
    # present known → field-only update; absent/unknown → anchor required.
    return set_, "high", (present is not None)


_AIM_SINGLE = {"SYJ-Q3": _aim_syj_q3, "JYK-Q5": _aim_jyk_q5}


def _x_aim_upsert(entry: dict, ans: Answer, ctx: "Ctx") -> Extract:
    qid = entry["question_id"]
    aim_id = _aim_id_for(entry)
    rj = _raw_jsonb(entry, ans.channel, ctx.batch)
    # 1) SMJ-Q2 — anchor-aware (FIX 5)
    if qid == "SMJ-Q2":
        res = _aim_smj_q2(entry, ans, ctx)
        if res is None:
            return Extract("archive_survey_aims", "aim_upsert", [],
                           needs_review=True,
                           review_reason="P3 mechanism unconfirmed / free prose")
        set_, conf, present = res
        if not present:
            return Extract("archive_survey_aims", "aim_upsert", [],
                           needs_review=True,
                           review_reason="P3 aim absent/unknown — anchor "
                                         "(domain/phenomenon/task) required before "
                                         "a mechanism update (FIX 5)")
        set_["raw_jsonb"] = rj
        return Extract("archive_survey_aims", "aim_upsert",
                       [{"pk": {"researcher_id": ans.researcher_id, "aim_id": aim_id},
                         "set": set_, "mode": "update"}], confidence=conf)
    # 2) single-choice field updates (SYJ-Q3, JYK-Q5)
    if qid in _AIM_SINGLE:
        res = _AIM_SINGLE[qid](entry, ans, ctx)
        if res is None:
            return Extract("archive_survey_aims", "aim_upsert", [],
                           needs_review=True,
                           review_reason="single-choice not classifiable")
        set_, conf = res
        if conf == "noop" or not set_:
            return Extract("archive_survey_aims", "aim_upsert", [], confidence="high",
                           noop=True, note="no recommendation lever changes "
                                           "(domain-priority is bonus-only)")
        set_["raw_jsonb"] = rj
        return Extract("archive_survey_aims", "aim_upsert",
                       [{"pk": {"researcher_id": ans.researcher_id, "aim_id": aim_id},
                         "set": set_, "mode": "update"}], confidence=conf)
    # 3) free single inert field (metric / seed_paper / hypothesis / condition /
    #    background) → store verbatim, field-only UPDATE (no-op if aim absent).
    cols = entry.get("target_columns") or []
    inert_single = [c for c in cols if c in ("metric", "seed_paper", "hypothesis",
                                             "condition", "background")]
    if entry.get("answer_shape") in ("free", "list") and len(cols) == 1 and inert_single:
        col = inert_single[0]
        val = _tidy(ans.raw_answer)[:600]
        if not val:
            return Extract("archive_survey_aims", "aim_upsert", [], noop=True,
                           note="blank answer")
        return Extract("archive_survey_aims", "aim_upsert",
                       [{"pk": {"researcher_id": ans.researcher_id, "aim_id": aim_id},
                         "set": {col: val, "raw_jsonb": rj}, "mode": "update"}],
                       confidence="medium", note=f"INERT field ({col})")
    # 4) anything else (new-aim anchor from free prose: JYK-Q9) → needs_review
    return Extract("archive_survey_aims", "aim_upsert", [], needs_review=True,
                   review_reason="free anchor (new aim) needs structuring "
                                 "(domain/phenomenon/task/mechanism)")


def _x_aim_delete(entry: dict, ans: Answer, ctx: "Ctx",
                  aim_id: Optional[str] = None) -> Extract:
    aid = aim_id or _aim_id_for(entry)
    return Extract("archive_survey_aims", "aim_delete",
                   [{"pk": {"researcher_id": ans.researcher_id, "aim_id": aid}, "set": {}}],
                   confidence="high",
                   note="full-row prior_snapshot captured for reversibility")


def _x_restructure(entry: dict, ans: Answer, ctx: "Ctx") -> Extract:
    decision = _restructure_decision(ans.raw_answer)
    if decision == "keep":
        return Extract("archive_survey_aims", "restructure", [], confidence="high",
                       noop=True, snapshot_kind="restructure",
                       note="keep current shape — no restructure needed")
    if decision == "change":
        return Extract("archive_survey_aims", "restructure", [], confidence="medium",
                       snapshot_kind="restructure",
                       note="restructure → operator replay: re-edit survey, "
                            "full re-ingest, then replay other applied patches (FIX 7)",
                       ops=[{"researcher_id": ans.researcher_id,
                             "question_id": entry["question_id"],
                             "action": "aim_restructure", "raw_answer": ans.raw_answer,
                             "raw_ref": ans.raw_ref}])
    return Extract("archive_survey_aims", "restructure", [], needs_review=True,
                   snapshot_kind="restructure",
                   review_reason="split/merge intent ambiguous vs current shape")


def _restructure_decision(raw: str) -> str:
    if any(m in raw for m in _CHANGE_STRUCT):
        return "change"
    if any(m in raw for m in _KEEP_STRUCT) and "분리" not in raw:
        return "keep"
    return "ambiguous"


def _x_operator_action(entry: dict, ans: Answer, ctx: "Ctx") -> Extract:
    return Extract(None, "operator_action", [], confidence=None, snapshot_kind="none",
                   note="out-of-recommender — ledger only (no archive_survey_* write)",
                   ops=[{"researcher_id": ans.researcher_id,
                         "question_id": entry["question_id"],
                         "action": "operator_route", "raw_answer": ans.raw_answer,
                         "raw_ref": ans.raw_ref}])


def _subops(entry: dict) -> set[str]:
    out: set[str] = set()
    for so in entry.get("subops") or []:
        if isinstance(so, str):
            out.add(so)
        elif isinstance(so, dict):
            op = so.get("dispatch_op", "")
            blob = json.dumps(so, ensure_ascii=False)
            if op == "operator_action" and "fingerprint" in blob:
                out.add("operator_action:fingerprint_prune")
            elif op:
                out.add(op)
    return out


# ===========================================================================
# depends_on / precondition resolution
# ===========================================================================

def _syj_q2_inactive(reg: dict, ctx: "Ctx") -> Optional[bool]:
    """Evaluate preconditions.SYJ-Q2.machine_check. Returns True (inactive) /
    False (active) / None (cannot evaluate offline)."""
    if not ctx.db_ok:
        return None
    rows = _safe_query_json(
        f"SELECT NOT EXISTS (SELECT 1 FROM {ctx.sch}.archive_survey_aims "
        f"WHERE researcher_id='SYJ' AND (aim_id='P2' OR "
        f"phenomenon ILIKE '%serial dependence%') AND confidence <> 'low') AS inactive")
    if not rows:
        return None
    return bool(rows[0].get("inactive"))


def _resolve_dispatch(entry: dict, ans: Answer, reg: dict, ctx: "Ctx"
                      ) -> tuple[str, Optional[Extract]]:
    """Resolve a conditional dispatch_op via depends_on. Returns (op, override)
    where override is a ready Extract (moot drop) or None to run the extractor."""
    op = entry["dispatch_op"]
    if "SYJ-Q2" in (entry.get("depends_on") or []):
        inactive = _syj_q2_inactive(reg, ctx)
        if inactive is None:
            return op, Extract(entry.get("target_table"), op, [], needs_review=True,
                               review_reason="depends_on SYJ-Q2 unresolved "
                                             "(offline / DB unavailable)")
        if inactive:
            if op == "aim_upsert_or_delete":
                # project-2 inactive → aim_delete (moot) of the stale P2 aim
                return "aim_delete", _x_aim_delete(entry, ans, ctx, aim_id="P2")
            if op == "pi_upsert_or_skip":
                return "operator_action", _x_pi_skip(entry, ans, ctx)
        else:
            if op == "aim_upsert_or_delete":
                return "aim_upsert", None
            if op == "pi_upsert_or_skip":
                return "pi_upsert", None
    # normalize the two conditional names to their active form
    if op == "aim_upsert_or_delete":
        return "aim_upsert", None
    if op == "pi_upsert_or_skip":
        return "pi_upsert", None
    return op, None


_EXTRACTORS: dict[str, Callable[[dict, Answer, "Ctx"], Extract]] = {
    "pi_upsert": _x_pi_upsert,
    "keyword_delete": _x_keyword_delete,
    "negative_upsert": _x_negative_upsert,
    "model_upsert": _x_model_upsert,
    "aim_upsert": _x_aim_upsert,
    "aim_delete": _x_aim_delete,
    "restructure": _x_restructure,
    "operator_action": _x_operator_action,
}


# ===========================================================================
# map_answer → proposal
# ===========================================================================

@dataclass
class Ctx:
    sch: str
    batch: str
    source_version: str
    db_ok: bool


def map_answer(ans: Answer, reg: dict, ctx: Ctx) -> Optional[dict]:
    """Deterministically map one Answer to a proposal dict (p30_blueprint §3-5).
    free / ambiguous answers become status='needs_review' (no LLM here)."""
    entry = reg["by_id"].get(ans.question_id)
    if entry is None:
        print(f"[reply] WARN unknown question_id {ans.question_id} — skipped",
              file=sys.stderr)
        return None
    op, override = _resolve_dispatch(entry, ans, reg, ctx)
    if override is not None:
        ex = override
    else:
        extractor = _EXTRACTORS.get(op)
        if extractor is None:
            ex = Extract(entry.get("target_table"), op, [], needs_review=True,
                         review_reason=f"no extractor for dispatch_op {op}")
        else:
            ex = extractor(entry, ans, ctx)
    return _build_proposal(ans, entry, ex, ctx)


def _build_proposal(ans: Answer, entry: dict, ex: Extract, ctx: Ctx) -> dict:
    content_hash = hashlib.sha1((ans.raw_answer or "").encode("utf-8")).hexdigest()
    if ex.items or ex.snapshot_kind == "restructure":
        proposed_update: Optional[dict] = {
            "table": ex.target_table, "op": ex.target_op, "items": ex.items}
    else:
        proposed_update = None
    status = ("needs_review" if ex.needs_review
              else "noop" if ex.noop else "proposed")
    prior = _capture_prior_dryrun(ans, entry, ex, ctx) if ctx.db_ok else None
    prop = {
        "researcher_id": ans.researcher_id,
        "question_id": ans.question_id,
        "channel": ans.channel,
        "raw_answer": ans.raw_answer,
        "raw_ref": ans.raw_ref,
        "target_table": ex.target_table,
        "target_op": ex.target_op,
        "proposed_update": proposed_update,
        "prior_snapshot_dryrun": prior,
        "confidence": ex.confidence,
        "status": status,
        "content_hash": content_hash,
        "source_version": ctx.source_version,
        "supersede_key": {"researcher_id": ans.researcher_id,
                          "question_id": ans.question_id},
    }
    if ex.note:
        prop["note"] = ex.note
    if ex.review_reason:
        prop["review_reason"] = ex.review_reason
    # carry side-artifacts (consumed by emit())
    prop["_relevance"] = ex.relevance
    prop["_ops"] = ex.ops
    prop["_snapshot_kind"] = ex.snapshot_kind
    return prop


def _capture_prior_dryrun(ans: Answer, entry: dict, ex: Extract, ctx: Ctx
                          ) -> Optional[dict]:
    """ADVISORY only — the binding snapshot is re-SELECTed in the --apply tx.
    Shape: {"items":[{"pk":{...}, "row": <row>|null}]} where row=null means
    confirmed-absent; {} for operator_action; {"restructure": {...}} for a
    restructure. Read-only, parameterized; None when the DB is unavailable."""
    if ex.snapshot_kind == "none":
        return {}
    if ex.snapshot_kind == "restructure":
        snap = _snapshot_rid_dryrun(ans.researcher_id, ctx)
        return {"restructure": snap} if snap is not None else None
    if not ex.items:
        return {}
    out = []
    for it in ex.items:
        row = _select_pk_read(ex.target_op, ex.target_table, it["pk"], ctx)
        out.append({"pk": it["pk"], "row": row})
    return {"items": out}


def _snapshot_rid_dryrun(rid: str, ctx: Ctx) -> Optional[dict]:
    if not (_DB_OK and _HAVE_PG2):
        return None
    snap: dict[str, Any] = {}
    try:
        with _read_conn().cursor() as cur:
            for key, tbl in (("aims", "archive_survey_aims"),
                             ("keywords", "archive_survey_keywords"),
                             ("models", "archive_survey_models"),
                             ("pis", "archive_survey_pis")):
                cur.execute(
                    f"SELECT * FROM {ctx.sch}.{tbl} WHERE researcher_id=%s", (rid,))
                cols = [d[0] for d in cur.description]
                snap[key] = [dict(zip(cols, r)) for r in cur.fetchall()]
        return _jsonable(snap)
    except Exception:
        return None


_PK_SELECT = {
    "pi_upsert": ("archive_survey_pis",
                  "WHERE researcher_id=%s AND pi_name=%s", ("researcher_id", "pi_name")),
    "keyword_delete": ("archive_survey_keywords",
                       "WHERE researcher_id=%s AND keyword=%s",
                       ("researcher_id", "keyword")),
    "model_upsert": ("archive_survey_models",
                     "WHERE researcher_id=%s AND model_name=%s",
                     ("researcher_id", "model_name")),
    "aim_upsert": ("archive_survey_aims",
                   "WHERE researcher_id=%s AND aim_id=%s", ("researcher_id", "aim_id")),
    "aim_delete": ("archive_survey_aims",
                   "WHERE researcher_id=%s AND aim_id=%s", ("researcher_id", "aim_id")),
    "negative_upsert": ("archive_survey_negatives",
                        "WHERE researcher_id=%s AND lower(excl_topic)=lower(%s)",
                        ("researcher_id", "excl_topic")),
}


def _select_pk_read(op: str, table: Optional[str], pk: dict, ctx: Ctx
                    ) -> Optional[dict]:
    spec = _PK_SELECT.get(op)
    if not spec or not table:
        return None
    tbl, where, keys = spec
    params = tuple(pk.get(k) for k in keys)
    row = _read_one(f"SELECT * FROM {ctx.sch}.{tbl} {where}", params)
    return _jsonable(row) if row else None


def _jsonable(obj: Any) -> Any:
    """Make psycopg2 rows JSON-serializable (datetime → iso)."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


# ===========================================================================
# Targeted dispatch — build SQL (NOT executed in dry-run). Row-at-a-time.
# ===========================================================================

_AIM_COLS = {"aim_label", "hyp_type", "domain", "phenomenon", "task", "mechanism",
             "population", "metric", "condition", "direction", "hypothesis",
             "background", "seed_paper", "confidence"}


def _j(v: Any) -> Any:
    """json.dumps dict/list values destined for ::jsonb columns; pass through."""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def _targeted_dispatch(prop: dict, sch: str, sv: str) -> list[tuple[str, tuple]]:
    """Build the row-level (sql, params) statements for a proposal's body write.
    Returns [] for operator_action / restructure / relevance (no body write).
    Pure builder — the caller executes them inside the --apply transaction."""
    op = prop["target_op"]
    pu = prop.get("proposed_update") or {}
    items = pu.get("items") or []
    out: list[tuple[str, tuple]] = []
    if op in ("operator_action", "restructure", "relevance_decision_reject"):
        return out
    for it in items:
        pk, s = it.get("pk", {}), it.get("set", {})
        if op == "pi_upsert":
            out.append((
                f"INSERT INTO {sch}.archive_survey_pis "
                f"(researcher_id,pi_name,affiliation,connection,polarity,"
                f"source_version,raw_jsonb) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb) "
                f"ON CONFLICT (researcher_id,pi_name) DO UPDATE SET "
                f"affiliation=COALESCE(EXCLUDED.affiliation,"
                f"{sch}.archive_survey_pis.affiliation),"
                f"connection=COALESCE(EXCLUDED.connection,"
                f"{sch}.archive_survey_pis.connection),"
                f"polarity=EXCLUDED.polarity,source_version=EXCLUDED.source_version,"
                f"ingested_at=now(),raw_jsonb=EXCLUDED.raw_jsonb",
                (pk["researcher_id"], pk["pi_name"], s.get("affiliation"),
                 s.get("connection"), s.get("polarity") or "+", sv,
                 _j(s.get("raw_jsonb") or {}))))
        elif op == "keyword_delete":
            out.append((
                f"DELETE FROM {sch}.archive_survey_keywords "
                f"WHERE researcher_id=%s AND keyword=%s",
                (pk["researcher_id"], pk["keyword"])))
        elif op == "model_upsert":
            out.append((
                f"INSERT INTO {sch}.archive_survey_models "
                f"(researcher_id,model_name,applied_aim,usage_modes,confidence,"
                f"source_version,raw_jsonb) VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb) "
                f"ON CONFLICT (researcher_id,model_name) DO UPDATE SET "
                f"applied_aim=COALESCE(EXCLUDED.applied_aim,"
                f"{sch}.archive_survey_models.applied_aim),"
                f"usage_modes=EXCLUDED.usage_modes,confidence=EXCLUDED.confidence,"
                f"source_version=EXCLUDED.source_version,ingested_at=now(),"
                f"raw_jsonb=EXCLUDED.raw_jsonb",
                (pk["researcher_id"], pk["model_name"], s.get("applied_aim"),
                 _j(s.get("usage_modes") or []), s.get("confidence"), sv,
                 _j(s.get("raw_jsonb") or {}))))
        elif op == "aim_delete":
            out.append((
                f"DELETE FROM {sch}.archive_survey_aims "
                f"WHERE researcher_id=%s AND aim_id=%s",
                (pk["researcher_id"], pk["aim_id"])))
        elif op == "aim_upsert":
            out.append(_build_aim_stmt(sch, sv, pk, s, it.get("mode", "update")))
        elif op == "negative_upsert":
            out.append(_build_negative_stmt(sch, sv, pk, s))
    return out


def _build_aim_stmt(sch: str, sv: str, pk: dict, s: dict, mode: str
                    ) -> tuple[str, tuple]:
    """anchor-bearing insert (mode='insert') → ON CONFLICT DO UPDATE; field-only
    (mode='update') → bare UPDATE so an ABSENT aim_id is a silent no-op (review
    FIX: never INSERT an anchorless aim from a field-only update)."""
    set_cols = [c for c in s if c in _AIM_COLS]
    if mode == "insert" and any(c in ("domain", "phenomenon", "task") for c in set_cols):
        cols = ["researcher_id", "aim_id"] + set_cols + ["source_version", "raw_jsonb"]
        ph = ",".join("%s" for _ in cols[:-1]) + ",%s::jsonb"
        upd = ",".join(f"{c}=COALESCE(EXCLUDED.{c},{sch}.archive_survey_aims.{c})"
                       for c in set_cols)
        sql = (f"INSERT INTO {sch}.archive_survey_aims ({','.join(cols)}) "
               f"VALUES ({ph}) ON CONFLICT (researcher_id,aim_id) DO UPDATE SET "
               f"{upd},source_version=EXCLUDED.source_version,ingested_at=now(),"
               f"raw_jsonb=EXCLUDED.raw_jsonb")
        params = ([pk["researcher_id"], pk["aim_id"]] +
                  [s.get(c) for c in set_cols] + [sv, _j(s.get("raw_jsonb") or {})])
        return sql, tuple(params)
    # field-only UPDATE (no-op if aim absent)
    assigns = ",".join(f"{c}=%s" for c in set_cols)
    rawj = "raw_jsonb" in s
    sql = (f"UPDATE {sch}.archive_survey_aims SET "
           f"{assigns + ',' if assigns else ''}"
           f"{'raw_jsonb=%s::jsonb,' if rawj else ''}"
           f"source_version=%s,ingested_at=now() "
           f"WHERE researcher_id=%s AND aim_id=%s")
    params = [s.get(c) for c in set_cols]
    if rawj:
        params.append(_j(s.get("raw_jsonb") or {}))
    params += [sv, pk["researcher_id"], pk["aim_id"]]
    return sql, tuple(params)


def _build_negative_stmt(sch: str, sv: str, pk: dict, s: dict) -> tuple[str, tuple]:
    """Idempotent by NORMALIZED excl_topic: UPDATE the existing row, else INSERT
    with neg_id = COALESCE(max,0)+1. One atomic statement (FIX 5)."""
    topic = s.get("excl_topic") or pk.get("excl_topic")
    rid = pk["researcher_id"]
    rawj = _j(s.get("raw_jsonb") or {})
    sql = (
        f"WITH existing AS (SELECT neg_id FROM {sch}.archive_survey_negatives "
        f" WHERE researcher_id=%s AND lower(excl_topic)=lower(%s) "
        f" ORDER BY neg_id LIMIT 1), "
        f"upd AS (UPDATE {sch}.archive_survey_negatives n "
        f" SET neg_type=%s,exclude_mode=%s,contrast_reason=%s,confidence=%s,"
        f"     source_version=%s,raw_jsonb=%s::jsonb,ingested_at=now() "
        f" FROM existing WHERE n.researcher_id=%s AND n.neg_id=existing.neg_id "
        f" RETURNING n.neg_id) "
        f"INSERT INTO {sch}.archive_survey_negatives "
        f"(researcher_id,neg_id,excl_topic,neg_type,exclude_mode,contrast_reason,"
        f" confidence,source_version,raw_jsonb) "
        f"SELECT %s, COALESCE((SELECT MAX(neg_id) FROM {sch}.archive_survey_negatives "
        f"  WHERE researcher_id=%s),0)+1, %s,%s,%s,%s,%s,%s,%s::jsonb "
        f"WHERE NOT EXISTS (SELECT 1 FROM upd)")
    params = (
        rid, topic,                                                # existing
        s.get("neg_type"), s.get("exclude_mode"), s.get("contrast_reason"),
        s.get("confidence"), sv, rawj,                             # upd SET
        rid,                                                       # upd WHERE
        rid, rid,                                                  # INSERT rid + MAX rid
        topic, s.get("neg_type"), s.get("exclude_mode"),
        s.get("contrast_reason"), s.get("confidence"), sv, rawj,   # INSERT values
    )
    return sql, params


# ===========================================================================
# Artifacts
# ===========================================================================

def emit(proposals: list[dict], out_dir: Path) -> dict[str, int]:
    """Write the proposal / gate / relevance / ops JSONL artifacts. The proposal
    JSON is the documented §3-5 shape (internal _-prefixed keys are stripped)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    p_path = out_dir / "reply_proposals.jsonl"
    g_path = out_dir / "reply_gate.jsonl"
    r_path = out_dir / "reply_relevance_decisions.jsonl"
    o_path = out_dir / "reply_ops_actions.jsonl"
    counts = {"proposals": 0, "gate": 0, "relevance": 0, "ops": 0}
    with p_path.open("w", encoding="utf-8") as pf, \
         g_path.open("w", encoding="utf-8") as gf, \
         r_path.open("w", encoding="utf-8") as rf, \
         o_path.open("w", encoding="utf-8") as of:
        for prop in proposals:
            public = {k: v for k, v in prop.items() if not k.startswith("_")}
            pf.write(json.dumps(public, ensure_ascii=False) + "\n")
            counts["proposals"] += 1
            if prop.get("status") == "needs_review":
                gf.write(json.dumps(public, ensure_ascii=False) + "\n")
                counts["gate"] += 1
            for rdec in prop.get("_relevance") or []:
                rf.write(json.dumps(rdec, ensure_ascii=False) + "\n")
                counts["relevance"] += 1
            for opact in prop.get("_ops") or []:
                of.write(json.dumps(opact, ensure_ascii=False) + "\n")
                counts["ops"] += 1
    return counts


# ===========================================================================
# precheck (2-tier)
# ===========================================================================

def _present_tables(sch: str, names: tuple[str, ...]) -> set[str]:
    inlist = ",".join("'" + n + "'" for n in names)
    rows = _safe_query_json(
        f"SELECT table_name FROM information_schema.tables "
        f"WHERE table_schema='{sch}' AND table_name IN ({inlist})") or []
    return {r["table_name"] for r in rows}


def precheck_p28(tier: str, sch: str, dry_run: bool) -> None:
    """read_tier: the 7 base tables (gates DB diffs). write_tier (--stage/
    --apply/--revert): the P30 ledger + negatives.exclude_mode + baseline
    survey-v13. In a pure dry-run, read_tier is enforced ONLY when the DB is
    reachable; offline → warn and proceed (p30_blueprint §3-3)."""
    if not _DB_OK:
        if dry_run:
            print("[reply] offline dry-run — read_tier precheck skipped "
                  "(no DB diff).", file=sys.stderr)
            return
        print("[reply] FATAL: --stage/--apply/--revert need a reachable DB.",
              file=sys.stderr)
        raise SystemExit(2)
    present = _present_tables(sch, _BASE_TABLES)
    missing = [t for t in _BASE_TABLES if t not in present]
    if missing:
        print(f"[reply] FATAL read_tier: missing {missing} in {sch}. Apply P28 "
              f"base first:\n  ! python3 scripts/run_migration.py "
              f"state/migrations/2026-06-08_p28_survey_memory.sql\n"
              f"  ! python3 scripts/archive/ingest_survey.py --source notion --apply",
              file=sys.stderr)
        raise SystemExit(2)
    if tier == "read":
        return
    # write_tier
    if _LEDGER_TABLE not in _present_tables(sch, (_LEDGER_TABLE,)):
        _refuse_p30(sch)
    col = _safe_query_json(
        f"SELECT 1 AS ok FROM information_schema.columns "
        f"WHERE table_schema='{sch}' AND table_name='archive_survey_negatives' "
        f"AND column_name='exclude_mode'")
    if not col:
        _refuse_p30(sch)
    base = _safe_query_json(
        f"SELECT 1 AS ok FROM {sch}.archive_survey_profile "
        f"WHERE source_version LIKE '{_BASELINE_SV}%' LIMIT 1")
    if not base:
        print(f"[reply] FATAL write_tier: no baseline survey rows "
              f"(source_version LIKE '{_BASELINE_SV}%'). Run the one-time survey "
              f"bootstrap:\n  ! python3 scripts/archive/ingest_survey.py "
              f"--source notion --apply", file=sys.stderr)
        raise SystemExit(2)


def _refuse_p30(sch: str) -> None:
    print(f"[reply] FATAL write_tier: P30 ledger objects missing in {sch}. "
          f"Apply the P30 add-on:\n  ! python3 scripts/run_migration.py "
          f"state/migrations/2026-06-12_p30_reply_ingest.sql", file=sys.stderr)
    raise SystemExit(2)


# ===========================================================================
# --stage / --apply / --revert (operator, write paths — psycopg2 required)
# ===========================================================================

def _require_pg2() -> None:
    if not _have_psycopg2():
        print("[reply] write paths (--stage/--apply/--revert) require "
              "psycopg2-binary.", file=sys.stderr)
        raise SystemExit(2)


def _proposals_for_write(proposals: list[dict]) -> list[dict]:
    """Drop needs_review proposals from the write set (they await the operator
    gate) but keep noop/proposed (noop still records an audit row)."""
    return [p for p in proposals if p.get("status") != "needs_review"]


def stage_proposals(proposals: list[dict], sch: str) -> int:
    """Insert ledger rows status='staged' (no body write, no supersede)."""
    _require_pg2()
    from _db import _conn  # noqa: E402
    rows = _proposals_for_write(proposals)
    n = 0
    by_rid: dict[str, list[dict]] = {}
    for p in rows:
        by_rid.setdefault(p["researcher_id"], []).append(p)
    for rid, props in by_rid.items():
        conn = _conn()
        try:
            conn.autocommit = False
            with conn.cursor() as cur:
                for p in props:
                    if _insert_ledger(cur, sch, p, status="staged",
                                      prior=p.get("prior_snapshot_dryrun"),
                                      applied=False) is not None:
                        n += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    print(f"[reply] staged {n} ledger row(s).")
    return n


def apply_proposals(proposals: list[dict], sch: str, allow_resurrect: bool) -> int:
    """One transaction per researcher: re-SELECT prior_snapshot in-tx (TOCTOU),
    run the row-level body dispatch, INSERT ledger status='applied', supersede
    the previous applied answer for the same (researcher,question)."""
    _require_pg2()
    from _db import _conn  # noqa: E402
    rows = _proposals_for_write(proposals)
    by_rid: dict[str, list[dict]] = {}
    for p in rows:
        by_rid.setdefault(p["researcher_id"], []).append(p)
    applied = 0
    for rid, props in by_rid.items():
        conn = _conn()
        try:
            conn.autocommit = False
            with conn.cursor() as cur:
                for p in props:
                    if _apply_one(cur, sch, p, allow_resurrect):
                        applied += 1
            conn.commit()
        except Exception:
            conn.rollback()
            print(f"[reply] {rid}: transaction ROLLED BACK (nothing applied for "
                  f"this researcher).", file=sys.stderr)
            raise
        finally:
            conn.close()
    print(f"[reply] applied {applied} answer(s).")
    return applied


def _apply_one(cur, sch: str, prop: dict, allow_resurrect: bool) -> bool:
    rid, qid = prop["researcher_id"], prop["question_id"]
    chash = prop["content_hash"]
    cur.execute(
        f"SELECT answer_id,status FROM {sch}.{_LEDGER_TABLE} "
        f"WHERE researcher_id=%s AND question_id=%s AND content_hash=%s",
        (rid, qid, chash))
    existing = cur.fetchone()
    if existing is not None:
        ex_id, ex_status = existing[0], existing[1]
        if ex_status == "applied":
            print(f"[reply] {qid}: identical reply already applied "
                  f"(answer_id={ex_id}) — no-op.")
            return False
        if ex_status in ("superseded", "reverted") and not allow_resurrect:
            print(f"[reply] {qid}: same-hash row is '{ex_status}' (answer_id="
                  f"{ex_id}); NOT resurrecting a stale batch. Pass "
                  f"--allow-resurrect to override.", file=sys.stderr)
            return False
        # promote an existing staged/proposed/needs_review/(resurrected) row
        prior = _capture_prior_apply(cur, sch, prop)
        _run_body(cur, sch, prop)
        cur.execute(
            f"UPDATE {sch}.{_LEDGER_TABLE} SET status='applied',applied_at=%s,"
            f"prior_snapshot=%s::jsonb,proposed_update=%s::jsonb,"
            f"source_version=%s WHERE answer_id=%s",
            (datetime.now(timezone.utc), json.dumps(prior, ensure_ascii=False),
             json.dumps(prop.get("proposed_update"), ensure_ascii=False),
             prop["source_version"], ex_id))
        new_id = ex_id
    else:
        prior = _capture_prior_apply(cur, sch, prop)
        _run_body(cur, sch, prop)
        new_id = _insert_ledger(cur, sch, prop, status="applied", prior=prior,
                                applied=True)
        if new_id is None:                       # UNIQUE conflict race → no-op
            print(f"[reply] {qid}: UNIQUE(rid,question_id,content_hash) "
                  f"conflict — no-op.")
            return False
    # supersede the prior applied answer for this (rid,qid)
    cur.execute(
        f"UPDATE {sch}.{_LEDGER_TABLE} SET status='superseded',superseded_by=%s "
        f"WHERE researcher_id=%s AND question_id=%s AND status='applied' "
        f"AND answer_id<>%s", (new_id, rid, qid, new_id))
    print(f"[reply] {qid}: applied (answer_id={new_id}, op={prop['target_op']}).")
    return True


def _run_body(cur, sch: str, prop: dict) -> None:
    for sql, params in _targeted_dispatch(prop, sch, prop["source_version"]):
        cur.execute(sql, params)


def _insert_ledger(cur, sch: str, prop: dict, status: str,
                   prior: Optional[dict], applied: bool) -> Optional[int]:
    cur.execute(
        f"INSERT INTO {sch}.{_LEDGER_TABLE} "
        f"(researcher_id,question_id,channel,raw_answer,raw_ref,proposed_update,"
        f" prior_snapshot,target_table,target_op,status,confidence,content_hash,"
        f" source_version,needs_review,applied_at) "
        f"VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s) "
        f"ON CONFLICT (researcher_id,question_id,content_hash) DO NOTHING "
        f"RETURNING answer_id",
        (prop["researcher_id"], prop["question_id"], prop["channel"],
         prop["raw_answer"], json.dumps(prop.get("raw_ref") or {}, ensure_ascii=False),
         json.dumps(prop.get("proposed_update"), ensure_ascii=False),
         json.dumps(prior, ensure_ascii=False) if prior is not None else None,
         prop["target_table"], prop["target_op"], status, prop.get("confidence"),
         prop["content_hash"], prop["source_version"],
         prop.get("status") == "needs_review",
         datetime.now(timezone.utc) if applied else None))
    row = cur.fetchone()
    return row[0] if row else None


def _capture_prior_apply(cur, sch: str, prop: dict) -> dict:
    """Binding in-transaction prior_snapshot (TOCTOU-safe). Shape mirrors the
    dry-run: {"items":[{"pk":{...},"row":<row>|null}]} (row=null=confirmed-absent)
    | {} (operator_action) | {"restructure": {...}}. If a capture SELECT itself
    fails, returns {"_unread": true} and the caller's tx rolls back — no row is
    ever applied with an uncertain snapshot."""
    kind = prop.get("_snapshot_kind", "items")
    op, table = prop["target_op"], prop.get("target_table")
    if kind == "none" or op == "operator_action":
        return {}
    try:
        if kind == "restructure" or op == "restructure":
            snap = {}
            for key, tbl in (("aims", "archive_survey_aims"),
                             ("keywords", "archive_survey_keywords"),
                             ("models", "archive_survey_models"),
                             ("pis", "archive_survey_pis")):
                cur.execute(f"SELECT * FROM {sch}.{tbl} WHERE researcher_id=%s",
                            (prop["researcher_id"],))
                cols = [d[0] for d in cur.description]
                snap[key] = [_jsonable(dict(zip(cols, r))) for r in cur.fetchall()]
            return {"restructure": snap}
        items = (prop.get("proposed_update") or {}).get("items") or []
        out = []
        for it in items:
            spec = _PK_SELECT.get(op)
            if not spec or not table:
                out.append({"pk": it["pk"], "row": None})
                continue
            tbl, where, keys = spec
            params = tuple(it["pk"].get(k) for k in keys)
            row = _cursor_one(cur, f"SELECT * FROM {sch}.{tbl} {where}", params)
            out.append({"pk": it["pk"], "row": _jsonable(row) if row else None})
        return {"items": out}
    except Exception:
        # surfacing this rolls the whole researcher tx back (reversibility safe)
        return {"_unread": True}


# --------------------------------------------------------------------- revert

def do_revert(answer_id: int, sch: str) -> None:
    """Restore from prior_snapshot. Only status='applied' rows; refuses unknown
    ({"_unread":true}) and restructure snapshots (manual re-ingest). Promotes the
    directly-superseded row back to 'applied' (chain rewind)."""
    _require_pg2()
    from _db import _conn  # noqa: E402
    conn = _conn()
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            row = _cursor_one(
                cur,
                f"SELECT answer_id,researcher_id,question_id,target_table,target_op,"
                f"proposed_update,prior_snapshot,status FROM {sch}.{_LEDGER_TABLE} "
                f"WHERE answer_id=%s", (answer_id,))
            if row is None:
                print(f"[reply] revert: answer_id {answer_id} not found.",
                      file=sys.stderr)
                raise SystemExit(2)
            if row["status"] != "applied":
                print(f"[reply] revert REFUSED: answer_id {answer_id} status="
                      f"'{row['status']}' (only 'applied' is revertible).",
                      file=sys.stderr)
                raise SystemExit(2)
            prior = row["prior_snapshot"] or {}
            if isinstance(prior, str):
                prior = json.loads(prior)
            if prior.get("_unread"):
                print(f"[reply] revert REFUSED: prior_snapshot is unknown "
                      f"(_unread) — cannot safely restore.", file=sys.stderr)
                raise SystemExit(2)
            if "restructure" in prior:
                print(f"[reply] revert REFUSED: restructure snapshot — restore by "
                      f"re-ingesting the survey baseline, not row revert.",
                      file=sys.stderr)
                raise SystemExit(2)
            _restore_items(cur, sch, row["target_op"], row.get("target_table"), prior)
            cur.execute(
                f"UPDATE {sch}.{_LEDGER_TABLE} SET status='reverted' "
                f"WHERE answer_id=%s", (answer_id,))
            # chain rewind: promote the directly-superseded row back to applied
            cur.execute(
                f"UPDATE {sch}.{_LEDGER_TABLE} SET status='applied',"
                f"superseded_by=NULL WHERE superseded_by=%s AND status='superseded'",
                (answer_id,))
        conn.commit()
        print(f"[reply] reverted answer_id {answer_id} "
              f"({row['question_id']}); chain rewound.")
    except SystemExit:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_RESTORE_PK = {
    "pi_upsert": ("archive_survey_pis", ("researcher_id", "pi_name")),
    "keyword_delete": ("archive_survey_keywords", ("researcher_id", "keyword")),
    "model_upsert": ("archive_survey_models", ("researcher_id", "model_name")),
    "aim_upsert": ("archive_survey_aims", ("researcher_id", "aim_id")),
    "aim_delete": ("archive_survey_aims", ("researcher_id", "aim_id")),
    "negative_upsert": ("archive_survey_negatives", ("researcher_id", "neg_id")),
}
_RESTORE_JSONB = {"raw_jsonb", "usage_modes", "measures"}


def _restore_items(cur, sch: str, op: str, table: Optional[str], prior: dict) -> None:
    spec = _RESTORE_PK.get(op)
    if not spec:
        return
    tbl, pk_cols = spec
    for it in prior.get("items") or []:
        row = it.get("row")
        pk = it.get("pk", {})
        if row is None:
            # was confirmed-absent before apply → delete what we inserted
            where = " AND ".join(f"{k}=%s" for k in pk)
            cur.execute(f"DELETE FROM {sch}.{tbl} WHERE {where}",
                        tuple(pk.get(k) for k in pk))
        else:
            _restore_full_row(cur, sch, tbl, pk_cols, row)


def _restore_full_row(cur, sch: str, tbl: str, pk_cols: tuple, row: dict) -> None:
    cols = [c for c in row if c != "ingested_at"]
    placeholders = []
    params: list[Any] = []
    for c in cols:
        if c in _RESTORE_JSONB:
            placeholders.append("%s::jsonb")
            params.append(_j(row[c]) if row[c] is not None else None)
        else:
            placeholders.append("%s")
            params.append(row[c])
    upd = ",".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in pk_cols)
    sql = (f"INSERT INTO {sch}.{tbl} ({','.join(cols)}) "
           f"VALUES ({','.join(placeholders)}) "
           f"ON CONFLICT ({','.join(pk_cols)}) DO UPDATE SET {upd}")
    cur.execute(sql, tuple(params))


# ===========================================================================
# CLI
# ===========================================================================

def _resolve_source_version(args, manual_batch: Optional[str]) -> tuple[str, str]:
    batch = (args.batch or manual_batch or "r1").strip()
    today = date.today().isoformat()
    return batch, f"reply-{batch}@{today}"


def _read_answers(args, targets: set[str], reg: dict
                  ) -> tuple[list[Answer], Optional[str]]:
    """Dispatch to the channel reader. Returns (answers, manual_batch)."""
    if args.source == "manual":
        if not args.fixture:
            print("[reply] --source manual requires --fixture PATH.", file=sys.stderr)
            raise SystemExit(2)
        path = Path(args.fixture)
        batch = None
        try:
            blob = json.loads(path.read_text("utf-8"))
            batch = (blob.get("batch") if isinstance(blob, dict) else None)
        except Exception:
            pass
        return read_manual(path, targets), batch
    if args.source == "email":
        if not args.email_file:
            print("[reply] --source email requires --email-file PATH.",
                  file=sys.stderr)
            raise SystemExit(2)
        return read_email(Path(args.email_file), targets, reg), None
    # notion
    print("[reply] --source notion is READ-ONLY (retrieve only; never writes "
          "Notion).", file=sys.stderr)
    return read_notion(targets, reg), None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=("manual", "notion", "email"),
                    default="manual")
    ap.add_argument("--only", metavar="INIT", default=None,
                    help="Restrict to one researcher (e.g. BHL).")
    ap.add_argument("--fixture", metavar="PATH", default=None,
                    help="Manual JSON fixture (--source manual).")
    ap.add_argument("--email-file", metavar="PATH", default=None,
                    help=".eml file (--source email).")
    ap.add_argument("--registry", metavar="PATH", default=str(_DEFAULT_REGISTRY))
    ap.add_argument("--out", metavar="DIR", default=str(_TMP_DIR))
    ap.add_argument("--batch", default=None,
                    help="Reply batch tag for source_version (default: fixture "
                         "batch or 'r1').")
    ap.add_argument("--offline", action="store_true",
                    help="Force offline dry-run (no DB diff / no probe).")
    ap.add_argument("--stage", action="store_true",
                    help="Operator: INSERT ledger rows status='staged'.")
    ap.add_argument("--apply", action="store_true",
                    help="Operator: write body rows + ledger + supersede.")
    ap.add_argument("--revert", metavar="ANSWER_ID", type=int, default=None,
                    help="Operator: revert an applied answer via prior_snapshot.")
    ap.add_argument("--allow-resurrect", action="store_true",
                    help="Allow re-applying a superseded/reverted same-hash row.")
    args = ap.parse_args()

    db_ok = db_probe(force_offline=args.offline)
    sch = _schema()

    # --revert is a standalone operator op (no channel read needed).
    if args.revert is not None:
        precheck_p28("write", sch, dry_run=False)
        do_revert(args.revert, sch)
        _close_read_conn()
        return 0

    reg = load_registry(Path(args.registry))
    targets = {args.only.strip().upper()} if args.only else set()

    is_write = args.stage or args.apply
    # read_tier gates DB diffs in dry-run (when reachable); write_tier gates writes
    precheck_p28("write" if is_write else "read", sch, dry_run=not is_write)

    answers, manual_batch = _read_answers(args, targets, reg)
    batch, source_version = _resolve_source_version(args, manual_batch)
    ctx = Ctx(sch=sch, batch=batch, source_version=source_version, db_ok=db_ok)

    proposals: list[dict] = []
    for ans in answers:
        prop = map_answer(ans, reg, ctx)
        if prop is not None:
            proposals.append(prop)

    out_dir = Path(args.out)
    counts = emit(proposals, out_dir)

    # summary
    by_status: dict[str, int] = {}
    for p in proposals:
        by_status[p["status"]] = by_status.get(p["status"], 0) + 1
    print(f"\n[reply] {len(answers)} answer(s) → {counts['proposals']} proposal(s): "
          f"{by_status}")
    print(f"[reply] artifacts in {out_dir}: reply_proposals.jsonl "
          f"(gate={counts['gate']}, relevance={counts['relevance']}, "
          f"ops={counts['ops']})")
    for p in proposals:
        tag = p["status"].upper()
        extra = (f" — {p.get('review_reason')}" if p.get("review_reason")
                 else f" — {p.get('note')}" if p.get("note") else "")
        n_items = len((p.get("proposed_update") or {}).get("items") or [])
        print(f"    [{tag:11}] {p['question_id']:8} {p['target_op']:24} "
              f"items={n_items} conf={p.get('confidence')}{extra[:70]}")

    rc = 0
    if args.stage:
        stage_proposals(proposals, sch)
    if args.apply:
        # effective write gate: a real input artifact must back --apply (maps the
        # ingest_survey prefill-REFUSE precedent: no source-of-truth → refuse).
        if args.source == "manual" and not args.fixture:
            print("[reply] REFUSING --apply: --source manual needs --fixture.",
                  file=sys.stderr)
            rc = 2
        elif args.source == "email" and not args.email_file:
            print("[reply] REFUSING --apply: --source email needs --email-file.",
                  file=sys.stderr)
            rc = 2
        else:
            apply_proposals(proposals, sch, args.allow_resurrect)
    if not is_write:
        print("[reply] dry-run only — artifacts written, ZERO DB writes. "
              "Re-run with --stage / --apply (operator) to persist.")
    _close_read_conn()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
