#!/usr/bin/env python3
"""
scripts/weekly/check_materials.py — weekly NAS material-completeness check.

Cross-references what the CALENDAR says happened against what is actually on the
NAS, and (optionally) sends each member a friendly note about anything missing.

WHY THIS FILE IS CATALOG-DRIVEN
  This script emails researchers, so a false "자료 없음" is a real-world harm.
  The lab's filenames are inconsistent by nature, and the previous rule — "does
  the bare yymmdd token appear anywhere in the filename?" — matched only
  83 / 122 real milestone-meeting days (BYL 0/33, SMJ 1/7).  A `--send` would
  have told BYL that all 33 of her decks were missing.

  The fix is NOT a bigger regex in this file.  Observed reality lives as DATA in
  `config/nas_catalog.json` (patterns + counterexamples + coverage + confidence);
  this file matches TOLERANTLY against that catalog.  A new naming convention is
  a catalog edit, never a code change.
      catalog blocks consumed here:
        unicode / walk_rules.normalize          -> NFC before every comparison
        ignore_rules.lock_stubs                 -> '~$' 165-byte Office stubs
        ignore_rules.dotfiles                   -> .DS_Store / ._AppleDouble / Thumbs.db
        ignore_rules.extension_allow_list       -> .pptx .ppt .pdf .key .docx
        directories.mm.path_template            -> where MM decks live
        directories.mm.date_token_forms         -> ORDERED date parser (4 forms)
        directories.mm.tolerance_knob.tol_days  -> calendar/filename slack
        directories.grm_current.path_template   -> GRM/{YYYY}/{YYYYMMDD}/
        directories.grm_archive.path_template   -> GRM/GRM Archive/{YYYY}_GRM/…
        directories.grm_archive.date_dir_forms  -> ORDERED day-folder parser
        initials.<INIT>.dirs.mm / .aliases      -> per-person folder + alias
        matching_policy.*                       -> tokenise, never anchor

  FAIL CLOSED: if the catalog is missing, unreadable or malformed the script
  reports the error and sends NOTHING.  It never falls back to the broken rule.

Rules (docs/CSNL-INFO.md §3a/§3b — narrative; the catalog is the machine view):
  * a `Meeting: [INIT]` (MM) event  -> MM/{INIT}/ must hold a deck for that date
  * a GRM (Wed lab meeting) event   -> the day folder must hold BOTH a GRM-kind
                                       file AND a PB-kind file.  Both GRM year
                                       conventions are searched (GRM/{YYYY}/ and
                                       'GRM Archive/{YYYY}_GRM/'), so a 2025
                                       lookback no longer reports 100% missing.

TONE (operator directive): this is NOT rule enforcement. The note says
"…에 옮겨주시면 csnl-on-ai 프로젝트에 도움이 됩니다" — never "must" / "규정".

BOUNDARIES
  * Postgres is READ-ONLY here (calendar truth from csnl_ops). No writes.
  * The NAS is read-only; nothing is moved or created on the share. No mounting,
    no re-authentication (the NAS auto-bans repeated logins) — resolve at runtime.
  * Researcher-facing send is gated: dry-run prints the notes; --send is explicit.
  * At most ONE send per ISO week (state/materials_last_notified_week), so a
    retry/boot loop can never turn into repeat mail.
  * Paper Blitz gaps are ADVISORY by default (never emailed): the catalog
    measures PB as a lab-wide bundle with no presenter in 24/33 files, and 7 of
    26 weeks in 2026 have no PB file at all.  --include-pb opts in.

USAGE
    python3 scripts/weekly/check_materials.py                    # dry-run report
    python3 scripts/weekly/check_materials.py --events-json f.json  # offline fixture
    python3 scripts/weekly/check_materials.py --audit            # verify vs the share
    python3 scripts/weekly/check_materials.py --send             # operator-gated
    python3 scripts/weekly/check_materials.py --send --to-self   # smoke test

EXIT CODES
    0 ok · 2 send blocked (SMTP) · 3 NAS not reachable · 4 catalog fail-closed
    5 audit assertion failed
"""
from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import ssl
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable, Optional

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
sys.path.insert(0, str(_HERE))

# Reuse, do not duplicate: the mount resolver already lives in the ingest module.
# (classify() is deliberately NOT reused — its substring 'grm' test turns
#  '260311_AIGRM_deepprep.key' into a GRM deck, which would mask a true gap.)
from ingest_grm_nas import _resolve_nas_base  # noqa: E402

INFO_DOC = _REPO_ROOT / "docs" / "CSNL-INFO.md"
CATALOG = Path(os.environ.get("NAS_CATALOG") or (_REPO_ROOT / "config" / "nas_catalog.json"))
WEEK_STAMP = _REPO_ROOT / "state" / "materials_last_notified_week"
DEFAULT_LOOKBACK_WEEKS = 4

EXIT_SMTP = 2
EXIT_NO_NAS = 3
EXIT_CATALOG = 4
EXIT_AUDIT = 5


# ====================================================================== catalog
class CatalogError(Exception):
    """The catalog is missing/unreadable/malformed → fail closed, send nothing."""


def _nfc(s: str) -> str:
    """NFC-normalise. The share stores NFD; comparing raw silently misses every
    diacritic and every Korean name (catalog `unicode.rule`)."""
    return unicodedata.normalize("NFC", s)


