#!/usr/bin/env python3
"""
scripts/weekly/check_materials.py — weekly NAS material-completeness check.

Cross-references what the CALENDAR says happened against what is actually on the
NAS, and (optionally) sends each member a friendly note about anything missing.

Rules (docs/CSNL-INFO.md §3a/§3b — the single source of truth):
  * a `Meeting: [INIT]` (MM) event  -> MM/{INIT}/ must hold a file for that date
  * a GRM (Wed lab meeting) event   -> GRM/{YYYY}/{YYYYMMDD}/ must hold BOTH a
                                       GRM-kind file AND a PB-kind file

TONE (operator directive): this is NOT rule enforcement. The note says
"…에 옮겨주시면 csnl-on-ai 프로젝트에 도움이 됩니다" — never "must" / "규정".

BOUNDARIES
  * Postgres is READ-ONLY here (calendar truth from csnl_ops). No writes.
  * The NAS is read-only; nothing is moved or created on the share.
  * Researcher-facing send is gated: dry-run prints the notes; --send is explicit.
  * At most ONE send per ISO week (state/materials_last_notified_week), so a
    retry/boot loop can never turn into repeat mail.

USAGE
    python3 scripts/weekly/check_materials.py                    # dry-run report
    python3 scripts/weekly/check_materials.py --events-json f.json  # offline fixture
    python3 scripts/weekly/check_materials.py --send             # operator-gated
    python3 scripts/weekly/check_materials.py --send --to-self   # smoke test
"""
from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import ssl
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
sys.path.insert(0, str(_HERE))

# Reuse, do not duplicate: the mount resolver and the GRM/PB filename classifier
# already live in the ingest module.
from ingest_grm_nas import _resolve_nas_base, classify  # noqa: E402

INFO_DOC = _REPO_ROOT / "docs" / "CSNL-INFO.md"
WEEK_STAMP = _REPO_ROOT / "state" / "materials_last_notified_week"
DEFAULT_LOOKBACK_WEEKS = 4


# --------------------------------------------------------------------- registry
def load_registry(doc: Path = INFO_DOC) -> dict:
    """Parse the fenced ```yaml block out of docs/CSNL-INFO.md.

    The doc is the single source of truth for people + conventions, so the
    checker reads it directly rather than keeping a second copy in sync.
    """
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
    """{INIT: {full_name, email, role}} for ACTIVE members only.

    Alumni are never contacted — they have no ongoing material duties.
    """
    out: dict[str, dict] = {}
    for r in reg.get("researchers") or []:
        if (r.get("status") or "").lower() != "active":
            continue
        init = (r.get("initial") or "").strip().upper()
        if init:
            out[init] = r
    return out


# ------------------------------------------------------------------- gap model
@dataclass
class Gap:
    initial: str
    kind: str                 # 'mm' | 'grm' | 'pb'
    meeting_date: str         # YYYY-MM-DD
    expected_dir: str
    detail: str


@dataclass
class Report:
    window_start: str
    window_end: str
    nas_base: str
    nas_mounted: bool
    gaps: list[Gap] = field(default_factory=list)
    checked_mm: int = 0
    checked_grm: int = 0

    def by_person(self) -> dict[str, list[Gap]]:
        out: dict[str, list[Gap]] = {}
        for g in self.gaps:
            out.setdefault(g.initial, []).append(g)
        return out


# ------------------------------------------------------------------- NAS probes
def _yymmdd(d: str) -> str:
    """'2026-07-15' -> '260715' (the filename date token)."""
    return datetime.strptime(d, "%Y-%m-%d").strftime("%y%m%d")


def mm_material_present(mm_root: Path, initial: str, meeting_date: str) -> tuple[bool, str]:
    """Is there a file for this date under MM/<INIT>/ ?

    Accepts any file whose name carries the yymmdd token (MM_260715.pptx,
    260715_MM.pdf, ...) — the check is about the material existing, not about
    enforcing one exact filename.
    """
    folder = mm_root / initial
    if not folder.is_dir():
        return False, f"{folder} 폴더가 없습니다"
    token = _yymmdd(meeting_date)
    try:
        for f in folder.iterdir():
            if f.is_file() and token in f.name:
                return True, f.name
    except OSError as e:
        return False, f"{folder} 를 읽을 수 없습니다 ({e.__class__.__name__})"
    return False, f"{folder} 안에 {token} 날짜 자료가 없습니다"


