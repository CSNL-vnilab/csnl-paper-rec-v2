#!/usr/bin/env python3
"""
scripts/weekly/ingest_grm_nas.py — weekly scan of the NAS GRM/2026 folders →
csnl_paper_rec.archive_meeting_materials (one row per slide FILE).

WHAT IT DOES
  Every week (Wednesday afternoon, after the GRM ends) the lab drops the
  Paper-Blitz + Research-Meeting slide files into a dated folder on the NAS:
      /Volumes/CSNL_new-1/GRM/2026/<YYYYMMDD>/
  This script walks the recent folders, classifies each file as a Paper Blitz
  (pb) or a Research Meeting (grm / focus_grm), pulls the GRM presenter out of
  the filename, OPTIONALLY enriches presenter/order/type from the Notion
  schedule, and writes one idempotent row per file (UNIQUE(nas_path)) into the
  meeting-material index.

CLASSIFICATION (filename, case-insensitive)
  pb         : name starts with 'PB' (PB.pptx · PB (1).pptx · PB_260610.pptx)
               OR contains 'paperblitz' / 'paper blitz'
               (260506_PaperBlitz_SK_Carricarte2025.pdf). PB = the whole lab →
               presenter is NULL.
  grm        : name contains 'GRM' (BYL_GRM(26.05.06).pptx · 260520 GRM_MSY.pptx
               · MinJin_GRM260526.pdf · 260610 GRM.pptx · 260610_GRM_BHL.pdf)
               OR matches '<initials>_<date>.{pdf,pptx,key}' (JSL_260512.key).
               presenter = a known initial ([A-Z]{2,8}) → presenter_initial;
               an unknown token / roman name (MinJin) → presenter_name only,
               presenter_initial NULL, matched_schedule=false (operator/Notion
               resolves later). No presenter token (260610 GRM.pptx) → both NULL.
  focus_grm  : a grm-kind row whose meeting_date is a Notion Type='Focus GRM'
               day (only set when the Notion schedule is reachable).
  skipped    : '~$…' (Office lock), '._…' / '.…' (mac/hidden), non-files,
               and any extension outside {pdf,pptx,ppt,key}.

NOTION SCHEDULE ENRICH (optional · graceful)
  Authoritative presenter/order/type = the Notion DB 'GRM 발표 순번 리스트'
  (data source 4088bc86-a8e3-4386-8f3e-fee006563a0d, under the 'CSNL GRM' page).
  We match by date and fill presenter_name / schedule_order / schedule_type.
  The workspace integration token may NOT be shared into 'CSNL GRM' — if the
  query 403s / errors / NOTION_API_KEY is absent, we log one line and proceed
  NAS-primary (matched_schedule=false). `--no-notion` skips it entirely. See
  docs/GRM-INGEST-FLOW.md for how to share the integration into 'CSNL GRM'.

BOUNDARY
  * The NAS share is READ-ONLY — this script never writes a single byte to it.
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
  2  --apply but the migration is missing, or the DB is unreachable.
  3  --apply but the NAS share is NOT mounted (NAS_BASE absent) — distinct
     from a genuinely-empty mounted folder so the wrapper does NOT mark the
     week done and retries on the next boot/calendar fire (a transient unmount
     no longer silently burns the week).

CLI
  python3 scripts/weekly/ingest_grm_nas.py                  # DRY-RUN, last 4 weeks
  python3 scripts/weekly/ingest_grm_nas.py --weeks 8        # DRY-RUN, last 8 weeks
  python3 scripts/weekly/ingest_grm_nas.py --date 20260610  # DRY-RUN, one folder
  python3 scripts/weekly/ingest_grm_nas.py --no-notion      # skip schedule enrich
  ! python3 scripts/weekly/ingest_grm_nas.py --weeks 4 --apply   # operator write
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
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
      1. GRM_NAS_BASE — an explicit override always wins (testing, odd mounts).
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

ALLOWED_EXT = {"pdf", "pptx", "ppt", "key"}
KIND_WORDS = {"GRM", "PB", "PAPERBLITZ", "PAPER", "BLITZ"}
EXT_WORDS = {"PDF", "PPTX", "PPT", "KEY"}
# Common file-noise English tokens that are NOT a presenter name. Used only in
# the bare-name fallback so a filename like 'Review_GRM.pdf' does not coin a
# bogus presenter_name='Review'. Conservative — roman names (MinJin) are kept;
# nothing here overlaps a known initial.
NOISE_WORDS = {
    "REVIEW", "FINAL", "REVISED", "REVISION", "DRAFT", "COPY", "VER",
    "VERSION", "SLIDE", "SLIDES", "MEETING", "SEMINAR", "PRESENTATION",
    "PRESENT", "UPDATED", "UPDATE", "NEW", "OLD", "LAB", "CSNL", "RESEARCH",
    "JOURNAL", "CLUB", "TALK", "SHARE", "WEEK", "FIX", "FIXED", "MERGED",
    "TEMP", "BACKUP", "TEST",
}

_DATE_DIR_RE = re.compile(r"^\d{8}$")
_PB_PREFIX_RE = re.compile(r"^pb($|[^a-z])")          # 'PB', 'PB ', 'PB_', 'PB.'
_INITDATE_RE = re.compile(r"^[a-z]{2,8}_(?:\d{6}|\d{8})\.(?:pdf|pptx|ppt|key)$")
_ALPHA_RUN_RE = re.compile(r"[A-Za-z]{2,}")

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
# Researcher registry (init ↔ name). Light-parsed from config/researchers.yaml
# so we never hard-depend on PyYAML; falls back to the known roster.
# ===========================================================================

_FALLBACK_REGISTRY = {
    "BHL": "이보현", "BYL": "이보연", "JOP": "박준오", "JYK": "김정예",
    "MSY": "여민수", "SMJ": "정새미", "SYJ": "조수영",
    "SK": "김성제", "JSL": "임재섭",
}
_YAML_ROW_RE = re.compile(r"^\s*([A-Z]{2,8})\s*:\s*\{\s*name:\s*([^,}]+)")


def load_registry() -> dict[str, str]:
    """init → name, parsed leniently from config/researchers.yaml."""
    path = _REPO_ROOT / "config" / "researchers.yaml"
    reg: dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            m = _YAML_ROW_RE.match(raw)
            if m:
                reg[m.group(1).upper()] = m.group(2).strip().strip("'\"")
    except OSError:
        pass
    for k, v in _FALLBACK_REGISTRY.items():
        reg.setdefault(k, v)
    return reg


REGISTRY = load_registry()
KNOWN_INITIALS = set(REGISTRY)


# ===========================================================================
# Filename classification
# ===========================================================================

def _ext_of(name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    return ext.lower() if dot else ""


def _extract_presenter(name: str) -> tuple[Optional[str], Optional[str]]:
    """(presenter_initial, presenter_name) from a GRM filename. A known initial
    wins; otherwise the first non-keyword, non-noise alpha run becomes
    presenter_name with a NULL initial (an unknown all-caps token or a roman
    name like 'MinJin'). File-noise tokens (Review/Final/…) are skipped so they
    never coin a bogus presenter_name; if nothing remains both are NULL."""
    runs = _ALPHA_RUN_RE.findall(name)
    cands = [t for t in runs if t.upper() not in KIND_WORDS | EXT_WORDS]
    for t in cands:
        if t.upper() in KNOWN_INITIALS:
            return t.upper(), REGISTRY.get(t.upper())
    for t in cands:
        if t.upper() not in NOISE_WORDS:
            return None, t
    return None, None


def classify(name: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """(kind, presenter_initial, presenter_name). kind None → unclassifiable."""
    low = name.lower()
    # Paper Blitz first (a PB file may also carry an initial token like _SK_).
    if _PB_PREFIX_RE.match(low) or "paperblitz" in low.replace(" ", ""):
        return "pb", None, None          # PB = whole lab → no presenter
    if "grm" in low or _INITDATE_RE.match(low):
        init, pname = _extract_presenter(name)
        return "grm", init, pname
    return None, None, None


# ===========================================================================
# NAS scan (read-only)
# ===========================================================================

def _target_dirs(args) -> list[Path]:
    if not NAS_BASE.exists():
        print(f"[grm] NAS base not mounted: {NAS_BASE} — nothing to scan. "
              f"Scanned /Volumes for a readable */GRM/{NAS_YEAR}; another "
              f"user's mount is mode-700 and cannot be used. Mount the share "
              f"as this user, or set GRM_NAS_BASE.", file=sys.stderr)
        return []
    if args.date:
        if not _DATE_DIR_RE.match(args.date):
            print(f"[grm] --date must be YYYYMMDD (got {args.date!r})",
                  file=sys.stderr)
            return []
        d = NAS_BASE / args.date
        if not d.is_dir():
            print(f"[grm] folder not found: {d}", file=sys.stderr)
            return []
        return [d]
    cutoff = date.today() - timedelta(days=max(args.weeks, 1) * 7)
    out: list[tuple[date, Path]] = []
    for child in NAS_BASE.iterdir():
        if not child.is_dir() or not _DATE_DIR_RE.match(child.name):
            continue
        try:
            fdate = datetime.strptime(child.name, "%Y%m%d").date()
        except ValueError:
            continue
        if fdate >= cutoff:
            out.append((fdate, child))
    out.sort(key=lambda t: t[0])
    return [p for _, p in out]


def _scan_folder(folder: Path) -> tuple[list[dict], list[str]]:
    """Return (material rows, skipped filenames) for one dated folder."""
    rows: list[dict] = []
    skipped: list[str] = []
    meeting_date = folder.name                          # YYYYMMDD
    iso_date = f"{meeting_date[:4]}-{meeting_date[4:6]}-{meeting_date[6:8]}"
    nas_folder = f"{NAS_FOLDER_PREFIX}/{meeting_date}"
    for entry in sorted(folder.iterdir(), key=lambda p: p.name):
        nm = entry.name
        if nm.startswith("~$") or nm.startswith("._") or nm.startswith("."):
            skipped.append(nm)
            continue
        if not entry.is_file():
            continue
        ext = _ext_of(nm)
        if ext not in ALLOWED_EXT:
            skipped.append(nm)
            continue
        kind, p_init, p_name = classify(nm)
        if kind is None:
            skipped.append(nm)              # not PB and not GRM → cannot store
            continue
        try:
            st = entry.stat()
            size, mtime = st.st_size, int(st.st_mtime)
        except OSError:
            size, mtime = None, None
        rows.append({
            "meeting_date": iso_date,
            "kind": kind,
            "presenter_initial": p_init,
            "presenter_name": p_name,
            "nas_folder": nas_folder,
            "filename": nm,
            "nas_path": str(entry),
            "file_format": ext,
            "schedule_order": None,
            "schedule_type": None,
            "matched_schedule": False,
            "source": "nas-scan",
            "raw_jsonb": {"size": size, "mtime": mtime},
        })
    return rows, skipped


# ===========================================================================
# Notion schedule enrich (optional · graceful)
# ===========================================================================

def _fetch_schedule(dates_iso: set[str]) -> dict[str, dict]:
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
    out: dict[str, dict] = {}
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


def enrich_with_schedule(rows: list[dict]) -> int:
    """Fill schedule_* on GRM-kind rows that match a Notion date; promote
    grm→focus_grm on Focus-GRM days. Returns the number of rows enriched."""
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

def _db_status() -> tuple[str, Optional[str]]:
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


def _existing_paths(sch: str, dates_iso: set[str]) -> set[str]:
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


def upsert(rows: list[dict], sch: str) -> int:
    from _db import exec_many
    return exec_many(_insert_sql(sch), [_row_tuple(r) for r in rows])


# ===========================================================================
# Preview
# ===========================================================================

def _write_preview(rows: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _print_preview(rows: list[dict], skipped_by_dir: dict[str, list[str]],
                   existing: set[str], db_status: str) -> None:
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        by_date.setdefault(r["meeting_date"], []).append(r)
    for d in sorted(by_date):
        print(f"\n=== {d} ({NAS_FOLDER_PREFIX}/{d.replace('-', '')}) ===")
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
    skipped_total = sum(len(v) for v in skipped_by_dir.values())
    print(f"\n[grm] {len(rows)} material(s): {n_pb} pb · {n_grm} grm · "
          f"{n_focus} focus_grm · {n_unmatched} grm without a known presenter "
          f"initial · {skipped_total} skipped.")
    if db_status == "ok":
        new = sum(1 for r in rows if r["nas_path"] not in existing)
        print(f"[grm] DB diff: {new} new · {len(rows) - new} existing "
              f"(would upsert).")
    elif db_status == "offline":
        print("[grm] DB offline — precheck/diff skipped (dry-run still valid).")


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
    ap.add_argument("--out", metavar="PATH", default=str(_DEFAULT_OUT),
                    help="dry-run preview JSONL path "
                         "(default state/archive/_tmp/grm_materials_preview.jsonl).")
    ap.add_argument("--apply", action="store_true",
                    help="UPSERT into csnl_paper_rec.archive_meeting_materials "
                         "(operator — needs .env DB creds + the migrated table).")
    args = ap.parse_args()

    # ---- scan (NAS read-only) ----
    dirs = _target_dirs(args)
    rows: list[dict] = []
    skipped_by_dir: dict[str, list[str]] = {}
    for d in dirs:
        r, sk = _scan_folder(d)
        rows.extend(r)
        if sk:
            skipped_by_dir[d.name] = sk

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
    existing: set[str] = set()
    if db_status == "ok" and sch:
        existing = _existing_paths(sch, {r["meeting_date"] for r in rows})
    _print_preview(rows, skipped_by_dir, existing, db_status)
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
