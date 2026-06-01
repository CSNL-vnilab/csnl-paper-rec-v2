#!/usr/bin/env python3
"""
scripts/weekly/dry_run_preview.py — Sunday operator preview of the digest that
Monday will send. Active only during the rollout window (design §3: first 4
weeks), then self-disables.

Builds the upcoming week's digest in DRY-RUN (reuses build_digest.py, so the
preview is exactly what Monday will stage) and posts it to the operator's Slack
DM for a GO / NO-GO read. No DB writes, no LLM.

Rollout gate: reads state/p23_dryrun_until (one line, YYYY-MM-DD KST). If today
is past that date, the script no-ops. If the file is absent it stays active and
warns the operator to set the cutoff (so it never silently runs forever).

Usage:
    python3 scripts/weekly/dry_run_preview.py            # print preview to stdout
    python3 scripts/weekly/dry_run_preview.py --send      # also DM the operator
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
from _db import load_env  # noqa: E402

KST = timezone(timedelta(hours=9))
_CUTOFF_FILE = _REPO_ROOT / "state" / "p23_dryrun_until"


def _iso_week(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def _upcoming_week() -> str:
    now = datetime.now(KST)
    days_ahead = (0 - now.weekday()) % 7  # Monday=0
    if days_ahead == 0:
        days_ahead = 7
    return _iso_week(now + timedelta(days=days_ahead))


def _within_window() -> tuple[bool, str]:
    today = datetime.now(KST).date()
    if not _CUTOFF_FILE.exists():
        # Fail-safe: self-initialise the 4-week rollout window on first run so
        # the preview can never post indefinitely (codex finding). 4 weeks =
        # 28 days from today.
        cutoff = today + timedelta(days=28)
        try:
            _CUTOFF_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CUTOFF_FILE.write_text(cutoff.isoformat() + "\n", encoding="utf-8")
        except Exception:
            pass
        return True, f"initialised rollout window — cutoff {cutoff} (4 weeks out)."
    raw = _CUTOFF_FILE.read_text("utf-8").strip()[:10]
    try:
        cutoff = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        # Malformed cutoff → fail CLOSED (do not preview) rather than forever-on.
        return False, f"⚠ unparseable cutoff {raw!r} — preview disabled (fail-closed)."
    if today > cutoff:
        return False, f"preview window closed (today {today} > cutoff {cutoff})."
    return True, f"preview active (cutoff {cutoff})."


def _operator_dm() -> tuple[str, str] | None:
    """(dm_channel, init) for the operator, from researchers.yaml + MY_INIT."""
    try:
        import yaml
        init = (os.environ.get("MY_INIT") or "JOP").strip().upper()
        d = yaml.safe_load((_REPO_ROOT / "config" / "researchers.yaml").read_text("utf-8"))
        r = (d.get("researchers") or {}).get(init) or {}
        dm = r.get("dm_channel")
        return (dm, init) if dm else None
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true",
                    help="Post the preview to the operator Slack DM "
                         "(otherwise prints to stdout).")
    ap.add_argument("--week", default=None, help="Override the previewed ISO week.")
    args = ap.parse_args()
    load_env()

    active, why = _within_window()
    print(f"[preview] {why}")
    if not active:
        return 0

    week = args.week or _upcoming_week()
    # Reuse build_digest's dry-run so the preview is byte-for-byte what Monday
    # will stage. Never pass --apply here.
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "build_digest.py"),
         "--week", week],
        capture_output=True, text=True, timeout=180,
    )
    body = proc.stdout.strip() or "(build_digest produced no output)"
    if proc.returncode != 0:
        body += f"\n\n[stderr]\n{proc.stderr.strip()[:1000]}"
    header = f"📋 P23 주간 추천 미리보기 — {week} (월요일 발송 예정, dry-run)\n"
    text = header + "```\n" + body[:3500] + "\n```"
    print(text)

    if not args.send:
        print("[preview] stdout only. Re-run with --send to DM the operator.")
        return 0

    import requests
    op = _operator_dm()
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not (op and token):
        print("[preview] cannot send: missing operator dm_channel or SLACK_BOT_TOKEN.",
              file=sys.stderr)
        return 1
    dm, init = op
    r = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=utf-8"},
        json={"channel": dm, "text": text}, timeout=20)
    ok = False
    try:
        ok = r.json().get("ok", False)
    except Exception:
        pass
    print(f"[preview] Slack DM to {init}: {'sent' if ok else 'FAILED ' + r.text[:200]}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
