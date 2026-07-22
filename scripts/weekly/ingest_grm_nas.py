#!/usr/bin/env python3
"""
scripts/weekly/ingest_grm_nas.py — weekly scan of the NAS GRM folders →
csnl_paper_rec.archive_meeting_materials (one row per slide FILE).

WHAT IT DOES
  Every week (Wednesday afternoon, after the GRM ends) the lab drops the
  Paper-Blitz + Research-Meeting slide files into a dated folder on the NAS:
      <share>/GRM/<YYYY>/<YYYYMMDD>/
  This script walks the recent folders, classifies each file as a Paper Blitz
  (pb) or a Research Meeting (grm / focus_grm), pulls the presenter out of the
  filename, OPTIONALLY enriches presenter/order/type from the Notion schedule,
  and writes one idempotent row per file (UNIQUE(nas_path)) into the
  meeting-material index.

CATALOG-DRIVEN, NOT HARDCODED  (operator directive 2026-07-21)
  The lab's filenames are inconsistent, so rigid regexes lose real material.
  Every fact about *observed* naming — people/initials/aliases/Korean names,
  noise tokens that must never become a presenter, the extension allow-list,
  junk filenames, the nested 'Paper Blitz/' subfolder — lives as DATA in
  `config/nas_catalog.json` (override with NAS_CATALOG_PATH). This module only
  implements TOLERANT matching over that data. A new naming convention or a new
  lab member is a CATALOG edit, never a code change. Every catalog lookup has a
  small built-in fallback so the ingest still runs if the file is absent.

CLASSIFICATION (filename, tokenised — never anchored, never substring)
  The stem is NFC-normalised and split on [_ -.,()…] plus script/camel
  boundaries, so 'PB250716_JOP' → {PB, 250716, JOP} and 'specialGRM' →
  {special, GRM} while 'AIGRM' stays ONE token (an all-caps run is not split —
  that is what used to manufacture presenter_name='AIGRM').
    pb        : any WHOLE token in {PB, PAPERBLITZ, BLITZ} — matches
                'PB_260715.pptx', 'Paper Blitz (1).pptx', 'MJC_PB_260107.pdf',
                'JOP_PB260429.pdf', 'JOP_AI_Blitz_260204.pdf',
                '260506_PaperBlitz_SK_Carricarte2025.pdf'.
                *** PB IS NOT ALWAYS LAB-WIDE. *** 122 of 267 catalogued PB
                files carry the presenter in the filename, so the presenter is
                extracted for pb too; NULL is kept ONLY for genuinely lab-wide
                bundles ('PB_260715.pptx', 'Paper Blitz (1) 2.pptx').
    grm       : a WHOLE token 'GRM' ('260610 GRM.pptx', 'BYL_specialGRM.pptx'),
                OR a date token (6/8 digits, plausible) together with an
                initial token IN ANY ORDER ('260715_JOP.pptx',
                'JOP_260325_part1.pptx', 'MSY_260422_MSY.key') — unless the
                name carries a grant-report marker ('중견', '연차보고서'), which
                is neither grm nor pb.
    focus_grm : a grm-kind row whose meeting_date is a Notion Type='Focus GRM'
                day (only when the Notion schedule is reachable).
    presenter : a whole-token match against the catalog's initials{} — the
                INIT itself, its aliases (MinJin→MJC, LBY→BYL, RJH→JHR, KY→SK),
                its Korean name (정새미→SMJ) or its romanised name. Never a
                coined token: 'special' / 'Special' / 'AIGRM' resolve to NULL.
                An UNKNOWN but initial-shaped token ([A-Z]{2,4}, not a noise
                token) is kept as presenter_name with a NULL initial so a new
                lab member is still discovered — add them to the catalog.
    skipped   : Office lock stubs ('~$…'), dotfiles/AppleDouble, Thumbs.db /
                desktop.ini, any extension outside the catalog allow-list, and
                anything that is neither pb nor grm (grant reports,
                AI-workshop decks, Zotero paper exports, manuscripts). Every
                skip is recorded WITH a reason (a directory used to vanish
                silently) — `--show-skipped` prints them.

SUBFOLDER DESCENT (one level)
  2025's per-presenter PB decks live one level down in a 'Paper Blitz/'
  subfolder that a flat iterdir() never sees (32 of 47 2025_GRM date folders).
  A child directory whose space-stripped lowercase name matches the catalog's
  nested_paperblitz_subdir is descended ONE level and its files are classified
  with kind='pb' by folder context. Any other directory is recorded as skipped
  with a reason instead of disappearing. Reach the archive years with
      GRM_NAS_BASE='<share>/GRM/GRM Archive/2025_GRM' --weeks 520
  (date-folder names are parsed with the catalog's date_dir_forms, so the
  dotted 2022/2023 conventions are understood too).

NOTION SCHEDULE ENRICH (optional · graceful)
  Authoritative presenter/order/type = the Notion DB 'GRM 발표 순번 리스트'
  (data source 4088bc86-a8e3-4386-8f3e-fee006563a0d, under the 'CSNL GRM' page).
  We match by date and fill presenter_name / schedule_order / schedule_type.
  The workspace integration token may NOT be shared into 'CSNL GRM' — if the
  query 403s / errors / NOTION_API_KEY is absent, we log one line and proceed
  NAS-primary (matched_schedule=false). `--no-notion` skips it entirely. See
  docs/GRM-INGEST-FLOW.md for how to share the integration into 'CSNL GRM'.

BOUNDARY
  * The NAS share is READ-ONLY — this script never writes a single byte to it,
    never mounts and never re-authenticates (the NAS auto-bans repeat logins).
  * DB writes land in csnl_paper_rec.archive_meeting_materials ONLY. The
    csnl_research / csnl_ops schemas and archive_responses are never touched.
  * DRY-RUN is the DEFAULT: preview to stdout + a JSONL artifact
    (state/archive/_tmp/grm_materials_preview.jsonl), ZERO DB writes. It also
    runs with NO database at all (the table precheck + new/existing diff only
    happen when the DB is reachable).
  * --apply UPSERTs (ON CONFLICT(nas_path) DO UPDATE) — operator-run via
    launchd/`!` with .env creds (not agent-held DB access).

EXIT CODES (matter for the weekly wrapper's once-per-week marking)
  0  success (rows upserted, OR the mounted folder(s) were genuinely empty).
  1  --self-test only: a classification assertion failed.
  2  --apply but the migration is missing, or the DB is unreachable.
  3  --apply but the NAS share is NOT mounted (NAS_BASE absent) — distinct
     from a genuinely-empty mounted folder so the wrapper does NOT mark the
     week done and retries on the next boot/calendar fire (a transient unmount
     no longer silently burns the week).

CLI
  python3 scripts/weekly/ingest_grm_nas.py                  # DRY-RUN, last 4 weeks
  python3 scripts/weekly/ingest_grm_nas.py --weeks 8        # DRY-RUN, last 8 weeks
  python3 scripts/weekly/ingest_grm_nas.py --date 20260610  # DRY-RUN, one folder
  python3 scripts/weekly/ingest_grm_nas.py --show-skipped   # why each file was dropped
  python3 scripts/weekly/ingest_grm_nas.py --self-test      # offline, no NAS/DB
  python3 scripts/weekly/ingest_grm_nas.py --no-notion      # skip schedule enrich
  ! python3 scripts/weekly/ingest_grm_nas.py --weeks 4 --apply   # operator write
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
sys.path.insert(0, str(_HERE))

# NAS layout (mounted share). Resolved at runtime — see _resolve_nas_base().
LEGACY_NAS_VOLUME = "/Volumes/CSNL_new-1"   # historical hardcode; fallback only


def _resolve_nas_base(year: str, volumes_root: str = "/Volumes") -> Path:
    """Locate the mounted GRM/<year> directory without hardcoding a volume.

    macOS mounts an SMB share **per user** and mode-700, so one lab member's
    mount is unreadable to another. When a second user mounts the SAME share,
    macOS appends a collision-avoiding suffix: the first mounter gets
    '/Volumes/CSNL_new', the second '/Volumes/CSNL_new-1', the third '-2'.
    Which name *this* account ends up with therefore depends on who mounted
    first, so the old hardcoded '/Volumes/CSNL_new-1' was a race between lab
    members — it broke whenever the mount order changed or nobody had mounted.

    Resolution order:
      1. GRM_NAS_BASE — an explicit override always wins (testing, odd mounts,
         and the 'GRM Archive/<YYYY>_GRM' backfill).
      2. scan /Volumes for a READABLE <volume>/GRM/<year>; sorted, so the pick
         is deterministic when several are readable. Another user's mode-700
         mount raises EACCES on traversal and is skipped.
      3. fall back to the legacy path, so the caller's "not mounted" message
         still names something concrete.
    """
    override = os.environ.get("GRM_NAS_BASE")
    if override:
        return Path(override)

    rel = Path("GRM") / year
    try:
        volumes = sorted(Path(volumes_root).iterdir())
    except OSError:
        volumes = []

    for vol in volumes:
        candidate = vol / rel
        try:
            if candidate.is_dir() and os.access(candidate, os.R_OK | os.X_OK):
                return candidate
        except OSError:
            continue            # another user's mode-700 mount -> EACCES; skip

    return Path(LEGACY_NAS_VOLUME) / rel


# Year is overridable and defaults to the CURRENT year, so the ingest does not
# silently stop finding material the moment the calendar rolls over.
NAS_YEAR = os.environ.get("GRM_NAS_YEAR") or str(datetime.now().year)
NAS_BASE = _resolve_nas_base(NAS_YEAR)
NAS_FOLDER_PREFIX = f"GRM/{NAS_YEAR}"   # stored nas_folder = 'GRM/<year>/<date>'

_TMP_DIR = _REPO_ROOT / "state" / "archive" / "_tmp"
_DEFAULT_OUT = _TMP_DIR / "grm_materials_preview.jsonl"

# Notion schedule (authoritative presenter/order/type). DB id = the UUID of the
# 'GRM 발표 순번 리스트' data source under the 'CSNL GRM' page. Overridable via env.
GRM_SCHEDULE_DB_ID = os.environ.get(
    "NOTION_GRM_SCHEDULE_DB_ID", "4088bc86-a8e3-4386-8f3e-fee006563a0d")
SCHED_PROP_DATE = "날짜"
SCHED_PROP_TYPE = "Type"
SCHED_PROP_PRESENTER = "발표자 이름"
SCHED_PROP_ORDER = "발표 순번"


# ===========================================================================
# Metadata catalog — config/nas_catalog.json is the DATA plane for everything
# below. Each accessor falls back to a small built-in default so this module
# still works when the catalog is missing, but the catalog always wins.
# ===========================================================================

CATALOG_PATH = Path(os.environ.get("NAS_CATALOG_PATH")
                    or (_REPO_ROOT / "config" / "nas_catalog.json"))


@lru_cache(maxsize=4)
def load_catalog(path: str = "") -> dict:
    """Parsed config/nas_catalog.json ({} when unreadable — warned once)."""
    p = Path(path) if path else CATALOG_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"[grm] nas_catalog unreadable ({p}: {type(e).__name__}) — "
              f"falling back to built-in defaults. Matching will be weaker; "
              f"restore the catalog rather than hardcoding here.",
              file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def _cat(*keys, default=None):
    """catalog['a']['b']… with a default; never raises."""
    node = load_catalog()
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node if node is not None else default


def _norm(s: str) -> str:
    """NFC — the share stores NFD, so EVERY comparison normalises first."""
    return unicodedata.normalize("NFC", s)


def _key(tok: str) -> str:
    """Case/format-insensitive lookup key for a filename token."""
    return _norm(tok).upper()


# --------------------------------------------------------------- extensions
_FALLBACK_EXT = {"pdf", "pptx", "ppt", "key"}


@lru_cache(maxsize=1)
def allowed_ext() -> frozenset:
    allow = _cat("ignore_rules", "extension_allow_list", "allow", default=None)
    if not isinstance(allow, list) or not allow:
        return frozenset(_FALLBACK_EXT)
    return frozenset(str(x).lstrip(".").lower() for x in allow if x)


ALLOWED_EXT = allowed_ext()          # kept as a module constant for callers

# ------------------------------------------------------------- junk / noise
_FALLBACK_JUNK_NAMES = {"THUMBS.DB", "DESKTOP.INI", ".DS_STORE"}


@lru_cache(maxsize=1)
def junk_names() -> frozenset:
    extra = _cat("ignore_rules", "dotfiles", "extra_names", default=None)
    got = set(_FALLBACK_JUNK_NAMES)
    if isinstance(extra, list):
        got |= {_key(str(x)) for x in extra if x}
    return frozenset(got)


# Common file-noise tokens that are NOT a presenter. The catalog owns this
# list (matching_policy.noise_tokens.tokens); the fallback below only keeps the
# ingest sane when the catalog is missing. EXTEND THE CATALOG, NOT THIS SET.
_FALLBACK_NOISE = {
    "REVIEW", "FINAL", "REVISED", "REVISION", "DRAFT", "COPY", "VER",
    "VERSION", "SLIDE", "SLIDES", "MEETING", "SEMINAR", "PRESENTATION",
    "PRESENT", "UPDATED", "UPDATE", "NEW", "OLD", "LAB", "CSNL", "RESEARCH",
    "JOURNAL", "CLUB", "TALK", "SHARE", "WEEK", "FIX", "FIXED", "MERGED",
    "TEMP", "BACKUP", "TEST", "PB", "PAPERBLITZ", "BLITZ", "PAPER", "GRM",
    "AIGRM", "AI", "MM", "RM", "SPECIAL", "ABSTRACT", "PART", "WORKSHOP",
    "PART1", "PART2", "PART3", "RAG", "MCP", "DEEPPREP", "NOTEBOOKLM",
    "PROPOSAL", "RESEARCHMEETING", "POST", "NAME", "THUMBS",
    "PDF", "PPTX", "PPT", "KEY", "DOCX",
}


@lru_cache(maxsize=1)
def noise_tokens() -> frozenset:
    got = set(_FALLBACK_NOISE)
    toks = _cat("matching_policy", "noise_tokens", "tokens", default=None)
    if isinstance(toks, list):
        got |= {_key(str(t)) for t in toks if t}
    # parser artifacts the catalog explicitly flags as "not a person"
    np = _cat("initials", "_alias_policy", "not_people", "tokens", default=None)
    if isinstance(np, list):
        got |= {_key(str(t)) for t in np if t}
    got |= {e.upper() for e in allowed_ext()}
    return frozenset(got)


# ---------------------------------------------------------------- kind tokens
_FALLBACK_KIND_TOKENS = {"pb": ["PB", "PAPERBLITZ", "BLITZ"], "grm": ["GRM"]}


@lru_cache(maxsize=1)
def kind_tokens() -> dict:
    """kind → frozenset of WHOLE tokens that assert it. Catalog-overridable
    via matching_policy.kind_tokens (absent today → the fallback is used)."""
    cfg = _cat("matching_policy", "kind_tokens", default=None)
    src = cfg if isinstance(cfg, dict) and cfg else _FALLBACK_KIND_TOKENS
    out = {}
    for kind, toks in src.items():
        if kind in ("pb", "grm") and isinstance(toks, list):
            out[kind] = frozenset(_key(str(t)) for t in toks if t)
    for kind, toks in _FALLBACK_KIND_TOKENS.items():
        out.setdefault(kind, frozenset(toks))
    return out


# Grant annual reports ride in the same folders but are neither grm nor pb
# (N2-grm §2a). They only veto the *fallback* (date+initial) branch — an
# explicit GRM/PB token still wins.
_FALLBACK_REPORT_MARKERS = {"연차보고서", "중견", "보고서"}


@lru_cache(maxsize=1)
def report_markers() -> frozenset:
    cfg = _cat("matching_policy", "report_marker_tokens", default=None)
    if isinstance(cfg, list) and cfg:
        return frozenset(_key(str(t)) for t in cfg if t)
    return frozenset(_key(t) for t in _FALLBACK_REPORT_MARKERS)


# --------------------------------------------------- nested 'Paper Blitz/' dir
@lru_cache(maxsize=1)
def pb_subdir_keys() -> frozenset:
    """Space-stripped lowercase names of child dirs to descend ONE level into.
    Derived from the catalog's nested_paperblitz_subdir.path so a renamed
    convention is a data edit."""
    got = {"paperblitz"}
    node = _cat("directories", "grm_archive", "nested_paperblitz_subdir",
                default=None)
    if isinstance(node, dict):
        raw = str(node.get("path") or "")
        for part in raw.split("/"):
            part = part.strip()
            if part and "{" not in part:
                flat = re.sub(r"\s+", "", _norm(part)).lower()
                if "blitz" in flat:
                    got.add(flat)
        sample = (node.get("sample_listing") or {}).get("folder")
        if isinstance(sample, str) and sample:
            last = sample.rstrip("/").split("/")[-1]
            flat = re.sub(r"\s+", "", _norm(last)).lower()
            if "blitz" in flat:
                got.add(flat)
    return frozenset(got)


# ---------------------------------------------------------- date-folder forms
_FALLBACK_DATE_DIR_FORMS = [r"^(\d{8})$"]


@lru_cache(maxsize=1)
def date_dir_forms() -> tuple:
    forms = _cat("directories", "grm_archive", "date_dir_forms", "forms",
                 default=None)
    out = []
    if isinstance(forms, list):
        for f in forms:
            pat = str(f).split("  ")[0].strip()      # strip the trailing note
            try:
                out.append(re.compile(pat))
            except re.error:
                continue
    if not out:
        out = [re.compile(p) for p in _FALLBACK_DATE_DIR_FORMS]
    return tuple(out)


def parse_dir_date(name: str) -> Optional[date]:
    """Folder name → date, trying the catalog's forms IN ORDER (most specific
    first) so '2025.08.14' is never re-read as YY.MM.DD."""
    nm = _norm(name).strip()
    for rx in date_dir_forms():
        m = rx.match(nm)
        if not m:
            continue
        groups = [g for g in m.groups() if g]
        if not groups:
            continue
        if len(groups) == 1 and len(groups[0]) == 8:
            digits = groups[0]
        elif len(groups) >= 3:
            digits = f"{groups[0]}{groups[1]}{groups[2]}"
        else:
            continue
        try:
            return datetime.strptime(digits, "%Y%m%d").date()
        except ValueError:
            continue
    return None


# ===========================================================================
# Researcher registry (init ↔ name) + presenter index.
# config/researchers.yaml stays the delivery registry; config/nas_catalog.json
# adds everyone who actually appears on the share (MJC, JHR, HJH, HOJ, SHP, …)
# plus their aliases / Korean names. Both are DATA.
# ===========================================================================

_FALLBACK_REGISTRY = {
    "BHL": "이보현", "BYL": "이보연", "JOP": "박준오", "JYK": "김정예",
    "MSY": "여민수", "SMJ": "정새미", "SYJ": "조수영",
    "SK": "김성제", "JSL": "임재섭",
}
_YAML_ROW_RE = re.compile(r"^\s*([A-Z]{2,8})\s*:\s*\{\s*name:\s*([^,}]+)")
_INIT_SHAPE_RE = re.compile(r"^[A-Z]{2,8}$")


def load_registry() -> dict[str, str]:
    """init → display name. researchers.yaml ∪ nas_catalog.initials ∪ fallback."""
    path = _REPO_ROOT / "config" / "researchers.yaml"
    reg: dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            m = _YAML_ROW_RE.match(raw)
            if m:
                reg[m.group(1).upper()] = m.group(2).strip().strip("'\"")
    except OSError:
        pass
    for init, rec in (_cat("initials", default={}) or {}).items():
        if init.startswith("_") or not _INIT_SHAPE_RE.match(init):
            continue
        if not isinstance(rec, dict):
            continue
        nm = rec.get("korean_name") or rec.get("full_name")
        if nm:
            reg.setdefault(init, str(nm))
        else:
            reg.setdefault(init, "")
    for k, v in _FALLBACK_REGISTRY.items():
        reg.setdefault(k, v)
    return reg


REGISTRY = load_registry()
KNOWN_INITIALS = set(REGISTRY)


def _alias_variants(value: str):
    """Yield lookup keys for one alias/name string. Commentary entries
    ("LS (probable — …)") are refused; spaced names also index spaceless."""
    s = _norm(str(value)).strip()
    if not s or "(" in s or "—" in s or len(s) > 40:
        return
    yield s.upper()
    flat = re.sub(r"\s+", "", s)
    if flat and flat != s:
        yield flat.upper()


@lru_cache(maxsize=1)
def presenter_index() -> dict:
    """token key → (INIT, display name). Built from the catalog's initials{}
    (INIT, aliases, korean_name, full_name) plus _alias_policy.merge_allowed
    and config/researchers.yaml. Ambiguous keys are DROPPED, never guessed —
    merging two people permanently poisons the reading history."""
    idx: dict[str, tuple] = {}
    dropped: set[str] = set()
    noise = noise_tokens()

    def add(tok: str, init: str, disp: str) -> None:
        for k in _alias_variants(tok):
            if not k or k in noise:
                continue
            cur = idx.get(k)
            if cur and cur[0] != init:
                dropped.add(k)
                idx.pop(k, None)
                continue
            if k in dropped:
                continue
            idx[k] = (init, disp)

    cat_inits = _cat("initials", default={}) or {}
    for init, rec in cat_inits.items():
        if init.startswith("_") or not _INIT_SHAPE_RE.match(init):
            continue
        if not isinstance(rec, dict):
            continue
        disp = rec.get("korean_name") or rec.get("full_name") or ""
        add(init, init, str(disp))
        for a in (rec.get("aliases") or []):
            add(str(a), init, str(disp))
        for field in ("korean_name", "full_name"):
            if rec.get(field):
                add(str(rec[field]), init, str(disp))

    for row in (_cat("initials", "_alias_policy", "merge_allowed",
                     default=[]) or []):
        if not isinstance(row, dict):
            continue
        src, dst = row.get("from"), row.get("to")
        if not src or not dst:
            continue
        if (_key(dst) not in idx) and (dst not in cat_inits):
            continue                     # merge target is not a known person
        disp = idx.get(_key(dst), (dst, REGISTRY.get(dst, "")))[1]
        add(str(src), str(dst), str(disp))

    for init, nm in REGISTRY.items():
        add(init, init, nm)
        if nm:
            add(nm, init, nm)
    return idx


# ===========================================================================
# Filename tokenisation + classification
# ===========================================================================

_DATE_DIR_RE = re.compile(r"^\d{8}$")            # --date argument shape
_SEP_RE = re.compile(r"[\s_\-.,;+&#@!~'\"/\\\[\]{}()]+")
_CAMEL_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+")
_RUN_RE = re.compile(r"[A-Za-z]+|[0-9]+|[^A-Za-z0-9]+")
_DATE_TOK_RE = re.compile(r"^(?:\d{6}|\d{8})$")
_UNKNOWN_INIT_RE = re.compile(r"^[A-Z]{2,4}$")


def _ext_of(name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    return ext.lower() if dot else ""


def _subtokens(tok: str):
    """Script/case-transition split of ONE token.
    'PB250716' → PB, 250716 · 'specialGRM' → special, GRM ·
    'JHR중견연차보고서2026' → JHR, 중견연차보고서, 2026 · 'AIGRM' → AIGRM
    (an all-caps run has no transition, which is exactly why 'grm' must never
    be matched as a substring)."""
    out = []
    for run in _RUN_RE.findall(tok):
        run = run.strip()
        if not run:
            continue
        if run.isascii() and run.isalpha():
            parts = _CAMEL_RE.findall(run)
            out.extend(parts if parts else [run])
        else:
            out.append(run)
    return out


def tokenise(name: str) -> tuple[list, list]:
    """(raw tokens, all tokens) for a filename. The extension is stripped; the
    stem is NFC-normalised and split on separators, then each raw token also
    contributes its script/camel sub-tokens. Raw tokens come first so an alias
    like 'MinJin' matches before its 'Min'/'Jin' split."""
    nfc = _norm(name)
    ext = _ext_of(nfc)
    stem = nfc[: -(len(ext) + 1)] if ext else nfc
    raw = [t for t in _SEP_RE.split(stem) if t]
    allt: list[str] = []
    for t in raw:
        allt.append(t)
        for sub in _subtokens(t):
            if sub != t and sub not in allt:
                allt.append(sub)
    return raw, allt


def _is_date_token(tok: str) -> bool:
    """6/8 digits AND a plausible calendar date — so '106294' (a page range in
    a paper title) never poses as 2010-62-94."""
    if not _DATE_TOK_RE.match(tok):
        return False
    if len(tok) == 8:
        yy, mm, dd = tok[:4], tok[4:6], tok[6:]
        if not ("1900" <= yy <= "2099"):
            return False
    else:
        mm, dd = tok[2:4], tok[4:]
    return "01" <= mm <= "12" and "01" <= dd <= "31"


def _find_presenter(raw: list, allt: list) -> tuple:
    """(initial, name, source). A catalog match wins; otherwise an UNKNOWN but
    initial-shaped raw token is kept as a name-only presenter so a new lab
    member is still discovered (add them to nas_catalog.initials). Junk tokens
    ('special', 'Special', 'AIGRM') resolve to (None, None)."""
    idx = presenter_index()
    for t in allt:
        hit = idx.get(_key(t))
        if hit:
            init, disp = hit
            return init, (disp or REGISTRY.get(init) or None), "catalog"
    noise = noise_tokens()
    for t in raw:
        tn = _norm(t)
        if _UNKNOWN_INIT_RE.match(tn) and _key(tn) not in noise:
            return None, tn, "unknown-token"
    return None, None, "none"


def classify_detail(name: str, dir_kind: Optional[str] = None) -> dict:
    """Full classification record for one filename (see classify())."""
    raw, allt = tokenise(name)
    keys = [_key(t) for t in allt]
    kt = kind_tokens()
    kind = kind_token = None
    for want in ("pb", "grm"):                 # PB first: 'JOP_PB260429.pdf'
        for t, k in zip(allt, keys):
            if k in kt.get(want, frozenset()):
                kind, kind_token = want, t
                break
        if kind:
            break

    dates = [t for t in allt if _is_date_token(t)]
    p_init, p_name, p_src = _find_presenter(raw, allt)
    has_report_marker = any(k in report_markers() for k in keys)

    source = "kind-token" if kind else None
    if not kind and dir_kind in ("pb", "grm"):
        kind, source = dir_kind, "folder-context"
    if not kind and dates and (p_init or p_name) and not has_report_marker:
        # date + initial in ANY order — 260715_JOP · JOP_260325_part1 · …
        kind, source = "grm", "date+initial"

    if not kind:
        why = "no kind token"
        if has_report_marker:
            why = "grant report (report marker token)"
        elif dates and not (p_init or p_name):
            why = "date but no presenter token"
        elif not dates:
            why = "no kind token and no date token"
        return {"kind": None, "presenter_initial": None, "presenter_name": None,
                "reason": why, "match": {"tokens": raw}}

    if not (p_init or p_name):
        p_src = "lab-wide"                     # genuine bundle → presenter NULL
    return {
        "kind": kind,
        "presenter_initial": p_init,
        "presenter_name": p_name,
        "reason": None,
        "match": {
            "kind_source": source,
            "kind_token": kind_token,
            "presenter_source": p_src,
            "date_tokens": dates,
        },
    }


def classify(name: str, dir_kind: Optional[str] = None) -> tuple:
    """(kind, presenter_initial, presenter_name). kind None → unclassifiable.

    Stable 3-tuple contract — scripts/weekly/check_materials.py imports this.
    `dir_kind` is set to 'pb' when the file was found inside a nested
    'Paper Blitz/' folder; callers that only have a name may omit it.
    """
    d = classify_detail(name, dir_kind)
    return d["kind"], d["presenter_initial"], d["presenter_name"]


def _extract_presenter(name: str) -> tuple:
    """Back-compat shim: (presenter_initial, presenter_name)."""
    raw, allt = tokenise(name)
    init, nm, _ = _find_presenter(raw, allt)
    return init, nm


# ===========================================================================
# NAS scan (read-only)
# ===========================================================================

def _target_dirs(args) -> list:
    """[(date, Path)] of date folders to scan, oldest first."""
    if not NAS_BASE.exists():
        print(f"[grm] NAS base not mounted: {NAS_BASE} — nothing to scan. "
              f"Scanned /Volumes for a readable */GRM/{NAS_YEAR}; another "
              f"user's mount is mode-700 and cannot be used. Mount the share "
              f"as this user, or set GRM_NAS_BASE.", file=sys.stderr)
        return []
    try:
        children = sorted(NAS_BASE.iterdir(), key=lambda p: p.name)
    except OSError as e:
        print(f"[grm] cannot list {NAS_BASE} ({type(e).__name__})",
              file=sys.stderr)
        return []

    dated = []
    for child in children:
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        d = parse_dir_date(child.name)
        if d:
            dated.append((d, child))

    if args.date:
        if not _DATE_DIR_RE.match(args.date):
            print(f"[grm] --date must be YYYYMMDD (got {args.date!r})",
                  file=sys.stderr)
            return []
        try:
            want = datetime.strptime(args.date, "%Y%m%d").date()
        except ValueError:
            print(f"[grm] --date is not a real date: {args.date!r}",
                  file=sys.stderr)
            return []
        hit = [(d, p) for d, p in dated if d == want]
        if not hit:
            print(f"[grm] folder not found for {args.date} under {NAS_BASE}",
                  file=sys.stderr)
        return sorted(hit, key=lambda t: t[0])

    cutoff = date.today() - timedelta(days=max(args.weeks, 1) * 7)
    out = [(d, p) for d, p in dated if d >= cutoff]
    out.sort(key=lambda t: t[0])
    return out


def _nas_folder_for(folder: Path) -> str:
    """Share-relative folder label ('GRM/2026/20260715',
    'GRM/GRM Archive/2025_GRM/20250716/Paper Blitz'). Derived from the path so
    an archive backfill is not mislabelled with the current year. No resolve()
    — the share root holds a SELF-REFERENTIAL symlink."""
    parts = [_norm(p) for p in folder.parts]
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == "GRM":
            return "/".join(parts[i:])
    return f"{NAS_FOLDER_PREFIX}/{_norm(folder.name)}"


def _ignore_reason(nm: str) -> Optional[str]:
    """Why this entry is not material at all (catalog ignore_rules)."""
    n = _norm(nm)
    if n.startswith("~$"):
        return "Office lock stub (~$)"
    if n.startswith("._"):
        return "AppleDouble sidecar"
    if n.startswith("."):
        return "dotfile"
    if _key(n) in junk_names():
        return "OS junk file"
    return None


def _scan_files(folder: Path, meeting: date, nas_folder: str,
                dir_kind: Optional[str]) -> tuple:
    """(rows, skipped) for the FILES directly inside one folder."""
    rows: list = []
    skipped: list = []
    iso_date = meeting.isoformat()
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name)
    except OSError as e:
        return rows, [f"{folder.name}/ — unreadable ({type(e).__name__})"]

    for entry in entries:
        nm = entry.name
        reason = _ignore_reason(nm)
        if reason:
            skipped.append(f"{nm} — {reason}")
            continue
        try:
            is_file = entry.is_file()
        except OSError:
            is_file = False
        if not is_file:
            continue                     # directories handled by the caller
        ext = _ext_of(_norm(nm))
        if ext not in allowed_ext():
            skipped.append(f"{nm} — extension .{ext or '?'} not in allow-list")
            continue
        det = classify_detail(nm, dir_kind)
        if det["kind"] is None:
            skipped.append(f"{nm} — {det['reason']}")
            continue
        try:
            st = entry.stat()
            size, mtime = st.st_size, int(st.st_mtime)
        except OSError:
            size, mtime = None, None
        rows.append({
            "meeting_date": iso_date,
            "kind": det["kind"],
            "presenter_initial": det["presenter_initial"],
            "presenter_name": det["presenter_name"],
            "nas_folder": nas_folder,
            "filename": nm,
            "nas_path": str(entry),
            "file_format": ext,
            "schedule_order": None,
            "schedule_type": None,
            "matched_schedule": False,
            "source": "nas-scan",
            "raw_jsonb": {"size": size, "mtime": mtime,
                          "match": det["match"]},
        })
    return rows, skipped


def _scan_folder(folder: Path, meeting: date) -> tuple:
    """(material rows, skipped descriptions) for one dated folder, descending
    ONE level into a nested 'Paper Blitz/' child (2025's per-presenter decks).
    Every other directory is RECORDED with a reason instead of vanishing."""
    nas_folder = _nas_folder_for(folder)
    rows, skipped = _scan_files(folder, meeting, nas_folder, None)

    try:
        children = sorted(folder.iterdir(), key=lambda p: p.name)
    except OSError:
        return rows, skipped
    for child in children:
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        nm = child.name
        if _ignore_reason(nm):
            continue                     # already recorded by _scan_files
        flat = re.sub(r"\s+", "", _norm(nm)).lower()
        if flat in pb_subdir_keys():
            sub_rows, sub_skipped = _scan_files(
                child, meeting, f"{nas_folder}/{_norm(nm)}", "pb")
            rows.extend(sub_rows)
            skipped.extend(f"{nm}/{s}" for s in sub_skipped)
            skipped.append(f"{nm}/ — descended (nested Paper Blitz, "
                           f"{len(sub_rows)} file(s))")
        else:
            skipped.append(f"{nm}/ — directory, not descended "
                           f"(only a nested Paper Blitz folder is)")
    return rows, skipped


# ===========================================================================
# Notion schedule enrich (optional · graceful)
# ===========================================================================

def _fetch_schedule(dates_iso: set) -> dict:
    """date(YYYY-MM-DD) → {presenter_name, schedule_order, schedule_type,
    page_url}. Returns {} on ANY failure (token missing / 403 not-shared / API
    error) so the ingest stays NAS-primary."""
    try:
        import _notion  # noqa: E402
        rows = _notion.query_database(GRM_SCHEDULE_DB_ID)
    except Exception as e:                  # NotionError / ImportError / network
        print(f"[grm] Notion schedule unreachable ({type(e).__name__}: "
              f"{str(e)[:120]}) — NAS-primary, matched_schedule=false. Share "
              f"the integration into 'CSNL GRM' to enable enrich.",
              file=sys.stderr)
        return {}
    out: dict = {}
    for pg in rows:
        props = pg.get("properties") or {}
        d = props.get(SCHED_PROP_DATE) or {}
        start = ((d.get("date") or {}) or {}).get("start") if isinstance(d.get("date"), dict) else None
        if not start:
            continue
        iso = start[:10]
        if iso not in dates_iso:
            continue
        # presenter (formula → string), order (number), type (select)
        fp = props.get(SCHED_PROP_PRESENTER) or {}
        formula = fp.get("formula") or {}
        pname = formula.get("string") or formula.get("number")
        op = props.get(SCHED_PROP_ORDER) or {}
        order = op.get("number")
        tp = props.get(SCHED_PROP_TYPE) or {}
        sel = tp.get("select") or {}
        stype = sel.get("name") if isinstance(sel, dict) else None
        out[iso] = {
            "presenter_name": (str(pname).strip() if pname not in (None, "") else None),
            "schedule_order": order,
            "schedule_type": stype,
            "page_url": pg.get("url"),
        }
    return out


def enrich_with_schedule(rows: list) -> int:
    """Fill schedule_* on GRM-kind rows that match a Notion date; promote
    grm→focus_grm on Focus-GRM days. Returns the number of rows enriched.
    PB rows keep their filename-derived presenter (the schedule names the GRM
    presenter only — it would otherwise overwrite the PB attribution)."""
    dates = {r["meeting_date"] for r in rows}
    sched = _fetch_schedule(dates)
    if not sched:
        return 0
    n = 0
    for r in rows:
        s = sched.get(r["meeting_date"])
        if not s:
            continue
        if r["kind"] not in ("grm", "focus_grm"):
            continue                        # the schedule is the GRM presenter
        r["matched_schedule"] = True
        if s.get("presenter_name"):
            r["presenter_name"] = s["presenter_name"]
        r["schedule_order"] = s.get("schedule_order")
        r["schedule_type"] = s.get("schedule_type")
        if (s.get("schedule_type") or "").strip().lower() == "focus grm":
            r["kind"] = "focus_grm"
        if s.get("page_url"):
            r["raw_jsonb"]["schedule_page_url"] = s["page_url"]
        n += 1
    return n


# ===========================================================================
# DB precheck + diff (only when the DB is reachable)
# ===========================================================================

def _db_status() -> tuple:
    """('ok'|'missing'|'offline', schema). 'offline' = could not connect (fine
    for dry-run); 'missing' = connected but the table is absent."""
    try:
        from _db import load_env, ledger_schema, query_json
        load_env()
        sch = ledger_schema()
        # information_schema (not to_regclass) so the SELECT has a real,
        # non-NULL column that does NOT collide with query_json's 't' table
        # alias — a NULL-only row aggregates to [null] and would be misread.
        rows = query_json(
            f"SELECT 1 AS present FROM information_schema.tables "
            f"WHERE table_schema = '{sch}' "
            f"AND table_name = 'archive_meeting_materials'")
        return ("ok" if rows else "missing"), sch
    except Exception:
        return "offline", None


def _existing_paths(sch: str, dates_iso: set) -> set:
    try:
        from _db import query_json
        if not dates_iso:
            return set()
        in_list = ",".join("'" + d.replace("'", "''") + "'" for d in dates_iso)
        rows = query_json(
            f"SELECT nas_path FROM {sch}.archive_meeting_materials "
            f"WHERE meeting_date IN ({in_list})")
        return {r["nas_path"] for r in rows}
    except Exception:
        return set()


_MIGRATION_HINT = (
    "! python3 scripts/run_migration.py "
    "state/migrations/2026-06-12_p31_meeting_materials.sql")


# ===========================================================================
# Upsert (operator --apply)
# ===========================================================================

_COLS = ["meeting_date", "kind", "presenter_initial", "presenter_name",
         "nas_folder", "filename", "nas_path", "file_format",
         "schedule_order", "schedule_type", "matched_schedule", "source",
         "raw_jsonb"]


def _insert_sql(sch: str) -> str:
    return f"""
        INSERT INTO {sch}.archive_meeting_materials
          (meeting_date,kind,presenter_initial,presenter_name,nas_folder,
           filename,nas_path,file_format,schedule_order,schedule_type,
           matched_schedule,source,raw_jsonb)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
        ON CONFLICT (nas_path) DO UPDATE SET
          meeting_date=EXCLUDED.meeting_date, kind=EXCLUDED.kind,
          presenter_initial=EXCLUDED.presenter_initial,
          presenter_name=EXCLUDED.presenter_name,
          nas_folder=EXCLUDED.nas_folder, filename=EXCLUDED.filename,
          file_format=EXCLUDED.file_format,
          schedule_order=EXCLUDED.schedule_order,
          schedule_type=EXCLUDED.schedule_type,
          matched_schedule=EXCLUDED.matched_schedule,
          source=EXCLUDED.source, ingested_at=now(),
          raw_jsonb=EXCLUDED.raw_jsonb
    """


def _row_tuple(r: dict) -> tuple:
    return (
        r["meeting_date"], r["kind"], r["presenter_initial"],
        r["presenter_name"], r["nas_folder"], r["filename"], r["nas_path"],
        r["file_format"], r["schedule_order"], r["schedule_type"],
        bool(r["matched_schedule"]), r["source"],
        json.dumps(r.get("raw_jsonb") or {}, ensure_ascii=False),
    )


def upsert(rows: list, sch: str) -> int:
    from _db import exec_many
    return exec_many(_insert_sql(sch), [_row_tuple(r) for r in rows])


# ===========================================================================
# Preview
# ===========================================================================

def _write_preview(rows: list, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _skip_reason(entry: str) -> str:
    return entry.rsplit(" — ", 1)[-1] if " — " in entry else "other"


def _print_preview(rows: list, skipped_by_dir: dict, existing: set,
                   db_status: str, show_skipped: bool = False) -> None:
    by_date: dict = {}
    for r in rows:
        by_date.setdefault(r["meeting_date"], []).append(r)
    for d in sorted(by_date):
        first = by_date[d][0]["nas_folder"]
        print(f"\n=== {d} ({first}) ===")
        for r in by_date[d]:
            tag = "+" if (db_status == "ok" and r["nas_path"] not in existing) \
                else ("~" if db_status == "ok" else " ")
            pres = r["presenter_initial"] or r["presenter_name"] or "-"
            sched = ""
            if r["matched_schedule"]:
                sched = f" sched#{r['schedule_order']}/{r['schedule_type']}"
            print(f"  {tag} [{r['kind']:9}] {pres:<8} {r['filename']}{sched}")
    n_pb = sum(1 for r in rows if r["kind"] == "pb")
    n_grm = sum(1 for r in rows if r["kind"] == "grm")
    n_focus = sum(1 for r in rows if r["kind"] == "focus_grm")
    n_unmatched = sum(1 for r in rows if r["kind"] in ("grm", "focus_grm")
                      and not r["presenter_initial"])
    n_pb_named = sum(1 for r in rows if r["kind"] == "pb"
                     and (r["presenter_initial"] or r["presenter_name"]))
    skipped_total = sum(len(v) for v in skipped_by_dir.values())
    print(f"\n[grm] {len(rows)} material(s): {n_pb} pb ({n_pb_named} with a "
          f"presenter) · {n_grm} grm · {n_focus} focus_grm · {n_unmatched} grm "
          f"without a known presenter initial · {skipped_total} skipped.")
    if skipped_total:
        hist: dict = {}
        for entries in skipped_by_dir.values():
            for e in entries:
                hist[_skip_reason(e)] = hist.get(_skip_reason(e), 0) + 1
        parts = " · ".join(f"{k}: {v}" for k, v in
                           sorted(hist.items(), key=lambda kv: -kv[1]))
        print(f"[grm] skipped by reason — {parts}")
        if show_skipped:
            for d in sorted(skipped_by_dir):
                for e in skipped_by_dir[d]:
                    print(f"    - {d}: {e}")
    if db_status == "ok":
        new = sum(1 for r in rows if r["nas_path"] not in existing)
        print(f"[grm] DB diff: {new} new · {len(rows) - new} existing "
              f"(would upsert).")
    elif db_status == "offline":
        print("[grm] DB offline — precheck/diff skipped (dry-run still valid).")


# ===========================================================================
# Offline self-test — REAL filenames observed on the share (batch02 evidence:
# state/archive/_explore/batch02/{N1-paperblitz,N2-grm}.md + nas_catalog.json).
# No NAS, no DB, no network. `--self-test`.
# ===========================================================================

# (filename, dir_kind, expected kind, expected initial, expected name-or-None)
_SELF_TEST: tuple = (
    # --- GRM, initial-first (the classic form) -----------------------------
    ("BYL_260506.pptx", None, "grm", "BYL", "이보연"),
    ("JYK_260701.pdf", None, "grm", "JYK", "김정예"),
    ("JHR_260318.key", None, "grm", "JHR", "류주형"),
    ("JSL_260407.key", None, "grm", "JSL", "임재섭"),
    ("JSL_240422.pptx", None, "grm", "JSL", "임재섭"),
    # --- GRM, the four shapes the anchored regex used to DROP --------------
    ("260715_JOP.pptx", None, "grm", "JOP", "박준오"),        # date FIRST
    ("JOP_260325_part1.pptx", None, "grm", "JOP", "박준오"),  # suffix after date
    ("JOP_260325_Part3.pptx", None, "grm", "JOP", "박준오"),
    ("MSY_260422_MSY.key", None, "grm", "MSY", "여민수"),     # trailing repeat
    ("260408 GRM_MSY.pptx", None, "grm", "MSY", "여민수"),
    # --- GRM by token, incl. camel 'specialGRM' and the Korean-name case ---
    ("260610 GRM.pptx", None, "grm", None, None),
    ("BYL_specialGRM.pptx", None, "grm", "BYL", "이보연"),
    ("BYL_GRM(26.05.06).pptx", None, "grm", "BYL", "이보연"),
    ("MinJin_260526.pdf", None, "grm", "MJC", "최민진"),      # alias
    ("special_GRM_정새미_2026.04.08.pdf", None, "grm", "SMJ", "정새미"),
    # --- the three invented presenters that must now be NULL / dropped -----
    ("Special_GRM_Abstract_2026-04-22.pdf", None, "grm", None, None),
    ("260311_AIGRM_deepprep.key", None, None, None, None),
    # --- PB, lab-wide bundles (presenter stays NULL) -----------------------
    ("PB_260715.pptx", None, "pb", None, None),
    ("PB_20260527.pptx", None, "pb", None, None),
    ("PB.pptx", None, "pb", None, None),
    ("PB (1).pptx", None, "pb", None, None),
    ("Paper Blitz (1) 2.pptx", None, "pb", None, None),
    ("Paper Blitz_0107.pptx", None, "pb", None, None),
    # --- PB *with* a presenter — the 122/267 the old code threw away ------
    ("MJC_PB_260107.pdf", None, "pb", "MJC", "최민진"),
    ("JOP_PB260429.pdf", None, "pb", "JOP", "박준오"),
    ("JOP_AI_Blitz_260204.pdf", None, "pb", "JOP", "박준오"),
    ("260506_PaperBlitz_SK_Carricarte2025.pdf", None, "pb", "SK", "김성제"),
    ("MSY_PB_250924.pptx", None, "pb", "MSY", "여민수"),
    ("PB_250716_BHL.pptx", None, "pb", "BHL", "이보현"),
    ("PB250716_JOP.pptx", None, "pb", "JOP", "박준오"),
    ("Blitz_SMJ_2025.07.16.pptx", None, "pb", "SMJ", "정새미"),
    ("Paper blitz_HOJ_250716.pptx", None, "pb", "HOJ", "정하옴"),
    ("paper blitz_정하옴_0702.pptx", None, "pb", "HOJ", "정하옴"),
    ("PB_HJH_250716.pptx", None, "pb", "HJH", "황현주"),
    ("MJC_250716_PB.key", None, "pb", "MJC", "최민진"),
    # --- nested 'Paper Blitz/' folder context (2025 backfill) -------------
    ("250716.pptx", "pb", "pb", None, None),
    ("BYL_ NiStocker(2025.09.24).pptx", "pb", "pb", "BYL", "이보연"),
    ("SYJ_250625.pptx", "pb", "pb", "SYJ", "조수영"),
    ("MJC_SchurginWixtedBrady2020.pdf", "pb", "pb", "MJC", "최민진"),
    ("JYK_DeepPrep.pptx", "pb", "pb", "JYK", "김정예"),
    # --- NOT material: reports, workshops, papers, manuscripts -------------
    ("260408_중견_JYK.pptx", None, None, None, None),
    ("MJC_260407_연차보고서.pdf", None, None, None, None),
    ("JHR중견연차보고서2026.pdf", None, None, None, None),
    ("26년도 연차보고서_김성제.docx", None, None, None, None),
    ("250225_AIWorkshop.pptx", None, None, None, None),
    ("260224_RAG_NotebookLM.pptx", None, None, None, None),
    ("260311_MCP.pptx", None, None, None, None),
    ("KimEtal_Merged_20Apr2026.pdf", None, None, None, None),
    ("Gu 등 - 2025 - Attractor dynamics of working memory explain.pdf",
     None, None, None, None),
    # --- SYNTHETIC (the only one): a not-yet-catalogued member must still be
    #     discovered as a name-only presenter, so the catalog can lag reality.
    ("XYZ_260701.pptx", None, "grm", None, "XYZ"),
)

# entries that must be ignored before classification even runs
_SELF_TEST_IGNORED = (
    "~$Paper Blitz_0107.pptx", "~$BYL_GRM(26.05.06).pptx", ".DS_Store",
    "Thumbs.db", "._2010_Older_Animals.pdf",
)


def run_self_test(verbose: bool = False) -> int:
    fails = []
    if not load_catalog():
        # Every expectation below is catalog-backed (people, aliases, Korean
        # names, noise tokens). Say so once instead of emitting a wall of
        # confusing per-case diffs.
        print(f"[grm] self-test cannot run: {CATALOG_PATH} is missing or "
              f"unreadable. The assertions are catalog-backed; the ingest "
              f"itself still runs on built-in fallbacks (weaker matching: "
              f"catalog-only people resolve to name-only presenters).",
              file=sys.stderr)
        return 1
    for nm, dk, want_kind, want_init, want_name in _SELF_TEST:
        got = classify(nm, dk)
        exp = (want_kind, want_init, want_name)
        if got != exp:
            fails.append(f"  {nm!r} (dir_kind={dk!r})\n      expected {exp}\n"
                         f"      got      {got}")
        elif verbose:
            print(f"  ok  {got[0] or '-':9} {str(got[1] or got[2] or '-'):8} {nm}")
    for nm in _SELF_TEST_IGNORED:
        if not _ignore_reason(nm):
            fails.append(f"  {nm!r} should have been ignored before classify()")
    # the catalog itself must be present and non-degenerate
    if not presenter_index():
        fails.append("  presenter_index() is EMPTY — nas_catalog.json missing?")
    for junk in ("AIGRM", "SPECIAL", "PART", "BLITZ"):
        if junk in presenter_index():
            fails.append(f"  {junk!r} resolves to a presenter — noise leak")
    n = len(_SELF_TEST) + len(_SELF_TEST_IGNORED)
    if fails:
        print(f"[grm] self-test FAILED — {len(fails)} of {n} case(s):")
        print("\n".join(fails))
        return 1
    print(f"[grm] self-test OK — {n} real NAS filenames classified as "
          f"expected ({len(presenter_index())} presenter keys from "
          f"{CATALOG_PATH.name}).")
    return 0


# ===========================================================================
# CLI
# ===========================================================================

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weeks", type=int, default=4,
                    help="catch-up window: scan folders within the last N weeks "
                         "(default 4). Ignored when --date is given.")
    ap.add_argument("--date", metavar="YYYYMMDD", default=None,
                    help="scan one specific folder only.")
    ap.add_argument("--no-notion", action="store_true",
                    help="skip the Notion schedule enrich entirely.")
    ap.add_argument("--show-skipped", action="store_true",
                    help="list every skipped entry with its reason.")
    ap.add_argument("--self-test", action="store_true",
                    help="offline classification assertions over real observed "
                         "filenames (no NAS, no DB); exit 1 on failure.")
    ap.add_argument("--out", metavar="PATH", default=str(_DEFAULT_OUT),
                    help="dry-run preview JSONL path "
                         "(default state/archive/_tmp/grm_materials_preview.jsonl).")
    ap.add_argument("--apply", action="store_true",
                    help="UPSERT into csnl_paper_rec.archive_meeting_materials "
                         "(operator — needs .env DB creds + the migrated table).")
    args = ap.parse_args()

    if args.self_test:
        return run_self_test(verbose=args.show_skipped)

    # ---- scan (NAS read-only) ----
    dirs = _target_dirs(args)
    rows: list = []
    skipped_by_dir: dict = {}
    for d, path in dirs:
        r, sk = _scan_folder(path, d)
        rows.extend(r)
        if sk:
            skipped_by_dir[path.name] = sk

    # ---- Notion enrich (optional · graceful) ----
    if rows and not args.no_notion:
        enrich_with_schedule(rows)

    # ---- DB precheck (only when reachable) ----
    db_status, sch = _db_status()

    # ---- apply (operator) ----
    if args.apply:
        if db_status == "missing":
            print("[grm] archive_meeting_materials is missing — run the "
                  "migration first:", file=sys.stderr)
            print(f"      {_MIGRATION_HINT}", file=sys.stderr)
            return 2
        if db_status == "offline":
            print("[grm] --apply needs a DB connection but none is reachable "
                  "(check .env SUPABASE_DB_*).", file=sys.stderr)
            return 2
        if not rows:
            # Distinguish a transient NAS unmount from a genuinely-empty (but
            # mounted) folder. If the share is not mounted there is NOTHING to
            # ingest yet — return a distinct non-zero so the weekly wrapper does
            # NOT mark the week done and retries on the next boot/fire instead
            # of silently burning the week (adversarial finding #1).
            if not NAS_BASE.exists():
                print(f"[grm] NAS not mounted ({NAS_BASE}) — cannot apply. "
                      f"Returning rc=3 so the weekly wrapper retries (week NOT "
                      f"marked done). Scanned /Volumes for a readable "
                      f"*/GRM/{NAS_YEAR}; mount the share as this user (another "
                      f"user's mount is mode-700), or set GRM_NAS_BASE.",
                      file=sys.stderr)
                return 3
            print("[grm] no materials found to apply (folder(s) mounted but "
                  "genuinely empty).")
            return 0
        n = upsert(rows, sch or "csnl_paper_rec")
        print(f"[grm] UPSERT complete: {n} row(s) into "
              f"{sch}.archive_meeting_materials.")
        return 0

    # ---- dry-run (default): preview + JSONL only, ZERO DB writes ----
    existing: set = set()
    if db_status == "ok" and sch:
        existing = _existing_paths(sch, {r["meeting_date"] for r in rows})
    _print_preview(rows, skipped_by_dir, existing, db_status,
                   show_skipped=args.show_skipped)
    out_path = Path(args.out)
    _write_preview(rows, out_path)
    print(f"\n[grm] dry-run only — wrote preview ({len(rows)} rows) → "
          f"{out_path}. No DB writes; NAS untouched. Re-run with --apply "
          f"(operator) to upsert.")
    if db_status == "missing":
        print(f"[grm] NOTE: archive_meeting_materials not present yet — "
              f"migrate before --apply:\n      {_MIGRATION_HINT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
