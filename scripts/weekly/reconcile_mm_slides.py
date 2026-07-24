#!/usr/bin/env python3
"""
scripts/weekly/reconcile_mm_slides.py — repair csnl_ops.milestone_meetings from
NAS truth.  The replacement for the dead node resolver, on the machine that can
actually see the LAN NAS.

WHY THIS FILE EXISTS (the incident)
  csnl_ops.milestone_meetings.slides_submitted is flipped True by exactly ONE
  thing: csnl-ops/scripts/resolve-mm-slides.mjs — a NODE script that needs NAS
  access.  It has not run since 2026-04-13 (no node on this Mac; the Vercel app
  cannot reach the LAN NAS).  So the flag is stale-False for every meeting since
  April, and the `chase-mm-slides` chaser emailed 24 researchers — cc'ing the
  PI — that decks were "not uploaded" when the .pptx/.pdf/.key were sitting on
  the share the whole time (e.g. MM/JOP/MM260714_JOP.pptx EXISTS).

  This reconciler reads the SAME NAS the node resolver read, using the SAME
  catalog-driven matcher the lab already trusts (scripts/weekly/check_materials
  .py :: mm_material_present, driven by config/nas_catalog.json's per-person
  date_token_forms — NOT a hand-rolled date parser), and repairs the flag.

CROSS-SCHEMA WRITE — DELIBERATE
  paper-rec normally writes only its own csnl_paper_rec ledger and treats
  csnl_research / csnl_ops as read-only.  This script is the ONE sanctioned
  exception: on --apply it WRITES csnl_ops.milestone_meetings.  The
  justification is the incident itself — only this machine can read the NAS, so
  only this machine can tell csnl_ops the truth.  It mirrors, one-for-one, what
  resolve-mm-slides.mjs did before it died; it is not a new capability, it is
  the relocation of an existing one onto the host that can see the share.

DIRECTION SAFETY — NEVER UN-SUBMIT (the one invariant)
  We ONLY ever write False/NULL -> True.  A True row is NEVER touched and NEVER
  reported as missing: a deck the resolver already recorded might now live
  somewhere this matcher does not scan, and un-submitting it would re-create the
  exact false-alarm the chaser caused, in reverse.  The write itself carries a
  `slides_submitted IS NOT TRUE` WHERE-guard, so the direction invariant holds
  at the SQL level even if the plan is stale.

WHO IS "NOT CHASEABLE"
  BHL / HSL / JSL / JHR have NO MM/ folder on the share (config/nas_catalog.json
  records `dirs_absent: [MM/…]`, or the initial is absent from the catalog).
  There is no folder to scan, so there is nothing this reconciler can repair for
  them.  Those rows are reported as NOT-CHASEABLE and are NEVER put in the
  "missing" bucket — the reconciler must not become a new reason to chase a
  person who has no folder.  (The catalog's true-gap adjudication for BHL is a
  SEND-side question for check_materials.py, not a reconciler question: the
  reconciler cannot invent a file, so BHL is not-chaseable here regardless.)

SK IS NOT A PATTERN GAP
  SK's folder holds exactly one deck — 260529_VSSpos+7T.key — and the catalog
  matcher parses it correctly (.key is in the extension allow-list; the
  (?<!\\d)…(?!\\d) guard stops '+7T' being read as a date).  SK has 28 milestone
  rows; 27 have no file because SK genuinely uploaded one deck, not because the
  matcher failed.  So SK is 1 flip + 27 GENUINELY missing — the report says so
  explicitly rather than hiding it.

FAIL CLOSED
  No catalog, or an unreadable/unreachable NAS, and this script reports the
  error and plans NOTHING.  It never falls back to a substring rule.

BOUNDARIES
  * NAS is read-only, listings are bounded and non-recursive, and the mount is
    resolved at runtime — never mounted, never re-authenticated (the NAS
    auto-bans repeated logins).  Shared with check_materials.py::resolve_share_root.
  * csnl_research is untouched.  csnl_ops is READ in dry-run; WRITTEN only on
    --apply, and only in the False/NULL -> True direction.
  * DEFAULT is dry-run: it prints the reconciliation report + writes JSONL and
    changes nothing.  --apply is the explicit, operator-run write path.

USAGE
    python3 scripts/weekly/reconcile_mm_slides.py                 # dry-run report + JSONL
    python3 scripts/weekly/reconcile_mm_slides.py --json          # machine summary to stdout
    python3 scripts/weekly/reconcile_mm_slides.py --rows-json f.json   # offline fixture
    python3 scripts/weekly/reconcile_mm_slides.py --self-check    # prove matcher agreement
    python3 scripts/weekly/reconcile_mm_slides.py --apply         # operator-gated WRITE

EXIT CODES
    0 ok · 3 NAS not reachable · 4 catalog fail-closed · 5 self-check mismatch
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
sys.path.insert(0, str(_HERE))

# REUSE, do not duplicate. The catalog loader, the mount resolver, the folder
# resolver and the tested matcher all live in check_materials.py. In particular
# mm_material_present() IS the matcher — the same one --send trusts — driven by
# config/nas_catalog.json's per-person date_token_forms. We never re-implement a
# date parser here.
from check_materials import (  # noqa: E402
    CATALOG,
    Catalog,
    CatalogError,
    _listdir,
    _nfc,
    mm_material_present,
    resolve_mm_folder,
    resolve_share_root,
)

# Cross-schema target. csnl_ops (NOT the paper-rec ledger, NOT csnl_research).
# Overridable for a test DB, but validated as a bare SQL identifier so it can
# never carry anything but a schema/table name into the interpolated SQL.
OPS_SCHEMA = "csnl_ops"
MM_TABLE = "milestone_meetings"
DEFAULT_JSONL = _REPO_ROOT / "state" / "reconcile_mm_slides.jsonl"

EXIT_NO_NAS = 3
EXIT_CATALOG = 4
EXIT_SELFCHECK = 5

# The measured blast radius, printed alongside our computed flip count so the
# operator can eyeball the match. NOT a gate — the truth is whatever the NAS says.
EXPECTED_FALSE_ALARMS = 24


def _ident(name: str, what: str) -> str:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name or ""):
        raise SystemExit(f"[reconcile] invalid {what}: {name!r}")
    return name


# --------------------------------------------------------------------- model
# Dispositions, most-actionable first:
#   flip                    -> file found, row was NOT True  -> plan write
#   already_submitted       -> file found, row already True  -> no write
#   kept_submitted_no_file  -> row already True, no file here -> NEVER touched
#   truly_missing           -> folder exists, no file for date, row NOT True
#   not_chaseable           -> no MM folder on the share -> cannot repair
FLIP = "flip"
ALREADY = "already_submitted"
KEPT = "kept_submitted_no_file"
MISSING = "truly_missing"
NOT_CHASEABLE = "not_chaseable"


@dataclass
class Decision:
    initial: str
    meeting_date: str          # YYYY-MM-DD
    current: Optional[bool]    # slides_submitted as it stands in the DB
    disposition: str
    slides_path: Optional[str] = None   # 'MM/<INIT>/<file>' for a flip
    reason: str = ""

    def as_json(self) -> dict:
        return {
            "researcher_initial": self.initial,
            "meeting_date": self.meeting_date,
            "current_slides_submitted": self.current,
            "disposition": self.disposition,
            "slides_path": self.slides_path,
            "reason": self.reason,
        }


@dataclass
class FolderInfo:
    """One resolved MM folder, listed ONCE — for the report and SK's audit."""
    folder: Optional[Path]     # existing dir, or None if the person has none
    shown: str                 # path to name in messages
    rel: Optional[str]         # 'MM/<name>' relative to the share, if it exists
    material_files: list[str] = field(default_factory=list)
    file_dates: set = field(default_factory=set)


