#!/usr/bin/env python3
"""
plugin/scripts/setup.py — interactive .env builder.

Researchers run this once after installing the plugin. It prompts for
the 5 connection values (host / user / password / port / schema), writes
~/.csnl-paper-archive/.env with `chmod 600`, then runs preflight to
verify the credentials work.

Designed to be robust to old Python (3.8+) and to be useful even when
the researcher cannot find the .env file path on their machine.

Usage:
    python plugin/scripts/setup.py
    python plugin/scripts/setup.py --init JOP   # skip the init prompt
    python plugin/scripts/setup.py --force      # overwrite existing .env
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

# Python version guard — print Korean error if too old, exit cleanly.
if sys.version_info < (3, 8):
    print(
        f"이 플러그인은 Python 3.8 이상이 필요합니다 "
        f"(현재: {sys.version_info[0]}.{sys.version_info[1]}). "
        f"`brew install python@3.11` 또는 pyenv 로 새 버전을 설치한 뒤 "
        f"다시 시도해주세요.",
        file=sys.stderr,
    )
    sys.exit(2)

PLUGIN_DIR = Path(__file__).resolve().parent.parent
HOME_ENV   = Path.home() / ".csnl-paper-archive" / ".env"


def _prompt(label: str, default: str = "", secret: bool = False) -> str:
    """Read a single value from stdin, with a default + optional masking."""
    suffix = f" [{default}]" if default else ""
    line = f"  {label}{suffix}: "
    if secret:
        v = getpass.getpass(line)
    else:
        v = input(line)
    v = v.strip()
    return v if v else default


def _confirm(label: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    ans = input(f"  {label} {suffix}: ").strip().lower()
    if not ans:
        return default
    return ans in ("y", "yes", "ㅇ", "예", "네")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", default=None,
                    help="Researcher init (e.g. JOP). If set, runs preflight "
                         "automatically at the end.")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite an existing .env without asking.")
    args = ap.parse_args()

    # P16 audit fix: getpass.getpass() echoes the password when no TTY is
    # attached. Refuse if stdin isn't a terminal (i.e. Claude-Code-spawned
    # subprocess) — direct researchers to run this from a real shell.
    if not sys.stdin.isatty():
        print(
            "이 스크립트는 비밀번호를 안전하게 받기 위해 실제 터미널에서 "
            "직접 실행해주세요. Claude Code 채팅에 붙여넣지 말고, 별도의 "
            "터미널 창을 열어서 같은 명령을 입력해주세요.",
            file=sys.stderr,
        )
        return 2

    print()
    print("CSNL paper-archive-interview — 환경 설정")
    print("=" * 50)
    print(f"저장 위치: {HOME_ENV}")
    print()

    if HOME_ENV.exists() and not args.force:
        print(f"기존 .env 파일이 이미 있어요: {HOME_ENV}")
        if not _confirm("덮어쓸까요?", default=False):
            print("취소했어요. 기존 파일은 그대로 두었습니다.")
            return 0
        print()

    print("운영자가 1Password / sealed DM 으로 보낸 값을 입력해주세요.")
    print("(엔터만 누르면 기본값을 사용합니다)")
    print()

    vals = {}
    vals["SUPABASE_DB_HOST"] = _prompt(
        "SUPABASE_DB_HOST",
        default="aws-1-ap-southeast-1.pooler.supabase.com",
    )
    if not vals["SUPABASE_DB_HOST"]:
        print("\n호스트가 비어 있어요. 취소합니다.", file=sys.stderr)
        return 2

    vals["SUPABASE_DB_PORT"] = _prompt("SUPABASE_DB_PORT", default="5432")
    vals["SUPABASE_DB_NAME"] = _prompt("SUPABASE_DB_NAME", default="postgres")
    vals["SUPABASE_DB_USER"] = _prompt("SUPABASE_DB_USER")
    if not vals["SUPABASE_DB_USER"]:
        print("\nDB 사용자명이 비어 있어요. 취소합니다.", file=sys.stderr)
        return 2

    vals["SUPABASE_DB_PASSWORD"] = _prompt(
        "SUPABASE_DB_PASSWORD (입력 시 안 보임)", secret=True,
    )
    if not vals["SUPABASE_DB_PASSWORD"]:
        print("\n비밀번호가 비어 있어요. 취소합니다.", file=sys.stderr)
        return 2

    vals["CPR_LEDGER_SCHEMA"] = _prompt(
        "CPR_LEDGER_SCHEMA", default="csnl_paper_rec",
    )

    HOME_ENV.parent.mkdir(parents=True, exist_ok=True)
    HOME_ENV.write_text(
        "\n".join(f"{k}={v}" for k, v in vals.items()) + "\n",
        encoding="utf-8",
    )
    try:
        HOME_ENV.chmod(0o600)
    except OSError:
        # Windows / unusual filesystems may not support unix perms.
        pass

    print()
    print(f"  저장 완료: {HOME_ENV}  (chmod 600)")
    print()

    init = args.init
    if not init:
        init = input("연구원 init 알려주시면 연결 테스트를 진행할게요 "
                     "(skip: 엔터): ").strip().upper()

    if not init:
        print("준비 끝났어요. Claude Code 세션에서")
        print(f"  /csnl-paper-archive-interview:paper-interview <YOUR_INIT>")
        print("로 시작하세요.")
        return 0

    print()
    print(f"연결 테스트 중... (init={init})")
    print("-" * 50)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    # Re-load env so the freshly-written values take effect.
    for k, v in vals.items():
        os.environ[k] = v
    try:
        import preflight  # noqa: E402
    except Exception as e:
        print(f"preflight 로딩 실패: {e}", file=sys.stderr)
        return 3
    sys.argv = ["preflight.py", init]
    return preflight.main()


if __name__ == "__main__":
    raise SystemExit(main())
