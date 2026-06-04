#!/usr/bin/env python3
"""
scripts/weekly/notify.py — weekly PaperBlitz email reminders.

Two touchpoints (run by run_weekly_cron.sh):
  --kind new_recs    (Wed 14:00, right after the refill routine): "이번 주 추천
                      N편 도착 — 1편 읽고 다음 수요일 발표 준비".
  --kind present_day (Wed 08:00, morning of PaperBlitz): "오늘 14:00 PaperBlitz
                      — 발표할 1편 확인".

Personalised per researcher from their ACTIVE board (archive_weekly_digests,
response_choice IS NULL): top pick highlighted (read 1 → present), the rest
listed. Sends via SMTP.

Config:
  - researcher emails: config/researchers.yaml  ->  researchers.<INIT>.email
  - SMTP creds in .env: SMTP_HOST (default smtp.gmail.com), SMTP_PORT (465,
    SSL), SMTP_USER, SMTP_PASS, SMTP_FROM (default SMTP_USER).
    (For Gmail use vnilab@gmail.com + a Gmail App Password as SMTP_PASS.)

Boundary: researcher-facing send — gated behind --send (dry-run prints the
emails otherwise). Reads Postgres + sends email; no DB writes. If creds or a
researcher email are missing it prints a clear notice and (for --send) skips
that recipient rather than crashing.

Usage:
    python3 scripts/weekly/notify.py --kind new_recs            # dry-run
    python3 scripts/weekly/notify.py --kind present_day --send   # send
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
from _db import load_env, query_json, ledger_schema  # noqa: E402

_TIER_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3}


def _researchers_cfg() -> dict:
    import yaml
    d = yaml.safe_load((_REPO_ROOT / "config" / "researchers.yaml").read_text("utf-8"))
    return d.get("researchers") or {}


def _digest_db_url() -> str:
    load_env()
    dbid = (os.environ.get("NOTION_DIGEST_DB_ID") or "").replace("-", "")
    return f"https://www.notion.so/{dbid}" if dbid else "(Notion 이번 주 논문 추천 DB)"


def _active_board(sch: str) -> dict[str, list[dict]]:
    """{researcher_id: [active digest rows, best-first]} (response_choice NULL)."""
    rows = query_json(f"""
        SELECT w.researcher_id, w.tier_at_send, w.rank_in_digest,
               p.title, p.year, p.venue, p.doi
          FROM {sch}.archive_weekly_digests w
          JOIN {sch}.archive_papers p ON p.canonical_id = w.canonical_id
         WHERE w.response_choice IS NULL AND w.notion_page_id IS NOT NULL
         ORDER BY w.researcher_id, w.rank_in_digest
    """)
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["researcher_id"], []).append(r)
    for rid in out:
        out[rid].sort(key=lambda x: (_TIER_ORDER.get(x["tier_at_send"], 9),
                                     x["rank_in_digest"] or 99))
    return out


def _fmt_paper(p: dict) -> str:
    t = (p.get("title") or "").strip()
    meta = ", ".join(x for x in [str(p.get("year") or "").strip(),
                                 (p.get("venue") or "").strip()] if x)
    line = f"[{p.get('tier_at_send')}] {t}" + (f" ({meta})" if meta else "")
    doi = (p.get("doi") or "").strip()
    if doi:
        line += f"\n      https://doi.org/{doi}"
    return line


def _compose(kind: str, name: str, papers: list[dict], url: str) -> tuple[str, str]:
    nm = name or ""
    if not papers:
        if kind == "present_day":
            subj = "[CSNL 논문 추천] 오늘 14:00 PaperBlitz"
            body = (f"{nm} 연구원님,\n\n오늘 수요일 14:00 PaperBlitz 입니다.\n"
                    f"이번 주 추천 목록이 비어 있습니다 — 운영자에게 문의하시거나 "
                    f"Notion 에서 확인해 주세요.\n\n{url}\n")
            return subj, body
        subj = "[CSNL 논문 추천] 이번 주 추천"
        body = f"{nm} 연구원님,\n\n이번 주 추천 목록이 아직 없습니다.\n\n{url}\n"
        return subj, body

    top = papers[0]
    rest = papers[1:]
    listing = "\n".join(f"  {i+1}. {_fmt_paper(p)}" for i, p in enumerate(papers))
    if kind == "present_day":
        subj = "[CSNL 논문 추천] 오늘 14:00 PaperBlitz — 발표할 1편 확인"
        body = (
            f"{nm} 연구원님,\n\n"
            f"오늘 수요일 14:00 PaperBlitz 입니다. 이번 주 추천에서 읽으신 1편을 "
            f"5분 발표해 주세요.\n"
            f"아직 정하지 않으셨다면 추천 1순위부터 확인하시고, Notion 에서 발표할 "
            f"논문에 '🎤 발표 예정' 을 체크해 주세요.\n\n"
            f"▶ 발표 1순위 추천: {_fmt_paper(top)}\n\n"
            f"이번 주 미독 {len(papers)}편:\n{listing}\n\n"
            f"Notion 이번 주 논문 추천: {url}\n"
        )
    else:  # new_recs
        subj = f"[CSNL 논문 추천] 이번 주 추천 {len(papers)}편 — 1편 읽고 수요일 발표 준비"
        body = (
            f"{nm} 연구원님,\n\n"
            f"이번 주 논문 추천 {len(papers)}편이 도착했습니다. 1편 이상 읽으시고, "
            f"다음 수요일 PaperBlitz 에서 발표할 1편을 준비해 주세요.\n"
            f"읽으신 논문은 Notion 에서 '읽음' 체크 → 자동으로 '논문 리스트' 로 "
            f"이동하고 새 논문 1편이 추천됩니다. 발표할 1편엔 '🎤 발표 예정' 체크.\n\n"
            f"▶ 1순위: {_fmt_paper(top)}\n\n"
            + (f"이번 주 추천:\n{listing}\n\n" if rest else "")
            + f"Notion 이번 주 논문 추천: {url}\n"
        )
    return subj, body


def _smtp_cfg() -> dict | None:
    load_env()
    user = os.environ.get("SMTP_USER", "").strip()
    pw = os.environ.get("SMTP_PASS", "").strip()
    if not (user and pw):
        return None
    return {
        "host": os.environ.get("SMTP_HOST", "smtp.gmail.com").strip(),
        "port": int(os.environ.get("SMTP_PORT", "465")),
        "user": user, "pw": pw,
        "from": os.environ.get("SMTP_FROM", user).strip(),
    }


def _send(cfg: dict, to_addr: str, subject: str, body: str) -> None:
    import smtplib
    from email.mime.text import MIMEText
    from email.header import Header
    from email.utils import formataddr
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header("CSNL 논문 추천", "utf-8")), cfg["from"]))
    msg["To"] = to_addr
    if cfg["port"] == 465:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=30) as s:
            s.login(cfg["user"], cfg["pw"])
            s.sendmail(cfg["from"], [to_addr], msg.as_string())
    else:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as s:
            s.starttls()
            s.login(cfg["user"], cfg["pw"])
            s.sendmail(cfg["from"], [to_addr], msg.as_string())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True, choices=("new_recs", "present_day"))
    ap.add_argument("--send", action="store_true", help="Actually send (else dry-run).")
    ap.add_argument("--only", default=None, help="One researcher only.")
    args = ap.parse_args()
    load_env()
    sch = ledger_schema()
    cfg_r = _researchers_cfg()
    url = _digest_db_url()
    board = _active_board(sch)

    # enabled researchers (consented to weekly delivery)
    enabled = [r["researcher_id"] for r in query_json(
        f"SELECT researcher_id FROM {sch}.archive_researcher_channels "
        f"WHERE channel_type='notion' AND enabled=true ORDER BY researcher_id")]
    if args.only:
        enabled = [r for r in enabled if r == args.only.strip().upper()]

    smtp = _smtp_cfg()
    if args.send and not smtp:
        print("[notify] ABORT --send: SMTP_USER/SMTP_PASS not set in .env "
              "(use vnilab@gmail.com + a Gmail App Password).", file=sys.stderr)
        return 2

    sent = skipped = 0
    for rid in enabled:
        r = cfg_r.get(rid) or {}
        email = (r.get("email") or "").strip()
        name = r.get("name") or rid
        subj, body = _compose(args.kind, name, board.get(rid, []), url)
        if not args.send:
            tag = email or f"<no email — set researchers.{rid}.email>"
            print(f"\n----- {rid} <{tag}> [{args.kind}] -----\nSubject: {subj}\n{body}")
            continue
        if not email:
            print(f"[notify] {rid}: ⚠ no email in researchers.yaml — skip "
                  f"(add researchers.{rid}.email).", file=sys.stderr)
            skipped += 1
            continue
        try:
            _send(smtp, email, subj, body)
            sent += 1
            print(f"[notify] sent {args.kind} → {rid} <{email}>")
        except Exception as e:
            skipped += 1
            print(f"[notify] FAIL {rid} <{email}>: {type(e).__name__}: {str(e)[:160]}",
                  file=sys.stderr)
    if not args.send:
        print(f"\n[notify] dry-run ({args.kind}). {len(enabled)} researcher(s); "
              f"re-run with --send (needs SMTP creds + researchers.*.email).")
    else:
        print(f"[notify] done — sent={sent} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