def _req(node, *path, kind=None):
    """Fetch catalog['a']['b'] or raise CatalogError naming the exact key."""
    cur, seen = node, []
    for key in path:
        seen.append(key)
        if not isinstance(cur, dict) or key not in cur:
            raise CatalogError(f"missing key: {'.'.join(seen)}")
        cur = cur[key]
    if kind is not None and not isinstance(cur, kind):
        raise CatalogError(f"{'.'.join(seen)} must be {kind.__name__}")
    return cur


# 'yy, m, d (+2000)' -> ('y2', 'm', 'd').  Data, so a new form is a catalog edit.
_ORDER_FIELD = {"y": "y4", "yyyy": "y4", "year": "y4",
                "yy": "y2", "m": "m", "mm": "m", "month": "m",
                "d": "d", "dd": "d", "day": "d"}


@dataclass(frozen=True)
class DateForm:
    """One observed filename date convention, compiled from the catalog."""
    id: str
    rx: re.Pattern
    fields: tuple[str, ...]      # e.g. ('y4','m','d') / ('m','d','y4') / ('y2','m','d')


def _compile_date_forms(forms: list) -> list[DateForm]:
    out: list[DateForm] = []
    for i, f in enumerate(forms):
        if not isinstance(f, dict):
            raise CatalogError(f"date_token_forms.forms[{i}] must be an object")
        fid = str(f.get("id") or f"form{i}")
        raw = f.get("regex")
        order = f.get("order")
        if not raw or not order:
            raise CatalogError(f"date form '{fid}' needs both 'regex' and 'order'")
        try:
            rx = re.compile(raw)
        except re.error as e:
            raise CatalogError(f"date form '{fid}' regex does not compile: {e}")
        if rx.groups != 3:
            raise CatalogError(f"date form '{fid}' must capture exactly 3 groups "
                               f"(got {rx.groups})")
        try:
            fields = tuple(_ORDER_FIELD[p.strip().split()[0].strip().lower()]
                           for p in str(order).split(","))
        except (KeyError, IndexError):
            raise CatalogError(f"date form '{fid}' has an unreadable order: {order!r}")
        if len(fields) != 3 or "m" not in fields or "d" not in fields \
                or not ({"y4", "y2"} & set(fields)):
            raise CatalogError(f"date form '{fid}' order must name a year, a month "
                               f"and a day: {order!r}")
        out.append(DateForm(fid, rx, fields))
    if not out:
        raise CatalogError("date_token_forms.forms is empty")
    return out


def _compile_dir_forms(forms: list) -> list[re.Pattern]:
    """Day-folder name patterns. Catalog strings may carry a trailing human
    comment ('^(\\d{8})_.*$  (suffix: 20250122_gGRM)') — split it off."""
    out = []
    for i, raw in enumerate(forms):
        if not isinstance(raw, str):
            raise CatalogError(f"date_dir_forms.forms[{i}] must be a string")
        pat = re.split(r"\s{2,}", raw.strip(), maxsplit=1)[0].strip()
        try:
            rx = re.compile(pat)
        except re.error as e:
            raise CatalogError(f"date_dir_forms.forms[{i}] does not compile: {e}")
        if rx.groups not in (1, 3):
            raise CatalogError(f"date_dir_forms.forms[{i}] must capture 1 (YYYYMMDD) "
                               f"or 3 (Y,M,D) groups")
        out.append(rx)
    if not out:
        raise CatalogError("grm_archive.date_dir_forms.forms is empty")
    return out


