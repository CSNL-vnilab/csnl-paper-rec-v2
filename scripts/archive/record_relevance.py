#!/usr/bin/env python3
"""
scripts/archive/record_relevance.py — deterministic write helper for the P26b
live-discovery scouts.

A scout (fresh Opus subagent) does the hard part — search + read + A/B/C
judgement — and emits a JSON batch on stdin. THIS script does the brittle part
deterministically: validate, UPSERT into csnl_paper_rec.archive_relevance_decisions,
and append genuinely-new accepted papers to the per-researcher found.jsonl for
later P26d ingest. Centralizing the write means every scout writes correctly and
the orchestrator never trusts hand-rolled SQL.

BOUNDARY: writes ONLY archive_relevance_decisions (+ the local found.jsonl).
Never archive_responses (무손상), archive_papers, or csnl_research.

stdin JSON:
  {
    "researcher_id": "BHL",
    "scout_version": "p26b-live-2026-06-04",
    "source": "live",                       # default 'live'
    "decisions": [
      {"doi": "10...", "title": "...", "year": 2023, "venue": "...",
       "abstract": "...", "relevance_type": "A|B|C|none",
       "reason": "...", "confidence": "low|medium|high",
       "source_api": "openalex|europepmc|arxiv|s2",
       "canonical_id": "<optional; computed from doi/title/year if absent>"}
    ]
  }

stdout: one-line JSON summary {researcher, upserted, by_type, new_appended, in_archive}.
Usage (from a scout):  echo "$JSON" | python3 scripts/archive/record_relevance.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ROOT / "pipeline"))
sys.path.insert(0, str(_HERE))

import _db  # noqa: E402
from _common import canonical_id as _canon  # noqa: E402

_FOUND_DIR = _ROOT / "state" / "archive" / "discovery_run" / "found"
_RID_RE = re.compile(r"^[A-Z]{2,8}$")
_CANON_RE = re.compile(r"^[a-f0-9]{32}$")
_VALID_TYPE = {"A", "B", "C", "none"}
_VALID_CONF = {"low", "medium", "high", None}


def _die(msg: str) -> None:
    print(json.dumps({"error": msg}), file=sys.stdout)
    sys.exit(2)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception as e:  # noqa: BLE001
        _die(f"bad stdin json: {e}")

    rid = str(payload.get("researcher_id", "")).strip()
    if not _RID_RE.match(rid):
        _die(f"bad researcher_id {rid!r} (need ^[A-Z]{{2,8}}$)")
    scout_version = str(payload.get("scout_version", "")).strip()
    if not scout_version:
        _die("missing scout_version")
    source = str(payload.get("source", "live")).strip()
    if source not in ("live", "archive"):
        _die(f"bad source {source!r}")
    decisions = payload.get("decisions") or []
    if not isinstance(decisions, list):
        _die("decisions must be a list")

    _db.load_env()
    schema = _db.ledger_schema()

    # ---- normalize + validate each decision --------------------------------
    norm = []          # (canonical_id, relevance_type, reason, source, confidence, scout_version)
    meta_by_cid = {}   # cid -> full metadata (for found.jsonl)
    seen = set()
    for d in decisions:
        rt = str(d.get("relevance_type", "")).strip()
        if rt not in _VALID_TYPE:
            _die(f"bad relevance_type {rt!r}")
        conf = d.get("confidence")
        conf = conf.strip() if isinstance(conf, str) and conf.strip() else None
        if conf not in _VALID_CONF:
            _die(f"bad confidence {conf!r}")
        cid = str(d.get("canonical_id", "")).strip().lower()
        if not cid:
            doi = (d.get("doi") or "").strip()
            title = (d.get("title") or "").strip()
            year = d.get("year")
            try:
                cid = _canon(doi, title, int(year) if year else 0)
            except Exception as e:  # noqa: BLE001
                _die(f"cannot compute canonical_id for {title[:40]!r}: {e}")
        if not _CANON_RE.match(cid):
            _die(f"bad canonical_id {cid!r} (need 32-hex)")
        if cid in seen:
            continue
        seen.add(cid)
        reason = (d.get("reason") or "").strip()[:300]
        norm.append((cid, rt, reason, source, conf, scout_version))
        meta_by_cid[cid] = {
            "canonical_id": cid,
            "doi": (d.get("doi") or "").strip(),
            "title": (d.get("title") or "").strip(),
            "year": d.get("year"),
            "venue": (d.get("venue") or "").strip(),
            "abstract": (d.get("abstract") or "").strip(),
            "relevance_type": rt,
            "reason": reason,
            "confidence": conf,
            "source_api": (d.get("source_api") or "").strip(),
            "scout_version": scout_version,
        }

    if not norm:
        print(json.dumps({"researcher": rid, "upserted": 0, "by_type": {},
                          "new_appended": 0, "in_archive": 0}))
        return

    # ---- UPSERT into archive_relevance_decisions ---------------------------
    sql = (
        f"INSERT INTO {schema}.archive_relevance_decisions "
        f"(researcher_id, canonical_id, relevance_type, reason, source, "
        f" confidence, scout_version, decided_at) "
        f"VALUES ('{rid}', %s, %s, %s, %s, %s, %s, now()) "
        f"ON CONFLICT (researcher_id, canonical_id) DO UPDATE SET "
        f"  relevance_type = EXCLUDED.relevance_type, "
        f"  reason         = EXCLUDED.reason, "
        f"  source         = EXCLUDED.source, "
        f"  confidence     = EXCLUDED.confidence, "
        f"  scout_version  = EXCLUDED.scout_version, "
        f"  decided_at     = now()"
    )
    rows = [(c, rt, rs, sr, cf, sv) for (c, rt, rs, sr, cf, sv) in norm]
    _db.exec_many(sql, rows)

    # ---- which canonical_ids already exist in archive_papers (NOT new) -----
    cids = list(meta_by_cid.keys())
    in_archive: set[str] = set()
    if cids:
        in_list = ",".join(f"'{c}'" for c in cids)  # cids are 32-hex, safe
        try:
            got = _db.query_json(
                f"SELECT canonical_id FROM {schema}.archive_papers "
                f"WHERE canonical_id IN ({in_list})")
            in_archive = {r["canonical_id"] for r in got}
        except Exception:  # noqa: BLE001
            in_archive = set()  # if archive_papers absent, treat all as new

    # ---- append genuinely-new ACCEPTED papers to found/<rid>.jsonl ---------
    _FOUND_DIR.mkdir(parents=True, exist_ok=True)
    found_path = _FOUND_DIR / f"{rid}.jsonl"
    already_logged: set[str] = set()
    if found_path.exists():
        for line in found_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                already_logged.add(json.loads(line)["canonical_id"])
            except Exception:  # noqa: BLE001
                continue
    new_appended = 0
    with found_path.open("a", encoding="utf-8") as fh:
        for cid, meta in meta_by_cid.items():
            if meta["relevance_type"] == "none":
                continue
            if cid in in_archive or cid in already_logged:
                continue
            fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
            already_logged.add(cid)
            new_appended += 1

    by_type: dict[str, int] = {}
    for (_c, rt, *_rest) in norm:
        by_type[rt] = by_type.get(rt, 0) + 1
    print(json.dumps({
        "researcher": rid,
        "upserted": len(norm),
        "by_type": by_type,
        "new_appended": new_appended,
        "in_archive": len(in_archive),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