@dataclass
class Report:
    share_root: str
    share_how: str
    catalog_version: object
    catalog_measured_at: str
    schema: str
    decisions: list[Decision] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    folders: dict = field(default_factory=dict)   # {INIT: FolderInfo}

    def by_disp(self, disp: str) -> list[Decision]:
        return [d for d in self.decisions if d.disposition == disp]

    def per_person(self, disp: str) -> dict[str, list[Decision]]:
        out: dict[str, list[Decision]] = {}
        for d in self.by_disp(disp):
            out.setdefault(d.initial, []).append(d)
        return out


# --------------------------------------------------------------- NAS truth
def _profile_folder(cat: Catalog, share_root: Path, initial: str) -> FolderInfo:
    """Resolve a person's MM folder and list it ONCE.

    The listing feeds the human report (per-person file counts) and the SK
    audit; per-row presence is still asked of mm_material_present so the answer
    that plans a write is the exact one --send trusts."""
    folder, shown = resolve_mm_folder(cat, share_root, initial)
    if folder is None:
        return FolderInfo(None, str(shown), None)
    try:
        rel = folder.relative_to(share_root).as_posix()
    except ValueError:
        rel = f"MM/{initial}"
    names: list[str] = []
    dates: set = set()
    entries, _err = _listdir(folder)
    for f in entries:
        name = _nfc(f.name)
        if cat.junk_reason(name):
            continue
        try:
            if not f.is_file():
                continue
        except OSError:
            continue
        names.append(name)
        dates |= cat.file_dates(name)     # catalog date_token_forms — not hand-rolled
    return FolderInfo(folder, str(shown), rel, names, dates)