@dataclass
class Catalog:
    """Typed, validated view of config/nas_catalog.json. Construction raises
    CatalogError on anything malformed — the caller then sends nothing."""
    path: Path
    raw: dict
    version: object
    measured_at: str
    lock_prefixes: tuple[str, ...]
    dot_prefixes: tuple[str, ...]
    junk_names: frozenset
    allow_ext: frozenset
    mm_forms: list[DateForm]
    mm_root_rel: str
    mm_dir_template: str
    tol_days: int
    grm_parent_templates: list[tuple[str, str]]     # (label, 'GRM/{YYYY}' style)
    grm_dir_forms: list[re.Pattern]
    initials: dict
    kind_patterns: dict[str, list[re.Pattern]]
    notes: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, path: Path = CATALOG) -> "Catalog":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise CatalogError(f"not found: {path}")
        except OSError as e:
            raise CatalogError(f"unreadable ({e.__class__.__name__}): {path}")
        except json.JSONDecodeError as e:
            raise CatalogError(f"invalid JSON in {path}: {e}")
        if not isinstance(raw, dict):
            raise CatalogError(f"{path} must contain a JSON object")

        notes: list[str] = []

        ign = _req(raw, "ignore_rules", kind=dict)
        lock = str(_req(ign, "lock_stubs", "pattern_prefix"))
        dot = str(_req(ign, "dotfiles", "pattern_prefix"))
        extra = _req(ign, "dotfiles").get("extra_names") or []
        allow = _req(ign, "extension_allow_list", "allow", kind=list)
        if not allow:
            raise CatalogError("ignore_rules.extension_allow_list.allow is empty")

        mm = _req(raw, "directories", "mm", kind=dict)
        mm_forms = _compile_date_forms(_req(mm, "date_token_forms", "forms", kind=list))
        mm_tpl = str(_req(mm, "path_template")).strip("/")
        if "{INIT}" not in mm_tpl:
            raise CatalogError("directories.mm.path_template must contain {INIT}")
        mm_root_rel = mm_tpl.split("{INIT}")[0].strip("/")
        tol = _req(mm, "tolerance_knob").get("tol_days", 0)
        try:
            tol = int(tol)
        except (TypeError, ValueError):
            raise CatalogError(f"directories.mm.tolerance_knob.tol_days not an int: {tol!r}")
        if not 0 <= tol <= 7:
            raise CatalogError(f"tol_days out of sane range 0..7: {tol}")

        # BOTH year conventions. Each path_template's last segment is the day
        # folder; everything before it (with {YYYY} filled in) is the year root.
        parents: list[tuple[str, str]] = []
        for label in ("grm_current", "grm_archive"):
            tpl = str(_req(raw, "directories", label, "path_template")).strip("/")
            parent = tpl.rsplit("/", 1)[0]
            leftover = set(re.findall(r"\{([A-Za-z_]+)\}", parent)) - {"YYYY"}
            if leftover:
                raise CatalogError(f"directories.{label}.path_template has "
                                   f"unsupported placeholder(s) {sorted(leftover)}")
            parents.append((label, parent))
        grm_dir_forms = _compile_dir_forms(
            _req(raw, "directories", "grm_archive", "date_dir_forms", "forms", kind=list))

        inits = _req(raw, "initials", kind=dict)

        # kind detection: catalog-overridable, tolerant defaults otherwise.
        kd = (_req(raw, "directories", "grm_current").get("kind_patterns")
              if isinstance(raw.get("directories", {}).get("grm_current"), dict) else None)
        if isinstance(kd, dict) and kd:
            kinds: dict[str, list[re.Pattern]] = {}
            for k, pats in kd.items():
                try:
                    kinds[str(k).lower()] = [re.compile(p, re.IGNORECASE) for p in pats]
                except (re.error, TypeError) as e:
                    raise CatalogError(f"grm_current.kind_patterns[{k}] invalid: {e}")
        else:
            # Whole-token only: 'AIGRM' must NOT read as grm, 'JOP_PB260429' must.
            kinds = {
                "grm": [re.compile(r"(?<![A-Za-z])grm(?![A-Za-z])", re.IGNORECASE)],
                "pb": [re.compile(r"(?<![A-Za-z])pb(?![A-Za-z])", re.IGNORECASE),
                       re.compile(r"(?<![A-Za-z])blitz(?![A-Za-z])", re.IGNORECASE),
                       re.compile(r"paper\s*blitz", re.IGNORECASE)],
            }
            notes.append("grm_current.kind_patterns absent from the catalog — using "
                         "built-in whole-token defaults (add the block to override)")

        norm = str(_req(raw, "walk_rules").get("normalize", "NFC")).upper()
        if norm != "NFC":
            notes.append(f"walk_rules.normalize={norm!r}; this checker only implements NFC")

        return cls(
            path=path, raw=raw, version=raw.get("version"),
            measured_at=str(raw.get("measured_at") or "?"),
            lock_prefixes=(lock,), dot_prefixes=(dot,),
            junk_names=frozenset(_nfc(str(n)).casefold() for n in extra),
            allow_ext=frozenset(str(e).lower() if str(e).startswith(".")
                                else "." + str(e).lower() for e in allow),
            mm_forms=mm_forms, mm_root_rel=mm_root_rel, mm_dir_template=mm_tpl,
            tol_days=tol, grm_parent_templates=parents, grm_dir_forms=grm_dir_forms,
            initials={k: v for k, v in inits.items() if not k.startswith("_")},
            kind_patterns=kinds, notes=notes,
        )

    # -------------------------------------------------------------- filtering
    def junk_reason(self, name: str) -> Optional[str]:
        """Why this entry is NOT material, or None if it is a candidate deck.

        Order matters: a '~$…' lock stub is 165 bytes of nothing, and a
        substring rule happily reports the deck it shadows as 'present' —
        catalog records an ORPHANED stub whose real deck no longer exists."""
        n = _nfc(name)
        if any(n.startswith(p) for p in self.lock_prefixes):
            return "lock_stub"
        if any(n.startswith(p) for p in self.dot_prefixes):
            return "dotfile"
        if n.casefold() in self.junk_names:
            return "os_metadata"
        if ("." + n.rsplit(".", 1)[-1].lower()) not in self.allow_ext or "." not in n:
            return "ext_not_allowed"
        return None

    # ------------------------------------------------------------ date tokens
    def file_dates(self, name: str) -> set[date]:
        """Every date encoded in a filename, using the catalog's ORDERED forms.

        First form that yields anything wins, so '2025.08.14' is never re-read
        as YY.MM.DD. Guards in the catalog regexes ((?<!\\d)…(?!\\d) + a 20\\d{2}
        anchor) are what stop 'TaylorBays_JNeurosci2018' and 'VSSpos+7T'."""
        n = _nfc(name)
        for form in self.mm_forms:
            found: set[date] = set()
            for m in form.rx.finditer(n):
                vals = {f: int(g) for f, g in zip(form.fields, m.groups())}
                y = vals.get("y4", 2000 + vals["y2"] if "y2" in vals else None)
                if y is None:
                    continue
                try:
                    found.add(date(y, vals["m"], vals["d"]))
                except ValueError:
                    continue        # 13th month etc. — a false token, not a date
            if found:
                return found
        return set()

    # ------------------------------------------------------------ per-initial
    def mm_dir_candidates(self, initial: str) -> list[str]:
        """Relative MM folder candidates for one initial, catalog first."""
        out: list[str] = []
        rec = self.initials.get(initial.upper()) or {}
        d = ((rec.get("dirs") or {}) if isinstance(rec, dict) else {}).get("mm")
        if isinstance(d, str) and d.strip():
            out.append(_nfc(d.split(" (")[0].strip().strip("/")))
        tpl = _nfc(self.mm_dir_template.replace("{INIT}", initial.upper()))
        if tpl not in out:
            out.append(tpl)
        return out

    def aliases(self, initial: str) -> list[str]:
        rec = self.initials.get(initial.upper()) or {}
        al = rec.get("aliases") if isinstance(rec, dict) else None
        return [_nfc(str(a)) for a in (al or [])]

    def known_tokens(self) -> set[str]:
        """Every initial + alias, casefolded — for whole-token attribution."""
        toks = set()
        for init, rec in self.initials.items():
            toks.add(_nfc(init).casefold())
            if isinstance(rec, dict):
                for a in rec.get("aliases") or []:
                    toks.add(_nfc(str(a)).casefold())
        return toks

    # ------------------------------------------------------------------ kinds
    def kinds_of(self, name: str, known: Optional[set[str]] = None) -> set[str]:
        """{'grm'} / {'pb'} / both / empty, from a NON-junk filename."""
        n = _nfc(name)
        out = {k for k, pats in self.kind_patterns.items()
               if any(p.search(n) for p in pats)}
        if not out and self.file_dates(n) and (known is not None):
            # 'JYK_260701.pdf' / '260715_JOP.pptx' — a dated deck carrying a known
            # initial is a GRM deck even with no kind token in the name.
            for tok in re.split(r"[^0-9A-Za-z가-힣]+", n.rsplit(".", 1)[0]):
                if tok and tok.casefold() in known:
                    out.add("grm")
                    break
        return out


