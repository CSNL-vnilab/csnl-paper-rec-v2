#!/usr/bin/env python3
"""
scripts/build_scout_briefs.py — pg-interest-reader role, deterministic.

Merges the operator-produced local artifacts into per-unit scout briefs:
  state/runs/<RID>/01_active_projects.json   (csnl_research, read-only)
  state/runs/<RID>/02_topic_bundles.json     (unit map + keywords + anchors)
  state/runs/<RID>/_dedup_snapshot.json      (never-re-recommend set)
→ state/runs/<RID>/_scout_briefs.json        (index)
  state/runs/<RID>/brief_<unit>.json         (one self-contained brief/unit)

No network, no DB, no LLM — pure assembly + criteria re-validation. Run by
the orchestrator (local). Scouts read their brief_<unit>.json + the shared
_dedup_snapshot.json (reading_db) and never touch a database.
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PHASES = {"data_collection", "analysis", "manuscript_draft"}


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: build_scout_briefs.py <RID>")
        return 2
    rid = sys.argv[1]
    rd = _ROOT / "state" / "runs" / rid
    proj = json.loads((rd / "01_active_projects.json").read_text())["projects"]
    bundles = json.loads((rd / "02_topic_bundles.json").read_text())["units"]
    dedup = json.loads((rd / "_dedup_snapshot.json").read_text())

    by_init: dict[str, list] = {}
    flagged = []
    for p in proj:
        if p["phase"] in _PHASES and float(p["confidence_avg"]) >= 0.7:
            by_init.setdefault(p["init"], []).append(p)
        else:
            flagged.append({"init": p["init"], "slug": p["project_slug"],
                            "phase": p["phase"], "conf": p["confidence_avg"],
                            "reason": "violates phase∈{...}∧conf≥0.7"})

    index = {"run_id": rid,
             "dedup_snapshot_path": f"state/runs/{rid}/_dedup_snapshot.json",
             "reading_db_count": len(dedup.get("reading_db", [])),
             "flagged_rows": flagged, "units": []}

    for b in bundles:
        uid = b["unit_id"]
        rows = []
        for init in b["members"]:
            rows.extend(by_init.get(init, []))
        terms = dedup["units"].get(uid, {"dois": [], "titles": [],
                                         "excluded_dois": [], "excluded_keywords": []})
        brief = {
            "run_id": rid, "unit_id": uid,
            "members": b["members"], "display_names": b["display_names"],
            "channel_ids": b["channel_ids"], "dm_inits": b["dm_inits"],
            "project_slugs": b["project_slugs"], "keywords": b["keywords"],
            "anchor_dois": b["anchor_dois"], "gist": b["gist"],
            "projects": rows,                       # full csnl_research rows
            "dedup_terms": terms,
            "reading_db_path": index["dedup_snapshot_path"],
            "no_active_projects": len(rows) == 0,
            "window": {"today": "2026-05-19",
                       "since_journal": "2025-05-19",   # today − 365 d (strict)
                       "since_preprint": "2026-02-19"},  # today − 90 d (strict)
        }
        (rd / f"brief_{uid.replace('+','_')}.json").write_text(
            json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
        index["units"].append({
            "unit_id": uid, "members": b["members"],
            "n_projects": len(rows), "n_keywords": len(b["keywords"]),
            "n_anchor_dois": len(b["anchor_dois"]),
            "n_dedup_dois": len(terms["dois"]),
            "n_excl_kw": len(terms["excluded_keywords"]),
            "no_active_projects": len(rows) == 0,
            "brief": f"state/runs/{rid}/brief_{uid.replace('+','_')}.json"})

    (rd / "_scout_briefs.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[build_scout_briefs] wrote _scout_briefs.json + {len(index['units'])} brief files")
    for u in index["units"]:
        print(f"  {u['unit_id']:8s} proj={u['n_projects']} kw={u['n_keywords']} "
              f"anchors={u['n_anchor_dois']} dedup_dois={u['n_dedup_dois']} "
              f"excl_kw={u['n_excl_kw']} active={not u['no_active_projects']}")
    if flagged:
        print(f"  flagged_rows: {flagged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