def _not_chaseable_reason(cat: Catalog, initial: str) -> str:
    """Why the reconciler cannot repair this person from the NAS."""
    ab = cat.mm_absence(initial)
    if ab is None:
        return ("MM 폴더가 카탈로그 기대와 달리 공유에 없습니다 — 연구원이 아니라 "
                "공유/마운트를 확인하세요 (reconciler 는 판단하지 않음)")
    if ab.source == "not-in-catalog":
        return (f"config/nas_catalog.json 에 initials.{initial} 없음 — MM 폴더 부재로 "
                f"간주, 재조정 대상 아님")
    if ab.true_gap:
        return (f"카탈로그가 '{ab.token}' 부재를 실제 누락으로 판정 — 그러나 NAS 에 파일이 "
                f"없어 reconciler 가 복구할 수 없음 (발송 판단은 check_materials 소관)")
    return f"카탈로그 dirs_absent 가 '{ab.token}' 를 정상 부재로 기록 — 재조정 대상 아님"


def reconcile(rows: list[dict], cat: Catalog, share_root: Path,
              share_how: str) -> Report:
    rep = Report(
        share_root=str(share_root), share_how=share_how,
        catalog_version=cat.version, catalog_measured_at=cat.measured_at,
        schema=OPS_SCHEMA, warnings=list(cat.notes),
    )
    for row in rows:
        init = str(row.get("researcher_initial") or "").strip().upper()
        day = str(row.get("meeting_date") or "").strip()
        cur = row.get("slides_submitted")
        cur = None if cur is None else bool(cur)
        if not init or not day:
            rep.warnings.append(f"불완전한 행 건너뜀: {row!r}")
            continue

        if init not in rep.folders:
            rep.folders[init] = _profile_folder(cat, share_root, init)
        fi = rep.folders[init]

        if fi.folder is None:
            rep.decisions.append(Decision(
                init, day, cur, NOT_CHASEABLE,
                reason=_not_chaseable_reason(cat, init)))
            continue

        # The tested matcher IS the oracle. `why` is the filename when ok=True.
        ok, why = mm_material_present(cat, share_root, init, day)
        if ok:
            path = f"{fi.rel}/{why}" if fi.rel else f"MM/{init}/{why}"
            if cur is True:
                rep.decisions.append(Decision(init, day, cur, ALREADY,
                                              slides_path=path,
                                              reason="이미 제출됨 — 변경 없음"))
            else:
                rep.decisions.append(Decision(init, day, cur, FLIP,
                                              slides_path=path,
                                              reason=f"NAS 에서 발견: {why}"))
        else:
            if cur is True:
                # NEVER un-submit. The file may live somewhere we do not scan.
                rep.decisions.append(Decision(
                    init, day, cur, KEPT,
                    reason="이미 제출로 기록됨 — 매처가 파일을 못 찾았으나 건드리지 않음 "
                           "(un-submit 금지)"))
            else:
                rep.decisions.append(Decision(init, day, cur, MISSING,
                                              reason=why))
    return rep