# --------------------------------------------------------------- mount lookup
def resolve_share_root(volumes_root: str = "/Volumes") -> tuple[Path, str]:
    """Locate the share ROOT (the directory holding both GRM/ and MM/).

    Catalog `mount.resolve_rule`: never hardcode a volume name — macOS mounts
    SMB per-user mode-700 and appends '-1'/'-2' by mount order.  Never mount and
    never re-authenticate (the NAS auto-bans repeated logins): if nothing
    qualifies we fail loudly instead."""
    env = os.environ.get("CSNL_NAS_ROOT")
    if env:
        return Path(env), "CSNL_NAS_ROOT"
    base = os.environ.get("GRM_NAS_BASE")
    if base:
        # GRM_NAS_BASE points at .../GRM/<year>
        return Path(base).parent.parent, "GRM_NAS_BASE"
    try:
        volumes = sorted(Path(volumes_root).iterdir())
    except OSError:
        volumes = []
    for vol in volumes:
        try:
            if all((vol / d).is_dir() and os.access(vol / d, os.R_OK | os.X_OK)
                   for d in ("GRM", "MM")):
                return vol, "probe"
        except OSError:
            continue            # another user's mode-700 mount -> EACCES; skip
    # Legacy shape, so the "not reachable" message still names something concrete.
    return _resolve_nas_base(str(datetime.now().year)).parent.parent, "legacy-fallback"


# ------------------------------------------------------------------- gap model
@dataclass
class Gap:
    initial: str
    kind: str                 # 'mm' | 'grm' | 'pb'
    meeting_date: str         # YYYY-MM-DD
    expected_dir: str
    detail: str
    advisory: bool = False    # advisory gaps are reported but never emailed


@dataclass
class Report:
    window_start: str
    window_end: str
    share_root: str
    share_how: str
    nas_mounted: bool
    catalog: str = ""
    catalog_version: object = None
    catalog_measured_at: str = ""
    gaps: list[Gap] = field(default_factory=list)
    checked_mm: int = 0
    checked_grm: int = 0
    warnings: list[str] = field(default_factory=list)

    def by_person(self, include_advisory: bool = False) -> dict[str, list[Gap]]:
        out: dict[str, list[Gap]] = {}
        for g in self.gaps:
            if g.advisory and not include_advisory:
                continue
            out.setdefault(g.initial, []).append(g)
        return out


# ------------------------------------------------------------------- registry
def load_registry(doc: Path = INFO_DOC) -> dict:
    """Parse the fenced ```yaml block out of docs/CSNL-INFO.md."""
    try:
        import yaml
    except ImportError:  # pragma: no cover - environment guard
        raise SystemExit("[materials] PyYAML required: pip install --user pyyaml")
    text = doc.read_text(encoding="utf-8")
    m = re.search(r"```yaml\n(.*?)```", text, re.DOTALL)
    if not m:
        raise SystemExit(f"[materials] no ```yaml block found in {doc}")
    return yaml.safe_load(m.group(1)) or {}


def active_people(reg: dict) -> dict[str, dict]:
    """{INIT: {full_name, email, role}} for ACTIVE members only."""
    out: dict[str, dict] = {}
    for r in reg.get("researchers") or []:
        if (r.get("status") or "").lower() != "active":
            continue
        init = (r.get("initial") or "").strip().upper()
        if init:
            out[init] = r
    return out


# ------------------------------------------------------------------ NAS probes
def _listdir(p: Path) -> tuple[list[Path], Optional[str]]:
    """One bounded, non-recursive listing. Never follows symlinks into a walk."""
    try:
        return sorted(p.iterdir()), None
    except OSError as e:
        return [], e.__class__.__name__