def grm_pb_present(day_dir: Path) -> tuple[bool, bool, str]:
    """(has_grm, has_pb, note) for one GRM date folder, via classify()."""
    if not day_dir.is_dir():
        return False, False, f"{day_dir} 폴더가 없습니다"
    has_grm = has_pb = False
    try:
        for f in day_dir.iterdir():
            if not f.is_file():
                continue
            kind, _init, _name = classify(f.name)
            if kind in ("grm", "focus_grm"):
                has_grm = True
            elif kind == "pb":
                has_pb = True
    except OSError as e:
        return False, False, f"{day_dir} 를 읽을 수 없습니다 ({e.__class__.__name__})"
    return has_grm, has_pb, ""


# ------------------------------------------------------------------ event input
def load_events(args) -> list[dict]:
    """Calendar truth: [{kind:'mm'|'grm', date:'YYYY-MM-DD', initial:'JOP'}, ...]

    --events-json feeds a fixture (offline testing, and the only path this agent
    ever uses). Without it the operator's run reads csnl_ops READ-ONLY.
    """
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
def build_report(events: list[dict], nas_base: Path, weeks: int) -> Report:
    """nas_base is .../GRM/<year>; the share root is two levels up."""
    share_root = nas_base.parent.parent
    mm_root = share_root / "MM"
    end = date.today()
    start = end - timedelta(weeks=weeks)
    rep = Report(
        window_start=start.isoformat(), window_end=end.isoformat(),
        nas_base=str(nas_base), nas_mounted=nas_base.is_dir(),
    )
    if not rep.nas_mounted:
        return rep

    for ev in events:
        d, init = ev.get("date"), (ev.get("initial") or "").upper()
        if not d or not init:
            continue
        if not (start.isoformat() <= d <= end.isoformat()):
            continue

        if ev.get("kind") == "mm":
            rep.checked_mm += 1
            ok, why = mm_material_present(mm_root, init, d)
            if not ok:
                rep.gaps.append(Gap(init, "mm", d, str(mm_root / init), why))

        elif ev.get("kind") == "grm":
            rep.checked_grm += 1
            day_dir = nas_base / d.replace("-", "")
            has_grm, has_pb, note = grm_pb_present(day_dir)
            if note:
                rep.gaps.append(Gap(init, "grm", d, str(day_dir), note))
                continue
            if not has_grm:
                rep.gaps.append(Gap(init, "grm", d, str(day_dir), "GRM 발표 자료를 찾지 못했습니다"))
            if not has_pb:
                rep.gaps.append(Gap(init, "pb", d, str(day_dir), "Paper Blitz 자료를 찾지 못했습니다"))
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
    args = ap.parse_args()

    if not args.events_json:
        from _db import load_env
        load_env()

    reg = load_registry()
    people = active_people(reg)
    nas_base = _resolve_nas_base(os.environ.get("GRM_NAS_YEAR") or str(datetime.now().year))
    rep = build_report(load_events(args), nas_base, args.weeks)

    if args.json:
        print(json.dumps({
            "window": [rep.window_start, rep.window_end],
            "nas_base": rep.nas_base, "nas_mounted": rep.nas_mounted,
            "checked": {"mm": rep.checked_mm, "grm": rep.checked_grm},
            "gaps": [vars(g) for g in rep.gaps],
        }, ensure_ascii=False, indent=2))
        return 0

    if not rep.nas_mounted:
        print(f"[materials] NAS not mounted ({rep.nas_base}) — cannot check. "
              f"Mount the share as this user or set GRM_NAS_BASE.", file=sys.stderr)
        return 3

    print(f"[materials] window {rep.window_start}..{rep.window_end} · "
          f"checked MM={rep.checked_mm} GRM={rep.checked_grm} · gaps={len(rep.gaps)}")
    if not rep.gaps:
        print("[materials] 모든 자료가 제자리에 있습니다.")
        return 0

    week = date.today().strftime("%G-W%V")
    if args.send and not args.force and week_already_sent(week):
        print(f"[materials] {week} 에 이미 발송했습니다 — 주 1회 제한 (--force 로 우회)")
        return 0

    ok, why = smtp_ready()
    sent = 0
    for init, gaps in sorted(rep.by_person().items()):
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
            return 2
        send_mail(to_addr, subject, body)
        print(f"[materials] sent → {to_addr} ({len(gaps)}건)")
        sent += 1

    if args.send and sent and not args.to_self:
        WEEK_STAMP.parent.mkdir(parents=True, exist_ok=True)
        WEEK_STAMP.write_text(week, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
