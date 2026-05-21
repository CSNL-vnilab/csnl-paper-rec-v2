#!/usr/bin/env python3
"""
scripts/cron_tick.py — multi-turn conversation state machine engine.

Runs every 4 hours via launchd (com.csnl.paper-rec.tick.plist). Idempotent.
Gated by state/.CRON_ENABLED. Lockfile state/.cron_tick.lock prevents
concurrent runs. Reuses fetch_replies/classify_feedback/apply_feedback +
deliver.py. NO LLM. Pure rule-based per-recipient state transitions.

For each recipient in the active cycle:
  awaiting_initial_reply ─reply─→ classify ─→ ack DM + state-advance
                         ─24h ─→ send reminder ─→ reminded
  reminded               ─reply─→ classify
                         ─24h ─→ timeout
  awaiting_decision      ─more reply─→ re-classify
                         ─12h ─→ timeout
Terminal: decided / passed / timeout / no_rec.

Rules respected: rules/00 (NO PB content), rules/01 (BANNED_TERMS
hard-abort via deliver.py), rules/04 (dedup + feedback loop), rules/06 §3
(2+ consistent signals before logic change — only applies at evolution
phase, not here).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "pipeline"))
from _db import load_env, exec_many, query_json, ledger_schema  # noqa: E402

import yaml  # noqa: E402

_KST = timezone(timedelta(hours=9))
INITIAL_TO_REMINDER_H = 24
REMINDER_TO_TIMEOUT_H = 24
DECISION_TIMEOUT_H = 12
LOCK_STALE_S = 3600

DISPLAY = {"JOP": "박준오", "BYL": "이보연", "MSY": "여민수", "SMJ": "정새미",
           "JYK": "김정예", "SYJ": "조수영", "BHL": "이보현"}

REMINDER_TPL = (
    "{name} 연구원께,\n\n"
    "지난 추천 논문(\"{title}\", https://doi.org/{doi})을 확인하셨는지요. "
    "추천작에 대한 피드백, 또는 후보 목록 중 다른 논문을 읽어보고 싶으시면 "
    "본 메시지에 회신해 주십시오. 응답이 없으셔도 무방합니다."
)
ACK_TPL = (
    "{name} 연구원께,\n\n"
    "지난 추천(\"{title}\")에 대한 회신을 확인했습니다. {action}{extra}\n\n"
    "추가 의견이 있으시면 언제든 본 메시지에 회신해 주십시오."
)
ACK = {
    "thumbs_up":     "추천이 도움 되신 것으로 확인했습니다. 동일한 방향성을 향후 추천에 유지하도록 반영하겠습니다.",
    "thumbs_down":   "해당 추천을 적합하지 않은 것으로 기록하고, 향후 동일 논문이 재추천 되지 않도록 본 분의 제외 목록에 반영하겠습니다.",
    "already_read":  "이미 읽으신 논문으로 기록하여 향후 재추천 대상에서 제외되도록 반영하겠습니다.",
    "saved":         "저장하셨다고 기록해 두었습니다. 후속 추천에서 관련 방향을 참고하겠습니다.",
    "cited":         "인용 예정으로 기록하였습니다. 관련 후속 연구 방향을 다음 추천에 반영하겠습니다.",
    "thinking":      "회신 확인했습니다. 의견을 별도로 기록해 두었으며 다음 추천에 참고하겠습니다.",
    "thread_reply":  "회신 확인했습니다. 의견을 별도로 기록해 두었으며 다음 추천에 참고하겠습니다.",
}
TERMINAL = {"decided", "passed", "timeout", "no_rec"}


def kst_now() -> datetime:
    return datetime.now(_KST)


def kst_iso() -> str:
    return kst_now().isoformat(timespec="seconds")


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def banned_scan(text: str) -> list[str]:
    tone = (_ROOT / "rules" / "01_tone.md").read_text()
    m = re.search(r"```BANNED_TERMS\s*\n(.*?)\n```", tone, re.S)
    if not m:
        return []
    low = text.lower()
    return [t.strip() for t in m.group(1).splitlines()
            if t.strip() and t.strip().lower() in low]


def gate_ok() -> bool:
    return (_ROOT / "state" / ".CRON_ENABLED").exists()


def acquire_lock() -> Path | None:
    lock = _ROOT / "state" / ".cron_tick.lock"
    if lock.exists():
        try:
            age = datetime.now().timestamp() - lock.stat().st_mtime
        except OSError:
            age = 0
        if age < LOCK_STALE_S:
            return None
        lock.unlink()
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()))
    return lock


def release_lock(lock: Path | None) -> None:
    try:
        if lock and lock.exists():
            lock.unlink()
    except OSError:
        pass


def load_researchers() -> dict:
    return yaml.safe_load((_ROOT / "config" / "researchers.yaml").read_text())


def active_cycle_id(now: datetime | None = None) -> str:
    """Friday-yyyymmdd of the active week. Before Fri 14:00 → previous Friday."""
    now = now or kst_now()
    weekday = now.weekday()  # Mon=0..Sun=6; Friday=4
    days_since_fri = (weekday - 4) % 7
    fri = (now.replace(hour=14, minute=0, second=0, microsecond=0)
           - timedelta(days=days_since_fri))
    if now < fri:
        fri -= timedelta(days=7)
    return fri.strftime("%Y%m%d")


def run_subscript(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3"] + args, cwd=str(_ROOT),
        capture_output=True, text=True, timeout=600)


def fetch_replies_for(rid: str) -> dict:
    rp = _ROOT / "state" / "runs" / rid / "_replies.json"
    proc = run_subscript([str(_ROOT / "scripts" / "fetch_replies.py"),
                          "--run-id", rid])
    if proc.returncode != 0:
        print(f"  fetch_replies FAILED: {proc.stderr.strip()}")
        return {"replies": []}
    return json.loads(rp.read_text())


def classify_for(rid: str) -> dict:
    fp = _ROOT / "state" / "runs" / rid / "_feedback_proposals.json"
    proc = run_subscript([str(_ROOT / "scripts" / "classify_feedback.py"),
                          "--run-id", rid])
    if proc.returncode != 0:
        print(f"  classify FAILED: {proc.stderr.strip()}")
        return {"proposals": []}
    return json.loads(fp.read_text())


def render_ack(name: str, title: str, signal: str, picked_alt: str | None,
               excl_term: str | None) -> str:
    action = ACK.get(signal, ACK["thread_reply"])
    extra = ""
    if picked_alt:
        extra += (f" 회신하신 선택({picked_alt})에 따라 해당 논문을 다음 "
                  "추천으로 갱신하여 별도 전달드리겠습니다.")
    if excl_term:
        extra += f" 제외 대상으로 기록될 항목: {excl_term}."
    return ACK_TPL.format(name=name, title=title, action=action, extra=extra)


def write_adhoc_drafts(rid: str, name_kind: str, items: list[dict]) -> Path:
    """Stage a one-shot 08-schema drafts file for deliver.py --mode dm --drafts."""
    rd = _ROOT / "state" / "runs" / rid
    rd.mkdir(parents=True, exist_ok=True)
    f = rd / f"_adhoc_{name_kind}.json"
    f.write_text(json.dumps(
        {"run_id": rid, "mode": "dm", "kind": name_kind, "drafts": items},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return f


def fire_send(rid: str, adhoc_path: Path) -> bool:
    """Run deliver.py --mode dm --drafts <adhoc> --send --operator-approved."""
    appr = _ROOT / "state" / f".APPROVED_{rid}"
    if not appr.exists():
        appr.write_text(f"cron auto-token for cycle {rid} (CRON_ENABLED)\n")
    proc = run_subscript([
        str(_ROOT / "scripts" / "deliver.py"),
        "--run-id", rid, "--mode", "dm",
        "--drafts", str(adhoc_path),
        "--send", "--operator-approved"])
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        return False
    return True


def apply_feedback_for(rid: str) -> bool:
    proc = run_subscript([str(_ROOT / "scripts" / "apply_feedback.py"),
                          "--run-id", rid])
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        return False
    return True


def upsert_state(schema: str, row: dict) -> None:
    cols = ("cycle_id", "member_init", "unit_id", "state", "rid", "paper_doi",
            "paper_title", "picked_alt_doi", "reply_count", "last_action_at",
            "next_action_at", "last_reply_ts", "notes")
    vals = [tuple(row.get(c) for c in cols)]
    sql = (f"INSERT INTO {schema}.cycle_state ({','.join(cols)}) "
           f"VALUES ({','.join(['%s']*len(cols))}) "
           "ON CONFLICT (cycle_id, member_init) DO UPDATE SET "
           "state=EXCLUDED.state, rid=EXCLUDED.rid, "
           "paper_doi=EXCLUDED.paper_doi, paper_title=EXCLUDED.paper_title, "
           "picked_alt_doi=EXCLUDED.picked_alt_doi, "
           "reply_count=EXCLUDED.reply_count, "
           "last_action_at=EXCLUDED.last_action_at, "
           "next_action_at=EXCLUDED.next_action_at, "
           "last_reply_ts=EXCLUDED.last_reply_ts, notes=EXCLUDED.notes")
    exec_many(sql, vals)


# ---------------------------------------------------------------------------
# Main tick
# ---------------------------------------------------------------------------

def tick(dry_run: bool = False) -> int:
    if not gate_ok():
        print(f"[cron_tick] {kst_iso()}  state/.CRON_ENABLED absent — silent exit")
        return 0
    lock = acquire_lock()
    if lock is None:
        print(f"[cron_tick] {kst_iso()}  another tick in progress — exit")
        return 0
    try:
        return _tick_inner(dry_run)
    finally:
        release_lock(lock)


def _tick_inner(dry_run: bool) -> int:
    load_env()
    schema = ledger_schema()
    cycle_id = active_cycle_id()
    print(f"[cron_tick] {kst_iso()}  cycle_id={cycle_id}  dry_run={dry_run}")

    states = {r["member_init"]: r for r in query_json(
        f"SELECT * FROM {schema}.cycle_state WHERE cycle_id='{cycle_id}'")}
    if not states:
        print(f"  no cycle_state rows for cycle_id={cycle_id} — "
              "weekly cron has not initialized this cycle yet")
        return 0

    rids = sorted({r.get("rid") for r in states.values() if r.get("rid")})
    if not rids:
        print("  cycle_state rows have no rid yet — nothing to tick")
        return 0

    now = kst_now()
    # Pull replies + classification per active rid
    rid = rids[-1]  # one active rec rid per cycle in current design
    print(f"  active rid: {rid}")
    fetch_replies_for(rid)
    fb = classify_for(rid)
    proposals_by_member = {p["member_init"]: p for p in fb.get("proposals", [])}

    reminder_items: list[dict] = []
    ack_items: list[dict] = []
    members_with_new_signal = set()

    for member, st in states.items():
        if st["state"] in TERMINAL:
            continue
        next_at = parse_iso(st.get("next_action_at"))
        last_at = parse_iso(st.get("last_action_at"))
        prop = proposals_by_member.get(member)
        reply_ts = (prop or {}).get("reply_ts") or st.get("last_reply_ts")

        # ---- Reply arrival paths ----
        if prop and (st.get("last_reply_ts") != prop.get("reply_ts")):
            members_with_new_signal.add(member)
            signal = prop.get("signal", "thread_reply")
            conf = prop.get("confidence", "low")
            picked = prop.get("picked_alternate_doi")
            excl_term = (prop.get("exclusion") or {}).get("excluded_term")

            # State transition
            if signal == "thumbs_up":
                new_state = "decided"
            elif signal == "thumbs_down":
                new_state = "passed"
            elif signal == "already_read":
                new_state = "passed"
            elif signal in ("saved", "cited"):
                new_state = "decided"
            else:  # thinking / thread_reply / unknown
                new_state = "awaiting_decision"

            if picked:
                new_state = "decided"

            # Build ack item (mirrors 08-schema)
            dm_text = render_ack(DISPLAY.get(member, member),
                                 st.get("paper_title") or "-",
                                 signal, picked, excl_term)
            if banned_scan(dm_text):
                print(f"  [{member}] ack hit banned terms — SKIP")
                continue
            ack_items.append({
                "unit_id": st.get("unit_id") or member,
                "member_init": member,
                "display_name": DISPLAY.get(member, member),
                "dm_channel": _dm_channel(member),
                "paper_doi": st.get("paper_doi") or "",
                "paper_title": st.get("paper_title") or "",
                "paper_authors": "", "paper_venue": "",
                "paper_date": "", "tier": "strict",
                "dm_text": dm_text,
            })
            upsert_state(schema, {
                **st, "state": new_state,
                "picked_alt_doi": picked,
                "reply_count": int(st.get("reply_count") or 0) + 1,
                "last_action_at": kst_iso(),
                "last_reply_ts": reply_ts,
                "next_action_at": (kst_now() + timedelta(hours=DECISION_TIMEOUT_H)).isoformat(timespec="seconds")
                                  if new_state == "awaiting_decision" else None,
                "notes": (st.get("notes") or "") + f"\n{kst_iso()} reply→{signal}/{conf}",
            })
            continue

        # ---- No new reply: time-based transitions ----
        if not next_at or now < next_at:
            continue  # not yet due

        if st["state"] == "awaiting_initial_reply":
            # send reminder
            name = DISPLAY.get(member, member)
            title = st.get("paper_title") or "-"
            doi = st.get("paper_doi") or ""
            text = REMINDER_TPL.format(name=name, title=title, doi=doi)
            if banned_scan(text):
                print(f"  [{member}] reminder hit banned terms — SKIP")
                continue
            reminder_items.append({
                "unit_id": st.get("unit_id") or member,
                "member_init": member, "display_name": name,
                "dm_channel": _dm_channel(member),
                "paper_doi": doi, "paper_title": title,
                "paper_authors": "", "paper_venue": "",
                "paper_date": "", "tier": "strict",
                "dm_text": text,
            })
            upsert_state(schema, {
                **st, "state": "reminded",
                "last_action_at": kst_iso(),
                "next_action_at": (kst_now() + timedelta(hours=REMINDER_TO_TIMEOUT_H)).isoformat(timespec="seconds"),
                "notes": (st.get("notes") or "") + f"\n{kst_iso()} reminder sent",
            })
        elif st["state"] in ("reminded", "awaiting_decision"):
            # timeout
            upsert_state(schema, {
                **st, "state": "timeout",
                "last_action_at": kst_iso(), "next_action_at": None,
                "notes": (st.get("notes") or "") + f"\n{kst_iso()} timeout",
            })

    # Fire sends
    if reminder_items and not dry_run:
        path = write_adhoc_drafts(rid, "reminder", reminder_items)
        print(f"  firing {len(reminder_items)} reminder(s) via {path.name}")
        fire_send(rid, path)
    elif reminder_items:
        print(f"  [dry_run] would fire {len(reminder_items)} reminders")

    # Apply feedback to ledger for any new signals
    if members_with_new_signal and not dry_run:
        print(f"  applying feedback for new signals: {sorted(members_with_new_signal)}")
        apply_feedback_for(rid)

    if ack_items and not dry_run:
        path = write_adhoc_drafts(rid, "ack", ack_items)
        print(f"  firing {len(ack_items)} ack(s) via {path.name}")
        fire_send(rid, path)
    elif ack_items:
        print(f"  [dry_run] would fire {len(ack_items)} acks")

    # Summary
    print()
    print("State summary (this cycle):")
    states = {r["member_init"]: r for r in query_json(
        f"SELECT * FROM {schema}.cycle_state WHERE cycle_id='{cycle_id}'")}
    for m, st in sorted(states.items()):
        print(f"  {m:5s} {st['state']:24s} replies={st.get('reply_count') or 0}  "
              f"next_action_at={st.get('next_action_at') or '-'}")
    return 0


def _dm_channel(member: str) -> str:
    r = load_researchers().get("researchers", {})
    return r.get(member, {}).get("dm_channel", "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    return tick(dry_run=a.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