def resolve_mm_folder(cat: Catalog, share_root: Path,
                      initial: str) -> tuple[Optional[Path], Path]:
    """(existing folder or None, the path to name in the message).

    Catalog first (`initials.<INIT>.dirs.mm`), then the path template, then a
    tolerant case-insensitive / alias match against the real MM listing."""
    cands = [share_root / c for c in cat.mm_dir_candidates(initial)]
    for c in cands:
        if c.is_dir():
            return c, c
    mm_root = share_root / cat.mm_root_rel
    wanted = {initial.casefold()} | {a.casefold() for a in cat.aliases(initial)}
    entries, _err = _listdir(mm_root)
    for e in entries:
        if e.is_dir() and _nfc(e.name).casefold() in wanted:
            return e, e
    return None, cands[0]


def mm_material_present(cat: Catalog, share_root: Path, initial: str,
                        meeting_date: str) -> tuple[bool, str]:
    """Is there a deck for this date under the researcher's MM folder?

    Catalog-driven: NFC → ignore_rules → ORDERED date_token_forms → |Δdays| ≤
    tolerance_knob.tol_days.  Never a substring test: BYL writes
    'BYL_ResearchMeeting(26.02.05).pptx' and SMJ writes three different formats
    in a seven-file folder."""
    folder, shown = resolve_mm_folder(cat, share_root, initial)
    if folder is None:
        return False, f"{shown} 폴더가 없습니다"
    target = datetime.strptime(meeting_date, "%Y-%m-%d").date()
    entries, err = _listdir(folder)
    if err:
        return False, f"{folder} 를 읽을 수 없습니다 ({err})"
    for f in entries:
        name = _nfc(f.name)
        if cat.junk_reason(name):
            continue
        try:
            if not f.is_file():
                continue
        except OSError:
            continue
        if any(abs((d - target).days) <= cat.tol_days for d in cat.file_dates(name)):
            return True, name
    return False, f"{folder} 안에 {target.isoformat()} 날짜 자료가 없습니다"


def _dir_date(cat: Catalog, name: str) -> Optional[date]:
    """Parse a day-folder name with the catalog's ORDERED date_dir_forms.
    A single ^\\d{8}$ rule makes 2022+2023 (74 folders) invisible."""
    n = _nfc(name)
    for rx in cat.grm_dir_forms:
        m = rx.match(n)
        if not m:
            continue
        g = m.groups()
        try:
            if len(g) == 1:
                return datetime.strptime(g[0], "%Y%m%d").date()
            return date(int(g[0]), int(g[1]), int(g[2]))
        except (ValueError, TypeError):
            continue
    return None


def grm_day_dirs(cat: Catalog, share_root: Path, d: date,
                 cache: dict) -> tuple[list[Path], list[str]]:
    """Every existing day folder for this date, across BOTH year conventions.

    GRM/{YYYY}/ holds 2026 only; everything older lives under
    'GRM Archive/{YYYY}_GRM/'.  Scanning just the first is why a 2025 lookback
    reported 100% missing."""
    hits: list[Path] = []
    warns: list[str] = []
    for label, parent_tpl in cat.grm_parent_templates:
        parent = share_root / parent_tpl.replace("{YYYY}", f"{d.year:04d}")
        if not parent.is_dir():
            warns.append(f"{label}: {parent} 없음")
            continue
        exact = parent / f"{d:%Y%m%d}"       # fast path, 26/26 of GRM/2026
        if exact.is_dir():
            hits.append(exact)
        key = str(parent)
        if key not in cache:
            entries, err = _listdir(parent)
            if err:
                warns.append(f"{label}: {parent} 를 읽을 수 없습니다 ({err})")
                cache[key] = {}
            else:
                idx: dict[date, list[Path]] = {}
                for e in entries:
                    try:
                        if not e.is_dir():
                            continue
                    except OSError:
                        continue
                    dd = _dir_date(cat, e.name)
                    if dd:
                        idx.setdefault(dd, []).append(e)
                cache[key] = idx
        for p in cache[key].get(d, []):
            if p not in hits:
                hits.append(p)
    return hits, warns


def grm_pb_present(cat: Catalog, day_dirs: Iterable[Path],
                   known: set[str]) -> tuple[bool, bool, list[str]]:
    """(has_grm, has_pb, problems) across every candidate day folder.

    Tolerant by design: PB appears mid-name ('JOP_PB260429.pdf'), the date can
    come first ('260715_JOP.pptx'), suffixes are everywhere ('…_part1.pptx'),
    and a presenter's slides can be a SUBDIRECTORY ('20260318/JSL_260317').

    One level of descent, and a child directory only counts when it actually
    holds a material file — otherwise the archive's Zoom recording folders
    ('2026-03-18 09.54.46 CSNL GRM', all .mp4/.m4a/.zoom) would answer 'GRM
    present' on their name alone and mask a true gap."""
    has_grm = has_pb = False
    problems: list[str] = []
    for day in day_dirs:
        entries, err = _listdir(day)
        if err:
            problems.append(f"{day} 를 읽을 수 없습니다 ({err})")
            continue
        for e in entries:
            name = _nfc(e.name)
            try:
                is_dir = e.is_dir()
            except OSError:
                continue
            if is_dir:
                if name.startswith(cat.dot_prefixes):
                    continue
                kids, kerr = _listdir(e)
                if kerr:
                    problems.append(f"{e} 를 읽을 수 없습니다 ({kerr})")
                    continue
                inner = [_nfc(k.name) for k in kids
                         if not cat.junk_reason(_nfc(k.name))]
                if not inner:
                    continue          # Zoom recording dir / empty — not material
                kinds = set(cat.kinds_of(name, known))
                for k in inner:
                    kinds |= cat.kinds_of(k, known)
                if not kinds:
                    kinds = {"grm"}   # a presenter's slide bundle, e.g. JSL_260317
                has_grm = has_grm or ("grm" in kinds)
                has_pb = has_pb or ("pb" in kinds)
                continue
            if cat.junk_reason(name):
                continue
            kinds = cat.kinds_of(name, known)
            has_grm = has_grm or ("grm" in kinds)
            has_pb = has_pb or ("pb" in kinds)
    return has_grm, has_pb, problems


