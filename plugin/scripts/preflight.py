#!/usr/bin/env python3
"""
plugin/scripts/preflight.py — verify the plugin's environment is ready
for /paper-interview to succeed.

Usage:
    python plugin/scripts/preflight.py <INIT>

Checks (read-only, no DB writes):
  1. .env discoverable and SUPABASE_DB_* populated.
  2. psycopg2 importable (or psql on PATH as fallback).
  3. DB connection works.
  4. archive_* tables exist in the configured schema.
  5. csnl_research.projects has >=1 active row for <INIT>.
  6. archive_researcher_queues has >=1 row for <INIT>.
  7. Sample one paper from each chunk to confirm the joined tables resolve.

Exit code 0 = all green. Non-zero = the failing check is printed in Korean.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _pdb import load_env, query, schema  # noqa: E402


def _fail(code: int, msg_ko: str) -> int:
    print(json.dumps({"ok": False, "code": code, "message_ko": msg_ko},
                     ensure_ascii=False))
    return code


def main() -> int:
    if len(sys.argv) < 2:
        return _fail(2, "researcher init를 인자로 전달해주세요. "
                        "예: /paper-interview BHL")
    init = sys.argv[1].strip().upper()

    try:
        load_env()
    except Exception as e:
        return _fail(3, f"환경 변수 로딩 실패: {e}")

    try:
        sch = schema()
    except Exception as e:
        return _fail(4, f"스키마 이름 검증 실패: {e}")

    # 1. DB reachability.
    try:
        query("SELECT 1 AS ok")
    except Exception as e:
        # Distinguish "env not loaded" from "creds wrong" — both surface as
        # connection errors, but the message + setup hint should differ.
        from _pdb import _ENV_PATHS, PLUGIN_DIR  # noqa: E402
        env_missing = not any(p.exists() for p in _ENV_PATHS)
        setup_py = PLUGIN_DIR / "scripts" / "setup.py"
        if env_missing:
            paths = "\n".join(f"    • {p}" for p in _ENV_PATHS)
            return _fail(5,
                f"Supabase 연결 정보 .env 파일이 없어요. 대화형 설정 "
                f"도우미를 실행해보세요:\n\n"
                f"  python {setup_py}\n\n"
                f"또는 직접 만들 위치 ↓\n{paths}")
        return _fail(5, f"Supabase 연결 실패. .env 의 SUPABASE_DB_* 값을 "
                        f"확인해주세요. 다시 셋업하려면:\n"
                        f"  python {setup_py} --force\n"
                        f"(원본 오류: {e})")

    # 2. Tables exist.
    expect = (
        "archive_papers", "archive_filter_decisions",
        "archive_researcher_queues", "archive_interview_sessions",
        "archive_responses", "archive_meta_reviews",
        "archive_profile_verifications",
    )
    present = {r["table_name"] for r in query(
        f"SELECT table_name FROM information_schema.tables "
        f"WHERE table_schema = '{sch}'"
    )}
    missing = [t for t in expect if t not in present]
    if missing:
        return _fail(6, f"운영자가 아직 schema_archive 를 적용하지 않은 듯합니다. "
                        f"누락된 테이블: {missing}. 운영자에게 문의해주세요.")

    # 3. Active projects.
    projs = query(
        "SELECT project_slug, title, phase, confidence_avg "
        "FROM csnl_research.projects "
        "WHERE init = %s "
        "  AND phase IN ('data_collection','analysis','manuscript_draft') "
        "  AND confidence_avg >= 0.7 "
        "ORDER BY project_slug",
        (init,),
    )
    if not projs:
        return _fail(7, f"{init} 에 해당하는 활성 프로젝트가 csnl_research 에 "
                        f"없습니다. CSNL 자가아카이브로 프로젝트 정보를 먼저 "
                        f"업데이트해주세요.")

    # 4. Queue ready.
    q_count = query(
        f"SELECT chunk, COUNT(*) AS n FROM {sch}.archive_researcher_queues "
        f"WHERE researcher_id = %s GROUP BY chunk",
        (init,),
    )
    by_chunk = {r["chunk"]: int(r["n"]) for r in q_count}
    if not by_chunk:
        return _fail(8, f"{init} 의 추천 큐가 아직 생성되지 않았습니다. "
                        f"운영자가 build_researcher_queue.py {init} --apply "
                        f"를 실행해야 합니다.")

    # 5. Joined-row sanity probe (one paper per chunk).
    samples = {}
    for ch in ("recent", "mid", "classic"):
        rows = query(
            f"SELECT q.canonical_id, p.title, p.year "
            f"  FROM {sch}.archive_researcher_queues q "
            f"  JOIN {sch}.archive_papers p ON p.canonical_id = q.canonical_id "
            f" WHERE q.researcher_id = %s AND q.chunk = %s "
            f" ORDER BY q.rank_in_chunk LIMIT 1",
            (init, ch),
        )
        samples[ch] = rows[0] if rows else None

    print(json.dumps({
        "ok":             True,
        "researcher_id":  init,
        "schema":         sch,
        "n_active_projects": len(projs),
        "queue_by_chunk": by_chunk,
        "sample_per_chunk": samples,
        "message_ko": "준비 완료. /paper-interview 명령으로 인터뷰를 시작하세요.",
    }, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
