#!/usr/bin/env python3
"""Backfill missing abstracts in state/archive/discovery_run/found/<INIT>.jsonl
deterministically by DOI — OpenAlex abstract_inverted_index (primary) + Europe PMC
REST abstractText (fallback). Lightweight HTTP GET (no PDF crawl), so it is robust
where crawl.mjs fulltext times out / returns unavailable for very recent articles.

Usage: python3 scripts/archive/backfill_abstracts.py <INIT>
Rewrites found/<INIT>.jsonl in place (abstracts filled where obtainable). Read-only
w.r.t. the DB and the network sources. Prints a summary.
"""
from __future__ import annotations
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
UA = "csnl-paper-rec/1.0 (mailto:lab@example.org)"


def _get(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _openalex_abstract(doi: str) -> str:
    try:
        d = _get(f"https://api.openalex.org/works/https://doi.org/{urllib.parse.quote(doi)}?mailto=lab@example.org")
    except Exception:
        return ""
    inv = d.get("abstract_inverted_index")
    if not inv:
        return ""
    pos = []
    for word, idxs in inv.items():
        for i in idxs:
            pos.append((i, word))
    pos.sort()
    return " ".join(w for _, w in pos).strip()


def _epmc_abstract(doi: str) -> str:
    try:
        q = urllib.parse.quote(f"DOI:{doi}")
        d = _get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={q}&resultType=core&format=json")
    except Exception:
        return ""
    res = (d.get("resultList") or {}).get("result") or []
    for r in res:
        ab = (r.get("abstractText") or "").strip()
        if ab:
            return ab
    return ""


def main():
    if len(sys.argv) < 2:
        print("usage: backfill_abstracts.py <INIT>"); sys.exit(2)
    init = sys.argv[1].strip().upper()
    fp = ROOT / "state/archive/discovery_run/found" / f"{init}.jsonl"
    if not fp.exists():
        print(f"no found file: {fp}"); sys.exit(2)
    rows = [json.loads(l) for l in fp.read_text(encoding="utf-8").splitlines() if l.strip()]
    filled = oa = ep = nodoi = already = 0
    for r in rows:
        if (r.get("abstract") or "").strip():
            already += 1
            continue
        doi = (r.get("doi") or "").strip().replace("https://doi.org/", "")
        if not doi:
            nodoi += 1
            continue
        ab = _openalex_abstract(doi)
        src = "oa"
        if not ab:
            ab = _epmc_abstract(doi)
            src = "ep"
        if ab:
            r["abstract"] = ab
            filled += 1
            oa += (src == "oa"); ep += (src == "ep")
        time.sleep(0.4)  # politeness
    fp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(json.dumps({"init": init, "rows": len(rows), "already_had": already,
                      "filled": filled, "via_openalex": oa, "via_epmc": ep,
                      "no_doi": nodoi,
                      "still_missing": sum(1 for r in rows if not (r.get("abstract") or "").strip())}))


if __name__ == "__main__":
    main()