# ------------------------------------------------------------------ event input
def load_events(args) -> list[dict]:
    """Calendar truth: [{kind:'mm'|'grm', date:'YYYY-MM-DD', initial:'JOP'}, ...]"""
    if args.events_json:
        return json.loads(Path(args.events_json).read_text(encoding="utf-8"))

    from _db import load_env, query_json          # local import: no DB at module load
    load_env()
    since = (date.today() - timedelta(weeks=args.weeks)).isoformat()
    events: list[dict] = []
    for row in query_json(
        "SELECT meeting_date::text AS d, researcher_initial AS i "
        f"FROM csnl_ops.milestone_meetings WHERE meeting_date >= '{since}'"
    ) or []:
        events.append({"kind": "mm", "date": row["d"], "initial": row["i"]})
    for row in query_json(
        "SELECT meeting_date::text AS d, presenter_initial AS i "
        f"FROM csnl_ops.lab_meetings WHERE meeting_date >= '{since}'"
    ) or []:
        events.append({"kind": "grm", "date": row["d"], "initial": row["i"]})
    return events


# ---------------------------------------------------------------------- checker
def build_report(events: list[dict], cat: Catalog, share_root: Path,
                 share_how: str, weeks: int, pb_advisory: bool = True) -> Report:
    end = date.today()
    start = end - timedelta(weeks=weeks)
    mounted = (share_root / cat.mm_root_rel).is_dir() or (share_root / "GRM").is_dir()
    rep = Report(
        window_start=start.isoformat(), window_end=end.isoformat(),
        share_root=str(share_root), share_how=share_how, nas_mounted=mounted,
        catalog=str(cat.path), catalog_version=cat.version,
        catalog_measured_at=cat.measured_at, warnings=list(cat.notes),
    )
    if not mounted:
        return rep

    known = cat.known_tokens()
    year_cache: dict = {}
    for ev in events:
        d, init = ev.get("date"), (ev.get("initial") or "").upper()
        if not d or not init:
            continue
        if not (start.isoformat() <= d <= end.isoformat()):
            continue

        if ev.get("kind") == "mm":
            rep.checked_mm += 1
            ok, why = mm_material_present(cat, share_root, init, d)
            if not ok:
                _folder, shown = resolve_mm_folder(cat, share_root, init)
                rep.gaps.append(Gap(init, "mm", d, str(shown), why))

        elif ev.get("kind") == "grm":
            rep.checked_grm += 1
            day = datetime.strptime(d, "%Y-%m-%d").date()
            dirs, warns = grm_day_dirs(cat, share_root, day, year_cache)
            for w in warns:
                if w not in rep.warnings:
                    rep.warnings.append(w)
            if not dirs:
                shown = share_root / cat.grm_parent_templates[0][1].replace(
                    "{YYYY}", f"{day.year:04d}") / f"{day:%Y%m%d}"
                rep.gaps.append(Gap(init, "grm", d, str(shown), f"{shown} 폴더가 없습니다"))
                continue
            has_grm, has_pb, problems = grm_pb_present(cat, dirs, known)
            shown = str(dirs[0])
            if problems:
                rep.gaps.append(Gap(init, "grm", d, shown, "; ".join(problems)))
                continue
            if not has_grm:
                rep.gaps.append(Gap(init, "grm", d, shown, "GRM 발표 자료를 찾지 못했습니다"))
            if not has_pb:
                rep.gaps.append(Gap(init, "pb", d, shown,
                                    "Paper Blitz 자료를 찾지 못했습니다",
                                    advisory=pb_advisory))
    return rep


# ------------------------------------------------------------------------ email
def compose(initial: str, person: dict, gaps: list[Gap]) -> tuple[str, str]:
    """Friendly, non-coercive note. Never demands; explains the benefit."""
    name = person.get("full_name") or initial
    lines = [
        f"안녕하세요, {name} 연구원님.",
        "",
        "최근 랩 일정과 NAS 자료를 대조하다가, 아래 자료를 찾지 못해 안내드립니다.",
        "",
    ]
    for g in sorted(gaps, key=lambda x: x.meeting_date):
        label = {"mm": "Milestone Meeting", "grm": "GRM 발표", "pb": "Paper Blitz"}[g.kind]
        lines.append(f"  · {g.meeting_date} {label} — {g.detail}")
        lines.append(f"    위치: {g.expected_dir}")
    lines += [
        "",
        "혹시 아직 올리지 않으셨다면 위 경로에 옮겨주시면 csnl-on-ai 프로젝트에 도움이 됩니다.",
        "이미 다른 곳에 정리해 두셨거나 사정이 있으셨다면 그대로 두셔도 괜찮습니다 —",
        "저장 규칙을 강제하려는 것이 아니라, 자료가 모이면 랩 전체가 검색·활용하기 좋아져서 드리는 안내입니다.",
        "",
        "감사합니다.",
    ]
    return f"[CSNL] {name} 연구원님 — 랩 자료 안내 ({len(gaps)}건)", "\n".join(lines)


def smtp_ready() -> tuple[bool, str]:
    user, pw = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASS", "")
    if not user or not pw:
        return False, "SMTP_USER / SMTP_PASS 가 .env 에 비어 있습니다 (Gmail 앱 비밀번호 필요)"
    return True, ""


