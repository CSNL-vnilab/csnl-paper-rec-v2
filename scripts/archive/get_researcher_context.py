#!/usr/bin/env python3
"""
scripts/archive/get_researcher_context.py — retrieval-priming layer.

Any future Claude session that helps a CSNL researcher should start by
loading this researcher's "research context" so its reasoning is primed:
  • What research are they doing right now? (csnl_research.projects)
  • What scientific vocabulary do they actually use? (fingerprint phrases)
  • What dim_preferences have they confirmed/updated? (latest archive_profile_verifications)
  • What have they recently read? (archive_responses where choice='already_read')
  • What are they planning to read? (choice='save_later')
  • What are they NOT interested in? (choice='not_relevant')

This is the persistent payoff of the archive interview: instead of every
new Claude session re-asking "what do you work on?" the context is fetched
from the DB in <1s.

Output: JSON to stdout (machine-readable) or --human for terminal-friendly.

Usage:
    python3 scripts/archive/get_researcher_context.py JOP
    python3 scripts/archive/get_researcher_context.py JOP --human
    python3 scripts/archive/get_researcher_context.py JOP --recent-days 30
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))

from _db import load_env, query_json  # noqa: E402


def _load_fingerprint(rid: str) -> dict:
    path = _REPO_ROOT / "state" / "archive" / "fingerprints" / f"{rid}.json"
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _load_dim_prefs(rid: str) -> dict | None:
    rows = query_json(
        f"SELECT dim_preferences, confirmed_at "
        f"FROM csnl_paper_rec.archive_profile_verifications "
        f"WHERE researcher_id = '{rid}' "
        f"ORDER BY confirmed_at DESC LIMIT 1"
    )
    if not rows:
        return None
    r = rows[0]
    dp = r.get("dim_preferences")
    if isinstance(dp, str):
        try:
            dp = json.loads(dp)
        except (ValueError, TypeError):
            dp = None
    if isinstance(dp, dict):
        dp["_confirmed_at"] = r.get("confirmed_at")
    return dp


def _load_recent_papers(rid: str, choice: str, days: int, limit: int) -> list[dict]:
    cutoff = (
        "current_date - interval '{} days'".format(int(days))
        if days > 0 else "'1900-01-01'"
    )
    return query_json(
        f"SELECT r.canonical_id, r.responded_at, r.choice_detail, "
        f"       p.title, p.year, p.authors_json, p.venue "
        f"FROM csnl_paper_rec.archive_responses r "
        f"JOIN csnl_paper_rec.archive_papers p "
        f"  ON p.canonical_id = r.canonical_id "
        f"WHERE r.researcher_id = '{rid}' "
        f"  AND r.choice = '{choice}' "
        f"  AND r.responded_at::date >= {cutoff} "
        f"ORDER BY r.responded_at DESC LIMIT {int(limit)}"
    )


def _load_projects(rid: str) -> list[dict]:
    return query_json(
        f"SELECT init, project_slug, title, phase, "
        f"       purpose_jsonb, apparatus_jsonb, modalities_jsonb, "
        f"       experiment_design_jsonb, last_updated_at "
        f"FROM csnl_research.projects "
        f"WHERE init = '{rid}' AND phase IN ('active','planning','data_collection','analysis','writing') "
        f"ORDER BY last_updated_at DESC NULLS LAST"
    )


def _build_context(rid: str, recent_days: int, limit: int) -> dict:
    fp = _load_fingerprint(rid)
    return {
        "researcher_id": rid,
        "projects": _load_projects(rid),
        "fingerprint": {
            "phrases": (fp.get("phrases") or [])[:30],
            "novel_terms": (fp.get("novel_terms") or [])[:20],
            "tag_priors": fp.get("tag_priors") or {},
            "method_signature": fp.get("method_signature") or {},
        },
        "dim_preferences_latest": _load_dim_prefs(rid),
        "recent_already_read":   _load_recent_papers(rid, "already_read", recent_days, limit),
        "recent_save_later":     _load_recent_papers(rid, "save_later",   recent_days, limit),
        "recent_not_relevant":   _load_recent_papers(rid, "not_relevant", recent_days, limit),
    }


def _human(ctx: dict) -> str:
    out = []
    out.append(f"# Research context — {ctx['researcher_id']}\n")
    projs = ctx.get("projects") or []
    if projs:
        out.append("## 진행 중인 프로젝트")
        for p in projs:
            name = p.get("title") or p.get("project_slug") or "(이름 없음)"
            phase = p.get("phase") or ""
            purpose = p.get("purpose_jsonb") or {}
            if isinstance(purpose, str):
                try:
                    purpose = json.loads(purpose)
                except (ValueError, TypeError):
                    purpose = {}
            qline = (purpose.get("research_question") or purpose.get("aim") or
                     purpose.get("hypothesis") or "")[:200] if isinstance(purpose, dict) else ""
            head = f"- **{name}**"
            if phase:
                head += f" _(phase: {phase})_"
            out.append(head)
            if qline:
                out.append(f"  · {qline}")
        out.append("")

    fp = ctx.get("fingerprint") or {}
    phrases = fp.get("phrases") or []
    if phrases:
        out.append("## 과학적 어휘 (top phrases)")
        # phrases is list of [phrase, score] or list of dicts
        for x in phrases[:15]:
            if isinstance(x, (list, tuple)) and len(x) >= 1:
                out.append(f"- {x[0]}")
            elif isinstance(x, dict):
                out.append(f"- {x.get('phrase') or x.get('term') or x}")
            else:
                out.append(f"- {x}")
        out.append("")

    dp = ctx.get("dim_preferences_latest") or {}
    if dp:
        out.append(f"## 차원 선호 (최근 confirm: {dp.get('_confirmed_at')})")
        for dim in ("focus", "method", "stim", "subj"):
            d = dp.get(dim) or {}
            if isinstance(d, dict) and d:
                top = sorted(d.items(), key=lambda kv: -float(kv[1]))[:5]
                out.append(f"- {dim}: " + ", ".join(f"{k}={v:.2f}" for k, v in top))
        out.append("")

    for label, key in (
        ("최근 읽은 paper",     "recent_already_read"),
        ("읽을 예정 paper",     "recent_save_later"),
        ("관심 없는 paper",     "recent_not_relevant"),
    ):
        papers = ctx.get(key) or []
        if not papers:
            continue
        out.append(f"## {label} (최근 {len(papers)}편)")
        for p in papers[:8]:
            t = (p.get("title") or "")[:80]
            out.append(f"- {p.get('year') or '----'} · {t}")
        out.append("")

    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("researcher")
    ap.add_argument("--recent-days", type=int, default=60)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--human", action="store_true", help="Human-readable Korean (default: JSON)")
    args = ap.parse_args()

    load_env()
    rid = args.researcher.strip().upper()
    ctx = _build_context(rid, args.recent_days, args.limit)

    if args.human:
        print(_human(ctx))
    else:
        print(json.dumps(ctx, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
