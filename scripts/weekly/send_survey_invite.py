#!/usr/bin/env python3
"""One-off: email the P27 research-profile survey invite to 6 researchers (Cc: PI).

Subject: 연구자 설문 완성 요청
- Group email: To = 6 researchers (MSY excluded), Cc = Sang-Hun Lee.
- Body: lab-DB achievement so far + survey purpose + per-person Notion link +
  deadline (tomorrow 8pm) + Paper Blitz suggestion + thanks. Polite, concise, no hype.

SMTP creds from .env: SMTP_HOST (default smtp.gmail.com), SMTP_PORT (465 SSL),
SMTP_USER, SMTP_PASS, SMTP_FROM (default SMTP_USER).

Boundary: researcher-facing send — DRY-RUN by default; prints the full message.
Pass --send to actually send (needs SMTP creds). Each survey Notion page must first
be shared with its researcher (the integration can create pages but cannot invite
people); otherwise the links return "no access".

Usage:
    python3 scripts/weekly/send_survey_invite.py            # dry-run preview
    python3 scripts/weekly/send_survey_invite.py --send     # actually send
"""
import argparse
import os
import smtplib
import sys
from email.message import EmailMessage
from email.utils import formataddr

sys.path.insert(0, os.path.dirname(__file__))
try:
    from _db import load_env  # loads .env into os.environ
except Exception:
    def load_env():
        p = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

SUBJECT = "연구자 설문 완성 요청"

# (display name, email, notion page id) — MSY excluded by request
RECIPIENTS = [
    ("정세미", "jsaemi22@gmail.com", "3762a38e-4f5f-8192-9dc8-f2ee905adcec"),
    ("이보연", "bolee0755@gmail.com", "3762a38e-4f5f-8163-9594-e9ffb7755431"),
    ("이보현", "leebohyun2002@gmail.com", "3762a38e-4f5f-8155-baff-d7282261ab29"),
    ("김정예", "jy061100@gmail.com", "3762a38e-4f5f-8180-9000-e402f1542518"),
    ("조수영", "drawing987@gmail.com", "3762a38e-4f5f-8175-99a4-fe5cd61596fe"),
    ("박준오", "joonop99@snu.ac.kr", "3762a38e-4f5f-81d5-a4c6-c7b454a8a04c"),
]
CC = [("Sang-Hun Lee", "sanghun.lee.vni@gmail.com")]


def page_url(pid: str) -> str:
    return "https://www.notion.so/" + pid.replace("-", "")


def build_body() -> str:
    links = "\n".join(f"· {name}: {page_url(pid)}" for name, _, pid in RECIPIENTS)
    return f"""안녕하세요, 연구원님들.

CSNL 논문 추천 시스템 관련하여 협조를 부탁드리고자 메일 드립니다.

지난 기간 동안 연구실 아카이브에 논문 약 2,000편의 구조화된 요약과 연구원별 맞춤 추천 큐(각 200편)를 구축했고, 인터뷰를 통해 약 500건의 응답을 모았습니다. 다만 일부 추천이 연구 범위를 벗어난다는 의견이 있어, 각 연구원의 연구 프로파일을 더 정확히 정리하고자 합니다.

이를 위해 연구원별 설문 페이지(Notion)를 준비했습니다. 기존 메모리로 채울 수 있는 부분은 미리 채워두었으니, 맞는지 확인하시고 빈 부분만 보완해 주시면 됩니다.

{links}

마감: 내일(6월 9일) 오후 8시까지 부탁드립니다.

아울러, 그동안 추천받으신 논문 중 한 편을 골라 이번 주 수요일 Paper Blitz에서 짧게 발표해 주시면 감사하겠습니다.

바쁘신 와중에 시간 내어 주셔서 감사합니다.

CSNL 논문 추천 시스템 운영 드림
"""


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="Actually send (else dry-run).")
    args = ap.parse_args()

    user = os.environ.get("SMTP_USER", "").strip()
    pw = os.environ.get("SMTP_PASS", "").strip()
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
    port = int(os.environ.get("SMTP_PORT", "465"))
    sender = os.environ.get("SMTP_FROM", user).strip()

    msg = EmailMessage()
    msg["Subject"] = SUBJECT
    msg["From"] = sender
    msg["To"] = ", ".join(formataddr((n, e)) for n, e, _ in RECIPIENTS)
    msg["Cc"] = ", ".join(formataddr((n, e)) for n, e in CC)
    msg.set_content(build_body())
    all_rcpts = [e for _, e, _ in RECIPIENTS] + [e for _, e in CC]

    print("=" * 70)
    print(f"Subject: {msg['Subject']}")
    print(f"From:    {msg['From']}")
    print(f"To:      {msg['To']}")
    print(f"Cc:      {msg['Cc']}")
    print("-" * 70)
    print(build_body())
    print("=" * 70)

    if not args.send:
        print(f"[dry-run] not sent. {len(all_rcpts)} recipients. Re-run with --send "
              f"(needs SMTP creds; each Notion page must be shared with its researcher).")
        return
    if not (user and pw):
        print("[ABORT --send] SMTP_USER/SMTP_PASS not set in .env.")
        sys.exit(2)
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            s.login(user, pw)
            s.sendmail(sender, all_rcpts, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(user, pw)
            s.sendmail(sender, all_rcpts, msg.as_string())
    print(f"[sent] survey invite → {len(all_rcpts)} recipients (To 6 + Cc 1).")


if __name__ == "__main__":
    main()