def send_mail(to_addr: str, subject: str, body: str) -> None:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ["SMTP_USER"]
    pw = os.environ["SMTP_PASS"]
    msg = EmailMessage()
    msg["From"] = os.environ.get("SMTP_FROM") or user
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as s:
        s.login(user, pw)
        s.send_message(msg)


def week_already_sent(week: str) -> bool:
    try:
        return WEEK_STAMP.read_text(encoding="utf-8").strip() == week
    except OSError:
        return False


# ------------------------------------------------------------------------ audit
def audit(cat: Catalog, share_root: Path, min_ratio: float = 0.9) -> int:
    """Verify the catalog-driven matcher against the REAL share, read-only.

    Method (same as batch02/N3-mm §3): every material file's own encoded date is
    a real meeting-day; for each day ask both rules 'is material present?'.
    ~8 directory listings total. Asserts BYL and SMJ are no longer ~0."""
    mm_root = share_root / cat.mm_root_rel
    entries, err = _listdir(mm_root)
    if err or not entries:
        print(f"[audit] {mm_root} 를 읽을 수 없습니다 ({err or 'empty'})", file=sys.stderr)
        return EXIT_NO_NAS

    print(f"[audit] catalog v{cat.version} measured_at={cat.measured_at}")
    print(f"[audit] share root: {share_root}")
    print(f"[audit] MM — per-initial matched/total (old = yymmdd substring rule)\n")
    print(f"  {'INIT':<6}{'days':>6}{'old':>7}{'new':>7}   {'old%':>6}{'new%':>7}  latest")

    tot_days = tot_old = tot_new = 0
    per_init: dict[str, tuple[int, int, int]] = {}
    for folder in entries:
        try:
            if not folder.is_dir():
                continue
        except OSError:
            continue
        init = _nfc(folder.name).upper()
        files, ferr = _listdir(folder)
        if ferr:
            print(f"  {init:<6} unreadable ({ferr})")
            continue
        days: set[date] = set()
        for f in files:
            name = _nfc(f.name)
            if cat.junk_reason(name):
                continue
            try:
                if not f.is_file():
                    continue
            except OSError:
                continue
            days |= cat.file_dates(name)
        if not days:
            continue
        raw_names = [_nfc(f.name) for f in files]
        old = new = 0
        for d in sorted(days):
            token = d.strftime("%y%m%d")
            if any(token in n for n in raw_names):          # the OLD rule, verbatim
                old += 1
            ok, _why = mm_material_present(cat, share_root, init, d.isoformat())
            if ok:
                new += 1
        per_init[init] = (len(days), old, new)
        tot_days += len(days); tot_old += old; tot_new += new
        print(f"  {init:<6}{len(days):>6}{old:>7}{new:>7}   "
              f"{100*old/len(days):>5.0f}%{100*new/len(days):>6.0f}%  {max(days)}")

    print(f"  {'TOTAL':<6}{tot_days:>6}{tot_old:>7}{tot_new:>7}   "
          f"{100*tot_old/max(tot_days,1):>5.0f}%{100*tot_new/max(tot_days,1):>6.0f}%")

    # --- assertions -------------------------------------------------------
    failures: list[str] = []
    for init in ("BYL", "SMJ"):
        if init not in per_init:
            failures.append(f"{init}: no MM folder found — cannot verify the fix")
            continue
        days, old, new = per_init[init]
        if new < min_ratio * days:
            failures.append(f"{init}: new rule {new}/{days} < {min_ratio:.0%}")
        if new <= old:
            failures.append(f"{init}: new rule {new} did not improve on old {old}")
    if tot_new < tot_old:
        failures.append(f"TOTAL regressed: new {tot_new} < old {tot_old}")

    # --- GRM: both year conventions must resolve --------------------------
    print("\n[audit] GRM year conventions (day folders discovered per year)")
    for y in (2025, 2026):
        found = 0
        for label, tpl in cat.grm_parent_templates:
            parent = share_root / tpl.replace("{YYYY}", f"{y:04d}")
            if not parent.is_dir():
                print(f"  {y} {label:<12} {parent}  — 없음")
                continue
            kids, kerr = _listdir(parent)
            n = sum(1 for k in kids if k.is_dir() and _dir_date(cat, k.name))
            found += n
            print(f"  {y} {label:<12} {parent}  — {n} day folders"
                  f"{' (' + kerr + ')' if kerr else ''}")
        if found == 0:
            failures.append(f"GRM {y}: no day folder found under EITHER convention")

    # --- GRM/PB kind detection on the real 2026 day folders ---------------
    # Catalog: a steady-state normal week holds exactly one GRM deck + one PB
    # file. Those weeks must classify as BOTH, or the checker would email the
    # presenter about material that is sitting right there.
    steady = ["20260610", "20260617", "20260701", "20260708", "20260715"]
    known = cat.known_tokens()
    cache: dict = {}
    print("\n[audit] GRM/PB detection — catalog 'steady_state_normal_week' folders")
    for name in steady:
        try:
            d = datetime.strptime(name, "%Y%m%d").date()
        except ValueError:
            continue
        dirs, _w = grm_day_dirs(cat, share_root, d, cache)
        if not dirs:
            failures.append(f"GRM {name}: day folder not resolved")
            print(f"  {name}  — 폴더 없음")
            continue
        g, p, probs = grm_pb_present(cat, dirs, known)
        print(f"  {name}  grm={'Y' if g else 'N'} pb={'Y' if p else 'N'}"
              f"  ({dirs[0]}){'  ' + '; '.join(probs) if probs else ''}")
        if not (g and p):
            failures.append(f"GRM {name}: steady-state week classified "
                            f"grm={g} pb={p} — expected both")

    print()
    if failures:
        for f in failures:
            print(f"[audit] FAIL — {f}", file=sys.stderr)
        return EXIT_AUDIT
    print("[audit] PASS — catalog-driven matcher verified against the live share.")
    return 0