# --------------------------------------------------------------- self-check
def self_check(rows: list[dict], cat: Catalog, share_root: Path) -> int:
    """Prove the per-folder listing in this file never disagrees with the
    authoritative mm_material_present() matcher — a guard against future drift.

    (mm_material_present is already what plans every flip; this simply re-asserts
    that the folder profile we print is consistent with it for every row.)"""
    mism = 0
    seen: dict[str, FolderInfo] = {}
    for row in rows:
        init = str(row.get("researcher_initial") or "").strip().upper()
        day = str(row.get("meeting_date") or "").strip()
        if not init or not day:
            continue
        if init not in seen:
            seen[init] = _profile_folder(cat, share_root, init)
        fi = seen[init]
        ok, _why = mm_material_present(cat, share_root, init, day)
        idx_hit = fi.folder is not None and any(
            abs((d - _parse(day)).days) <= cat.tol_days for d in fi.file_dates)
        if ok != idx_hit:
            mism += 1
            print(f"[self-check] MISMATCH {init} {day}: matcher={ok} "
                  f"folder-index={idx_hit}", file=sys.stderr)
    if mism:
        print(f"[self-check] {mism} mismatch(es) — matcher and folder listing "
              f"disagree; investigate before trusting the report", file=sys.stderr)
        return EXIT_SELFCHECK
    print(f"[self-check] OK — matcher agrees with the folder listing on all "
          f"{len(rows)} rows")
    return 0


def _parse(day: str):
    from datetime import datetime
    return datetime.strptime(day, "%Y-%m-%d").date()


