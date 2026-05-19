#!/usr/bin/env python3
"""
scripts/dedup_snapshot.py — export the never-re-recommend set to a LOCAL
JSON so scouts can dedup without any DB access (rules/04).

Reads (operator-run via `!`): the csnl_paper_rec ledger
(paper_recommendations + paper_recommendations_read + exclusion_rules) and
the ported reading-DB snapshot (config/reading_db_snapshot.json).

Writes: state/runs/<RID>/_dedup_snapshot.json
  { run_id, generated_at, reading_db:[{doi,title}],
    units: { "<unit_id>": { dois:[norm], titles:[lower],
                            excluded_dois:[norm], excluded_keywords:[str] } } }

The agent-side scouts read ONLY this local file. csnl_research untouched.

    ! python scripts/dedup_snapshot.py <RID>
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from _db import load_env, query_json, ledger_schema  # noqa: E402
import yaml  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent


def _norm_doi(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    return s.strip().lower()


def _looks_like_doi(term: str) -> bool:
    return bool(re.match(r"^10\.\d{4,9}/", (term or "").strip(), re.I))


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: dedup_snapshot.py <RID>")
        return 2
    rid = sys.argv[1]
    load_env()
    schema = ledger_schema()

    units_cfg = yaml.safe_load((_ROOT / "config" / "researchers.yaml").read_text())
    unit_ids = [u["unit_id"] for u in units_cfg.get("units", [])]

    recs = query_json(
        f"SELECT unit_id, paper_doi, paper_title FROM {schema}.paper_recommendations")
    reads = query_json(
        f"SELECT unit_id, paper_doi, paper_title FROM {schema}.paper_recommendations_read")
    excl = query_json(
        f"SELECT unit_id, excluded_term FROM {schema}.exclusion_rules")

    units: dict[str, dict] = {
        uid: {"dois": set(), "titles": set(),
              "excluded_dois": set(), "excluded_keywords": set()}
        for uid in unit_ids
    }

    def bucket(uid):  # tolerate any unit_id seen in ledger
        return units.setdefault(uid, {"dois": set(), "titles": set(),
                                      "excluded_dois": set(),
                                      "excluded_keywords": set()})

    for r in recs + reads:
        b = bucket(r["unit_id"])
        if r.get("paper_doi"):
            b["dois"].add(_norm_doi(r["paper_doi"]))
        if r.get("paper_title"):
            b["titles"].add(r["paper_title"].strip().lower())
    for e in excl:
        b = bucket(e["unit_id"])
        term = (e.get("excluded_term") or "").strip()
        if not term:
            continue
        (b["excluded_dois"] if _looks_like_doi(term)
         else b["excluded_keywords"]).add(
            _norm_doi(term) if _looks_like_doi(term) else term.lower())

    reading_db = json.loads(
        (_ROOT / "config" / "reading_db_snapshot.json").read_text()).get("entries", [])
    reading_db = [
        {"doi": _norm_doi(x.get("doi", "")), "title": (x.get("title") or "").strip()}
        for x in reading_db if x.get("doi") or x.get("title")
    ]

    out = {
        "run_id": rid,
        "reading_db": reading_db,
        "units": {
            uid: {k: sorted(v) for k, v in d.items()}
            for uid, d in units.items()
        },
    }
    run_dir = _ROOT / "state" / "runs" / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    dest = run_dir / "_dedup_snapshot.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[dedup_snapshot] {dest}")
    print(f"  reading_db entries : {len(reading_db)}")
    for uid in sorted(out["units"]):
        d = out["units"][uid]
        print(f"  {uid:10s} dois={len(d['dois'])} read+rec titles={len(d['titles'])} "
              f"excl_doi={len(d['excluded_dois'])} excl_kw={len(d['excluded_keywords'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