# ------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=DEFAULT_LOOKBACK_WEEKS)
    ap.add_argument("--events-json", default=None, help="fixture instead of the DB")
    ap.add_argument("--send", action="store_true", help="actually email (gated)")
    ap.add_argument("--to-self", action="store_true",
                    help="smoke test: route every note to SMTP_FROM/SMTP_USER")
    ap.add_argument("--force", action="store_true", help="ignore the once-per-week stamp")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--audit", action="store_true",
                    help="verify the matcher against the real share (read-only)")
    ap.add_argument("--include-pb", action="store_true",
                    help="also email Paper Blitz gaps (advisory by default: PB is a "
                         "lab-wide bundle with no presenter in 24/33 files)")
    ap.add_argument("--catalog", default=None, help="override config/nas_catalog.json")
    args = ap.parse_args()

    # FAIL CLOSED. No catalog → no matching rule we trust → nothing is sent.
    try:
        cat = Catalog.load(Path(args.catalog) if args.catalog else CATALOG)
    except CatalogError as e:
        print(f"[materials] 카탈로그를 읽을 수 없어 중단합니다 (발송 없음): {e}",
              file=sys.stderr)
        print("[materials] fix config/nas_catalog.json — this checker will NOT fall "
              "back to the yymmdd-substring rule (83/122 accuracy, BYL 0/33).",
              file=sys.stderr)
        return EXIT_CATALOG
    for n in cat.notes:
        print(f"[materials] note: {n}", file=sys.stderr)

    share_root, how = resolve_share_root()

    if args.audit:
        return audit(cat, share_root)

    if not args.events_json:
        from _db import load_env
        load_env()

    reg = load_registry()
    people = active_people(reg)
    rep = build_report(load_events(args), cat, share_root, how, args.weeks,
                       pb_advisory=not args.include_pb)

    if args.json:
        print(json.dumps({
            "window": [rep.window_start, rep.window_end],
            "share_root": rep.share_root, "resolved_by": rep.share_how,
            "nas_mounted": rep.nas_mounted,
            "catalog": {"path": rep.catalog, "version": rep.catalog_version,
                        "measured_at": rep.catalog_measured_at},
            "checked": {"mm": rep.checked_mm, "grm": rep.checked_grm},
            "warnings": rep.warnings,
            "gaps": [vars(g) for g in rep.gaps],
        }, ensure_ascii=False, indent=2))
        return 0

    if not rep.nas_mounted:
        print(f"[materials] NAS not reachable ({rep.share_root}, via {rep.share_how}) "
              f"— cannot check. Mount the share as this user or set CSNL_NAS_ROOT. "
              f"Do NOT re-authenticate: the NAS auto-bans repeated logins.",
              file=sys.stderr)
        return EXIT_NO_NAS

    advisory = [g for g in rep.gaps if g.advisory]
    print(f"[materials] window {rep.window_start}..{rep.window_end} · "
          f"catalog v{rep.catalog_version} ({rep.catalog_measured_at}) · "
          f"share {rep.share_root} [{rep.share_how}]")
    print(f"[materials] checked MM={rep.checked_mm} GRM={rep.checked_grm} · "
          f"gaps={len(rep.gaps) - len(advisory)} (+{len(advisory)} advisory)")
    for w in rep.warnings:
        print(f"[materials] warn: {w}")
    for g in sorted(advisory, key=lambda x: (x.initial, x.meeting_date)):
        print(f"[materials] advisory (not emailed) {g.initial} {g.meeting_date} "
              f"{g.kind} — {g.detail}")

    mailable = rep.by_person()
    if not mailable:
        print("[materials] 모든 자료가 제자리에 있습니다.")
        return 0

    week = date.today().strftime("%G-W%V")
    if args.send and not args.force and week_already_sent(week):
        print(f"[materials] {week} 에 이미 발송했습니다 — 주 1회 제한 (--force 로 우회)")
        return 0

    ok, why = smtp_ready()
    sent = 0
    for init, gaps in sorted(mailable.items()):
        person = people.get(init)
        if not person:
            print(f"[materials] {init}: active 명단에 없음 — 건너뜀 ({len(gaps)}건)")
            continue
        subject, body = compose(init, person, gaps)
        to_addr = (os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER")) \
            if args.to_self else person.get("email")
        if not to_addr:
            print(f"[materials] {init}: 이메일 주소 없음 — 건너뜀 ({len(gaps)}건)")
            continue

        if not args.send:
            print(f"\n--- DRY-RUN → {to_addr} ---\nSubject: {subject}\n{body}")
            continue
        if not ok:
            print(f"[materials] 발송 불가: {why}", file=sys.stderr)
            return EXIT_SMTP
        send_mail(to_addr, subject, body)
        print(f"[materials] sent → {to_addr} ({len(gaps)}건)")
        sent += 1

    if args.send and sent and not args.to_self:
        WEEK_STAMP.parent.mkdir(parents=True, exist_ok=True)
        WEEK_STAMP.write_text(week, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