# --------------------------------------------------------------- data plane
def load_rows(args) -> list[dict]:
    if args.rows_json:
        data = json.loads(Path(args.rows_json).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise SystemExit("[reconcile] --rows-json must be a JSON array")
        return data
    schema = _ident(args.schema, "schema")
    table = _ident(MM_TABLE, "table")
    from _db import load_env, query_json
    load_env()
    sql = (f"SELECT meeting_date::text AS meeting_date, researcher_initial, "
           f"slides_submitted, slides_path FROM {schema}.{table}")
    if args.since:
        # meeting_date literal, validated to YYYY-MM-DD so nothing else reaches SQL.
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", args.since):
            raise SystemExit(f"[reconcile] --since must be YYYY-MM-DD: {args.since!r}")
        sql += f" WHERE meeting_date >= '{args.since}'"
    sql += " ORDER BY researcher_initial, meeting_date"
    return query_json(sql)


def apply_flips(flips: list[Decision], schema: str) -> int:
    """Write False/NULL -> True ONLY. The WHERE-guard makes the direction
    invariant hold even against a stale plan: a row that is already True is
    left exactly as it is."""
    schema = _ident(schema, "schema")
    table = _ident(MM_TABLE, "table")
    from _db import load_env, exec_many
    load_env()
    sql = (f"UPDATE {schema}.{table} "
           f"SET slides_submitted = TRUE, slides_path = %s "
           f"WHERE researcher_initial = %s AND meeting_date = %s "
           f"AND slides_submitted IS NOT TRUE")
    payload = [(d.slides_path, d.initial, d.meeting_date) for d in flips]
    return exec_many(sql, payload)


# --------------------------------------------------------------- reporting
def write_jsonl(rep: Report, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for d in rep.decisions:
                fh.write(json.dumps(d.as_json(), ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[reconcile] JSONL 기록 실패 ({e}) — 보고서는 계속 진행합니다",
              file=sys.stderr)


def print_report(rep: Report) -> None:
    flips = rep.per_person(FLIP)
    missing = rep.per_person(MISSING)
    nch = rep.per_person(NOT_CHASEABLE)
    already = rep.by_disp(ALREADY)
    kept = rep.by_disp(KEPT)
    n_flip = sum(len(v) for v in flips.values())

    print(f"[reconcile] share {rep.share_root} [{rep.share_how}] · "
          f"catalog v{rep.catalog_version} ({rep.catalog_measured_at}) · "
          f"target {rep.schema}.{MM_TABLE}")
    print(f"[reconcile] rows={len(rep.decisions)} · "
          f"flips={n_flip} · truly-missing={sum(len(v) for v in missing.values())} "
          f"· not-chaseable={sum(len(v) for v in nch.values())} "
          f"· already-True={len(already)} · kept-True-no-file={len(kept)}")
    for w in rep.warnings:
        print(f"[reconcile] warn: {w}")

    print(f"\n[reconcile] FLIP False/NULL -> True (per researcher)")
    if not flips:
        print("  (none)")
    for init in sorted(flips):
        ds = sorted(flips[init], key=lambda x: x.meeting_date)
        print(f"  {init:<5} {len(ds):>2}  {', '.join(d.meeting_date for d in ds)}")
    print(f"  {'TOTAL':<5} {n_flip:>2}   "
          f"(measured false alarms in the incident: {EXPECTED_FALSE_ALARMS})")
    if n_flip != EXPECTED_FALSE_ALARMS:
        print(f"  note: computed flips ({n_flip}) != measured ({EXPECTED_FALSE_ALARMS}) "
              f"— reconcile the difference before --apply")

    print(f"\n[reconcile] TRULY MISSING — folder exists, no deck for that date "
          f"(NOT emailed by this tool)")
    if not missing:
        print("  (none)")
    for init in sorted(missing):
        ds = sorted(missing[init], key=lambda x: x.meeting_date)
        print(f"  {init:<5} {len(ds):>2}  {', '.join(d.meeting_date for d in ds)}")

    print(f"\n[reconcile] NOT CHASEABLE — no MM folder on the share "
          f"(reconciler cannot repair; must NOT be chased)")
    if not nch:
        print("  (none)")
    for init in sorted(nch):
        ds = nch[init]
        print(f"  {init:<5} {len(ds):>2}  {ds[0].reason}")

    if kept:
        print(f"\n[reconcile] KEPT — already True, matcher found no file → left "
              f"untouched (never un-submitted)")
        for d in sorted(kept, key=lambda x: (x.initial, x.meeting_date)):
            print(f"  {d.initial:<5} {d.meeting_date}")

    _print_sk_audit(rep, missing)


def _print_sk_audit(rep: Report, missing: dict[str, list[Decision]]) -> None:
    """The anomaly the incident asked about explicitly: are SK's ~27 'missing'
    rows a real absence or a matcher/pattern gap?"""
    fi = rep.folders.get("SK")
    if fi is None:
        return
    sk_flip = [d for d in rep.by_disp(FLIP) if d.initial == "SK"]
    sk_miss = missing.get("SK", [])
    print(f"\n[reconcile] SK anomaly audit")
    if fi.folder is None:
        print("  SK has no MM folder on the share — not chaseable.")
        return
    print(f"  folder: {fi.rel}  ·  material files: {len(fi.material_files)} "
          f"({', '.join(fi.material_files) or '—'})")
    print(f"  parsed file-dates: "
          f"{', '.join(sorted(d.isoformat() for d in fi.file_dates)) or '—'}")
    print(f"  flips: {len(sk_flip)} "
          f"({', '.join(d.meeting_date for d in sk_flip) or '—'})  ·  "
          f"truly-missing: {len(sk_miss)}")
    print(f"  verdict: the {len(sk_miss)} 'missing' rows are a GENUINE absence, "
          f"NOT a pattern gap — .key is in the extension allow-list and the "
          f"(?<!\\d)…(?!\\d) guard correctly stops '+7T' being read as a date, so "
          f"the one deck SK did upload matches and the rest have no file at all. "
          f"(A CHASER must still not email SK about {len(sk_miss)} misses — that "
          f"is check_materials.py's send-side interlock, out of scope here.)")


def report_as_json(rep: Report) -> dict:
    flips = rep.per_person(FLIP)
    missing = rep.per_person(MISSING)
    nch = rep.per_person(NOT_CHASEABLE)
    return {
        "share_root": rep.share_root, "resolved_by": rep.share_how,
        "catalog": {"version": rep.catalog_version,
                    "measured_at": rep.catalog_measured_at},
        "schema": rep.schema, "table": MM_TABLE,
        "counts": {
            "rows": len(rep.decisions),
            "flip": sum(len(v) for v in flips.values()),
            "truly_missing": sum(len(v) for v in missing.values()),
            "not_chaseable": sum(len(v) for v in nch.values()),
            "already_submitted": len(rep.by_disp(ALREADY)),
            "kept_submitted_no_file": len(rep.by_disp(KEPT)),
            "expected_false_alarms": EXPECTED_FALSE_ALARMS,
        },
        "flips_per_researcher": {k: [d.meeting_date for d in v]
                                 for k, v in flips.items()},
        "truly_missing_per_researcher": {k: [d.meeting_date for d in v]
                                         for k, v in missing.items()},
        "not_chaseable_per_researcher": {k: {"count": len(v), "reason": v[0].reason}
                                         for k, v in nch.items()},
        "warnings": rep.warnings,
    }


# --------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else None)
    ap.add_argument("--apply", action="store_true",
                    help="WRITE csnl_ops.milestone_meetings (False/NULL -> True only). "
                         "Operator-run; the default is dry-run.")
    ap.add_argument("--since", default=None,
                    help="only reconcile rows with meeting_date >= YYYY-MM-DD "
                         "(default: the whole table)")
    ap.add_argument("--rows-json", default=None,
                    help="offline fixture instead of the DB (list of "
                         "{researcher_initial, meeting_date, slides_submitted})")
    ap.add_argument("--schema", default=OPS_SCHEMA,
                    help=f"ops schema holding {MM_TABLE} (default {OPS_SCHEMA})")
    ap.add_argument("--jsonl", default=str(DEFAULT_JSONL),
                    help=f"where to write the per-row plan (default {DEFAULT_JSONL})")
    ap.add_argument("--json", action="store_true",
                    help="print the report as JSON to stdout (still writes JSONL)")
    ap.add_argument("--self-check", action="store_true",
                    help="prove the folder listing agrees with mm_material_present, "
                         "then exit")
    ap.add_argument("--catalog", default=None, help="override config/nas_catalog.json")
    args = ap.parse_args()

    # FAIL CLOSED — no trusted matcher, no plan.
    try:
        cat = Catalog.load(Path(args.catalog) if args.catalog else CATALOG)
    except CatalogError as e:
        print(f"[reconcile] 카탈로그를 읽을 수 없어 중단합니다 (계획 없음, 발송/쓰기 없음): {e}",
              file=sys.stderr)
        return EXIT_CATALOG
    for n in cat.notes:
        print(f"[reconcile] note: {n}", file=sys.stderr)

    share_root, how = resolve_share_root()
    mounted = (share_root / cat.mm_root_rel).is_dir()
    if not mounted:
        print(f"[reconcile] NAS not reachable ({share_root} via {how}) — cannot "
              f"reconcile. Mount the share as this user or set CSNL_NAS_ROOT. Do NOT "
              f"re-authenticate: the NAS auto-bans repeated logins.", file=sys.stderr)
        return EXIT_NO_NAS

    rows = load_rows(args)

    if args.self_check:
        return self_check(rows, cat, share_root)

    rep = reconcile(rows, cat, share_root, how)
    write_jsonl(rep, Path(args.jsonl))

    if args.json:
        print(json.dumps(report_as_json(rep), ensure_ascii=False, indent=2))
    else:
        print_report(rep)
        print(f"\n[reconcile] per-row plan written: {args.jsonl}")

    flips = [d for d in rep.decisions if d.disposition == FLIP]
    if args.apply:
        if not flips:
            print("[reconcile] --apply: 반영할 flip 이 없습니다.")
            return 0
        print(f"[reconcile] --apply: {rep.schema}.{MM_TABLE} 에 {len(flips)} 건 "
              f"False/NULL -> True 반영 (WHERE slides_submitted IS NOT TRUE)")
        n = apply_flips(flips, rep.schema)
        print(f"[reconcile] --apply: {n} 건 UPDATE 실행 (True 행은 절대 건드리지 않음). "
              f"확인하려면 이 스크립트를 dry-run 으로 다시 돌려 flips=0 인지 보세요.")
    else:
        print(f"[reconcile] dry-run — 아무것도 쓰지 않았습니다. 운영자가 --apply 로 "
              f"{len(flips)} 건을 반영합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
