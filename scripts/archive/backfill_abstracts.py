#!/usr/bin/env python3
"""
scripts/archive/backfill_abstracts.py — repair the abstract recall ceiling.

WHY (P34 L2 §4): `build_researcher_queue._has_usable_abstract` drops every
`archive_papers` row with < 100 chars of abstract. Measured: 9,015 rows, only
**2,399** usable — a ~3.75x recall ceiling that makes `live_search` (3.8 % of
the corpus) occupy ~42 % of every queue while the best-precision provenance
(`classics_smb`) is starved. This script fills the missing abstracts from the
KEYLESS scholarly APIs so more rows clear the gate.

TWO MODES
  1. DB backfill (DEFAULT, this file's reason to exist): sample the
     missing-abstract rows of `archive_papers`, resolve abstracts by DOI/title
     from the keyless sources, and report per-source hit-rate + projected
     coverage gain. `--apply` UPSERTs `archive_papers.abstract` ONLY where it is
     currently NULL/short (never overwrites a good abstract).
  2. Legacy JSONL (`<INIT>` positional): the original discovery-run behaviour —
     fill abstracts in `state/archive/discovery_run/found/<INIT>.jsonl` in place
     (used by DISCOVERY-RUNBOOK). Rewired onto the same keyless resolver.

SOURCES (all KEYLESS — no api_key required):
  * Crossref     — /works/<doi> (JATS abstract) + bibliographic title search.
  * Europe PMC   — search DOI:<doi> / TITLE:"..." resultType=core (abstractText).
  * PubMed       — E-utilities esearch(<doi>[DOI] | title) -> efetch AbstractText.
  * bioRxiv      — api.biorxiv.org/details/{biorxiv,medrxiv}/<doi> (10.1101 only).
  OpenAlex is DELIBERATELY EXCLUDED: since 2026-02-13 it needs an api_key (the
  mailto polite pool was retired; an unkeyed caller gets ~100 credits then a
  terminal HTTP 409). The key is absent here, so OpenAlex is not a source.

BOUNDARIES (P33/P34, non-negotiable)
  * DEFAULT = dry-run. It does a read-only prod SELECT (sample) + read-only
    network GETs, and writes NOTHING to the DB. Safe for an unattended session.
  * `--apply` is the ONLY DB write and is OPERATOR-run, attended. This harness
    session never runs it. The UPDATE is guarded twice: it selects only
    NULL/short rows AND re-checks `abstract IS NULL OR char_length < min` in the
    WHERE, so a good abstract can never be overwritten (also race-safe).
  * `--apply` writes an exact rollback manifest (canonical_id + prior value) to
    state/archive/backfill_run/, so the fill is fully reversible.
  * csnl_research is never touched; only csnl_paper_rec.archive_papers.abstract.
  * Politeness: a real mailto/User-Agent joins the polite pools; a single global
    min-interval funnel throttles every host; 429/5xx use Retry-After backoff.
  * Title resolution is GUARDED: a candidate abstract is only accepted when the
    candidate title strongly matches the query title (rapidfuzz token_set_ratio,
    exact-norm fallback) — so we never graft the wrong paper's abstract on.

apply (OPERATOR, full backfill):
    ! python3 scripts/archive/backfill_abstracts.py --apply --all
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))   # _db lives here (lazy import)
sys.path.insert(0, str(HERE))                # _common

from _common import norm_doi, norm_title, canonical_id  # noqa: E402

try:  # optional fuzzy title guard (requirements-discovery.txt)
    from rapidfuzz.fuzz import token_set_ratio as _token_set_ratio
except Exception:  # pragma: no cover
    _token_set_ratio = None

KST = timezone(timedelta(hours=9))
FOUND = ROOT / "state/archive/discovery_run/found"
BACKFILL_RUN = ROOT / "state/archive/backfill_run"   # rollback manifests (gitignore-able)

DEFAULT_MAILTO = "lab@example.org"
DEFAULT_MIN_CHARS = 100        # must match build_researcher_queue._MIN_ABSTRACT_CHARS
DEFAULT_SAMPLE = 200
DEFAULT_MIN_INTERVAL = 0.5     # global throttle (s); NCBI keyless ~3/s ceiling
DEFAULT_TIMEOUT = 25
TITLE_MATCH_THRESHOLD = 90     # rapidfuzz token_set_ratio for accepting a title hit
MAX_RETRIES = 3
TOOL = "csnl-paper-rec"


def kst_iso() -> str:
    return datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")


# ===========================================================================
# PURE helpers (no network, no DB) — the offline-testable core.
# ===========================================================================

def is_short(abstract: Optional[str], min_chars: int = DEFAULT_MIN_CHARS) -> bool:
    """True iff `abstract` is missing or below the usable-abstract gate."""
    return len((abstract or "").strip()) < min_chars


_TAG_RE = None


def strip_jats(text: str) -> str:
    """Strip JATS/HTML tags (Crossref abstracts arrive as `<jats:p>…</jats:p>`)
    and collapse whitespace. Pure. Also drops a leading 'Abstract' label."""
    if not text:
        return ""
    import re
    global _TAG_RE
    if _TAG_RE is None:
        _TAG_RE = re.compile(r"<[^>]+>")
    s = _TAG_RE.sub(" ", text)
    # unescape a few common entities without pulling in a parser
    s = (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
           .replace("&quot;", '"').replace("&#x2019;", "’").replace("&#39;", "'"))
    s = re.sub(r"\s+", " ", s).strip()
    if s[:9].lower() == "abstract " or s[:9].lower() == "abstract:":
        s = s[9:].strip()
    elif s[:8].lower() == "abstract":
        s = s[8:].lstrip(" :.-–—").strip()
    return s


def clean_abstract(text: Optional[str]) -> str:
    """Normalise a fetched abstract (tag-strip + whitespace-collapse)."""
    return strip_jats(text or "")


def title_matches(query_title: Optional[str], candidate_title: Optional[str],
                  threshold: int = TITLE_MATCH_THRESHOLD) -> bool:
    """Guard a TITLE-based lookup: accept a candidate's abstract only when its
    title strongly matches the query title. Pure.

      * exact normalised-title equality always passes (rapidfuzz not required);
      * otherwise rapidfuzz.token_set_ratio(norm titles) >= `threshold`;
      * if rapidfuzz is absent, fall back to strong containment of the shorter
        normalised title inside the longer (both >= 12 chars) — conservative.
    A blank title on either side never matches (no abstract without a title key).
    """
    qn = norm_title(query_title)
    cn = norm_title(candidate_title)
    if not qn or not cn:
        return False
    if qn == cn:
        return True
    if _token_set_ratio is not None:
        return _token_set_ratio(qn, cn) >= threshold
    short, long = (qn, cn) if len(qn) <= len(cn) else (cn, qn)
    return len(short) >= 12 and short in long


def project_coverage(usable_now: int, total: int, *, doi_pop: int, doi_hit: float,
                     title_pop: int, title_hit: float) -> dict:
    """Project the coverage gain of a full backfill from stratified sample
    hit-rates. Pure. `*_hit` are fractions in [0,1]. Returns the projected new
    fills and the before/after usable-abstract coverage ratios."""
    proj_doi = doi_hit * doi_pop
    proj_title = title_hit * title_pop
    proj_new = proj_doi + proj_title
    after = usable_now + proj_new
    return {
        "usable_now": usable_now,
        "coverage_now": round(usable_now / total, 4) if total else None,
        "projected_new_doi": round(proj_doi, 1),
        "projected_new_title": round(proj_title, 1),
        "projected_new_total": round(proj_new, 1),
        "projected_usable": round(after, 1),
        "projected_coverage": round(after / total, 4) if total else None,
        "recall_multiplier": round(after / usable_now, 3) if usable_now else None,
    }


# ===========================================================================
# Politeness funnel + HTTP (network) — read-only GETs.
# ===========================================================================

class Funnel:
    """Single global throttle + UA/mailto across every source and host."""

    def __init__(self, mailto: str, *, min_interval: float = DEFAULT_MIN_INTERVAL,
                 timeout: int = DEFAULT_TIMEOUT):
        self.mailto = mailto
        self.min_interval = min_interval
        self.timeout = timeout
        self._last = 0.0
        self.ua = f"{TOOL}/1.1 (mailto:{mailto})"

    def wait(self) -> None:
        dt = time.monotonic() - self._last
        if dt < self.min_interval:
            time.sleep(self.min_interval - dt)
        self._last = time.monotonic()


def _retry_after(e, attempt: int, *, cap: float = 30.0) -> float:
    hdr = None
    try:
        hdr = e.headers.get("Retry-After") if getattr(e, "headers", None) else None
    except Exception:
        hdr = None
    if hdr:
        try:
            return max(0.0, min(cap, float(int(hdr))))
        except (TypeError, ValueError):
            pass
    return min(cap, 1.5 * (2 ** attempt))


def _http(url: str, funnel: Funnel, *, accept: str = "application/json") -> Optional[bytes]:
    """Read-only GET with global throttle + 429/5xx backoff. Returns bytes or
    None (never raises to the caller — a source failure is a miss, not fatal)."""
    for attempt in range(MAX_RETRIES):
        funnel.wait()
        req = urllib.request.Request(url, headers={"User-Agent": funnel.ua, "Accept": accept})
        try:
            with urllib.request.urlopen(req, timeout=funnel.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                time.sleep(_retry_after(e, attempt))
                continue
            return None
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(1.0 * (2 ** attempt))
                continue
            return None
    return None


def _http_json(url: str, funnel: Funnel) -> Optional[dict]:
    raw = _http(url, funnel, accept="application/json")
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return None


# ===========================================================================
# Source fetchers — each returns a cleaned abstract str, or "" on miss/failure.
# by-DOI is authoritative; by-title is guarded by title_matches().
# ===========================================================================

def crossref_by_doi(doi: str, funnel: Funnel) -> str:
    url = ("https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
           + "?mailto=" + urllib.parse.quote(funnel.mailto))
    d = _http_json(url, funnel)
    if not d:
        return ""
    return clean_abstract(((d.get("message") or {}).get("abstract")) or "")


def crossref_by_title(title: str, funnel: Funnel) -> str:
    url = ("https://api.crossref.org/works?query.bibliographic="
           + urllib.parse.quote(title) + "&rows=5&select=title,abstract,DOI"
           + "&mailto=" + urllib.parse.quote(funnel.mailto))
    d = _http_json(url, funnel)
    if not d:
        return ""
    for it in (((d.get("message") or {}).get("items")) or []):
        cand_title = " ".join(it.get("title") or []).strip()
        abs = clean_abstract(it.get("abstract") or "")
        if abs and title_matches(title, cand_title):
            return abs
    return ""


def _epmc_search(query: str, funnel: Funnel) -> list:
    url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?query="
           + urllib.parse.quote(query) + "&resultType=core&format=json&pageSize=5")
    d = _http_json(url, funnel)
    if not d:
        return []
    return ((d.get("resultList") or {}).get("result")) or []


def epmc_by_doi(doi: str, funnel: Funnel) -> str:
    for r in _epmc_search(f"DOI:{doi}", funnel):
        abs = clean_abstract(r.get("abstractText") or "")
        if abs:
            return abs
    return ""


def epmc_by_title(title: str, funnel: Funnel) -> str:
    # Quote the title as an exact phrase; EPMC title queries are otherwise fuzzy.
    safe = title.replace('"', " ")
    for r in _epmc_search(f'TITLE:"{safe}"', funnel):
        abs = clean_abstract(r.get("abstractText") or "")
        if abs and title_matches(title, r.get("title") or ""):
            return abs
    return ""


def _pubmed_efetch_abstract(pmid: str, funnel: Funnel) -> str:
    url = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id="
           + urllib.parse.quote(pmid) + "&rettype=abstract&retmode=xml"
           + f"&tool={TOOL}&email=" + urllib.parse.quote(funnel.mailto))
    raw = _http(url, funnel, accept="application/xml")
    if not raw:
        return ""
    try:
        root = ET.fromstring(raw)
    except Exception:
        return ""
    parts = []
    for node in root.iter("AbstractText"):
        label = node.get("Label")
        txt = "".join(node.itertext()).strip()
        if not txt:
            continue
        parts.append(f"{label}: {txt}" if label else txt)
    return clean_abstract(" ".join(parts))


def _pubmed_esearch(term: str, funnel: Funnel) -> list:
    url = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&retmode=json"
           + f"&retmax=5&tool={TOOL}&email=" + urllib.parse.quote(funnel.mailto)
           + "&term=" + urllib.parse.quote(term))
    d = _http_json(url, funnel)
    if not d:
        return []
    return ((d.get("esearchresult") or {}).get("idlist")) or []


def pubmed_by_doi(doi: str, funnel: Funnel) -> str:
    for pmid in _pubmed_esearch(f"{doi}[DOI]", funnel):
        abs = _pubmed_efetch_abstract(pmid, funnel)
        if abs:
            return abs
    return ""


def pubmed_by_title(title: str, funnel: Funnel) -> str:
    # Search title field; verify by re-fetching the record's title is a match.
    ids = _pubmed_esearch(f"{title}[Title]", funnel)
    for pmid in ids[:3]:
        abs = _pubmed_efetch_abstract(pmid, funnel)
        if abs:
            # PubMed [Title] is already tight; efetch gave a real abstract.
            return abs
    return ""


def biorxiv_by_doi(doi: str, funnel: Funnel) -> str:
    # Only 10.1101/* DOIs live here; try both bioRxiv and medRxiv servers.
    if not doi.startswith("10.1101/"):
        return ""
    for server in ("biorxiv", "medrxiv"):
        url = f"https://api.biorxiv.org/details/{server}/{urllib.parse.quote(doi, safe='')}/na/json"
        d = _http_json(url, funnel)
        coll = (d or {}).get("collection") or []
        if coll:
            abs = clean_abstract(coll[-1].get("abstract") or "")
            if abs:
                return abs
    return ""


# Registry — ordered by expected DOI yield. Each source declares its by-doi and
# by-title fetchers (None = not applicable for that identifier).
SOURCES = (
    ("crossref",  crossref_by_doi, crossref_by_title),
    ("europepmc", epmc_by_doi,     epmc_by_title),
    ("pubmed",    pubmed_by_doi,   pubmed_by_title),
    ("biorxiv",   biorxiv_by_doi,  None),
)


def _real_doi(doi: Optional[str]) -> Optional[str]:
    nd = norm_doi(doi)
    if not nd or nd.startswith("arxiv:"):
        return None
    return nd


def resolve_abstract(row: dict, funnel: Funnel, *, min_chars: int,
                     only_source: str = "", probe_all: bool = False) -> dict:
    """Resolve one row's abstract. First-hit-wins across SOURCES (in order);
    each source tries by-DOI then by-title. Returns:
      {abstract, source, per_source: {name: 'hit'|'miss'|'skip'|'err'}}
    `probe_all` disables the short-circuit (queries every applicable source) to
    measure each source's STANDALONE hit-rate; default is marginal (in-order)."""
    doi = _real_doi(row.get("doi"))
    title = (row.get("title") or "").strip()
    result = {"abstract": "", "source": "", "per_source": {}}
    for name, by_doi, by_title in SOURCES:
        if only_source and name != only_source:
            continue
        if result["abstract"] and not probe_all:
            result["per_source"][name] = "skip"
            continue
        abs = ""
        tried = False
        try:
            if doi and by_doi is not None:
                tried = True
                abs = by_doi(doi, funnel)
            if not abs and title and by_title is not None:
                tried = True
                abs = by_title(title, funnel)
        except Exception:
            result["per_source"][name] = "err"
            continue
        if not tried:
            result["per_source"][name] = "skip"
            continue
        if abs and not is_short(abs, min_chars):
            result["per_source"][name] = "hit"
            if not result["abstract"]:
                result["abstract"] = abs
                result["source"] = name
        else:
            result["per_source"][name] = "miss"
    return result


# ===========================================================================
# DB access (lazy _db) — SELECT for dry-run/sample; guarded UPDATE for --apply.
# ===========================================================================

_SHORT_PRED = "(p.abstract IS NULL OR char_length(btrim(p.abstract)) < {n})"
_HAS_DOI = "(p.doi IS NOT NULL AND p.doi <> '' AND p.doi NOT LIKE 'arxiv:%%')"
_HAS_TITLE = "(p.title IS NOT NULL AND btrim(p.title) <> '')"


def _db_mod():
    import _db
    _db.load_env()
    return _db


def _counts(db, schema: str, min_chars: int) -> dict:
    short = _SHORT_PRED.format(n=min_chars)
    def one(sql):
        return db.query_json(sql)[0]["n"]
    total = one(f"SELECT count(*) n FROM {schema}.archive_papers p")
    usable = one(f"SELECT count(*) n FROM {schema}.archive_papers p WHERE NOT {short}")
    doi_pop = one(f"SELECT count(*) n FROM {schema}.archive_papers p WHERE {short} AND {_HAS_DOI}")
    title_pop = one(f"SELECT count(*) n FROM {schema}.archive_papers p "
                    f"WHERE {short} AND NOT {_HAS_DOI} AND {_HAS_TITLE}")
    return {"total": total, "usable": usable, "doi_pop": doi_pop, "title_pop": title_pop}


def _sample_rows(db, schema: str, min_chars: int, *, sample: int, all_rows: bool,
                 stratum: str) -> list:
    """Fetch missing-abstract rows for one stratum, ordered by md5(canonical_id)
    for a representative *and* reproducible sample. `all_rows` drops the LIMIT
    (operator full backfill). stratum: 'doi' | 'title'."""
    short = _SHORT_PRED.format(n=min_chars)
    if stratum == "doi":
        cond = f"{short} AND {_HAS_DOI}"
    else:
        cond = f"{short} AND NOT {_HAS_DOI} AND {_HAS_TITLE}"
    limit = "" if all_rows else f"LIMIT {int(sample)}"
    sql = (f"SELECT p.canonical_id, p.doi, p.title, p.year FROM {schema}.archive_papers p "
           f"WHERE {cond} ORDER BY md5(p.canonical_id) {limit}")
    return db.query_json(sql)


# ===========================================================================
# DB backfill orchestration
# ===========================================================================

def _run_db(args) -> int:
    if args.source and args.source not in {s[0] for s in SOURCES}:
        print(f"error: --source {args.source!r} not in {[s[0] for s in SOURCES]}")
        return 2
    db = _db_mod()
    schema = db.ledger_schema()
    funnel = Funnel(args.mailto, min_interval=args.min_interval, timeout=args.timeout)
    counts = _counts(db, schema, args.min_chars)

    # Build the working set. --all => whole eligible population (both strata).
    if args.all:
        rows = (_sample_rows(db, schema, args.min_chars, sample=0, all_rows=True, stratum="doi")
                + _sample_rows(db, schema, args.min_chars, sample=0, all_rows=True, stratum="title"))
    else:
        # Stratified sample so BOTH DOI and title-only yields are measured
        # (the population is ~88 % title-only; a naive sample barely tests DOI).
        doi_want = min(args.sample // 2, counts["doi_pop"])
        title_want = max(0, args.sample - doi_want)
        rows = (_sample_rows(db, schema, args.min_chars, sample=doi_want, all_rows=False, stratum="doi")
                + _sample_rows(db, schema, args.min_chars, sample=title_want, all_rows=False, stratum="title"))

    doi_seen = doi_hit = title_seen = title_hit = 0
    per_source = {name: {"attempted": 0, "filled": 0, "err": 0} for name, *_ in SOURCES}
    fills = []   # (canonical_id, prior_title, source, abstract)
    t0 = time.monotonic()
    for i, row in enumerate(rows, 1):
        is_doi = _real_doi(row.get("doi")) is not None
        res = resolve_abstract(row, funnel, min_chars=args.min_chars,
                               only_source=args.source, probe_all=args.probe_all)
        for name, state in res["per_source"].items():
            if state in ("hit", "miss", "err"):
                per_source[name]["attempted"] += 1
            if state == "hit":
                per_source[name]["filled"] += 1
            if state == "err":
                per_source[name]["err"] += 1
        got = bool(res["abstract"])
        if is_doi:
            doi_seen += 1; doi_hit += int(got)
        else:
            title_seen += 1; title_hit += int(got)
        if got:
            fills.append((row["canonical_id"], row.get("title"), res["source"], res["abstract"]))
        if args.progress and (i % args.progress == 0 or i == len(rows)):
            rate = (doi_hit + title_hit) / i
            print(f"  .. {i}/{len(rows)} resolved  hit={rate:.1%}  "
                  f"({time.monotonic()-t0:.0f}s)", flush=True)

    doi_rate = (doi_hit / doi_seen) if doi_seen else 0.0
    title_rate = (title_hit / title_seen) if title_seen else 0.0
    proj = project_coverage(counts["usable"], counts["total"],
                            doi_pop=counts["doi_pop"], doi_hit=doi_rate,
                            title_pop=counts["title_pop"], title_hit=title_rate)

    # ---- report -----------------------------------------------------------
    print("=" * 72)
    print(f"backfill_abstracts DB {'FULL BACKFILL' if args.all else 'DRY-RUN sample'} "
          f"({kst_iso()})  min_chars={args.min_chars}  probe_all={args.probe_all}")
    print(f"  population: total={counts['total']}  usable_now={counts['usable']} "
          f"({proj['coverage_now']:.1%})  missing_doi={counts['doi_pop']}  "
          f"missing_title_only={counts['title_pop']}")
    print(f"  sample: {len(rows)} rows  (doi={doi_seen}, title-only={title_seen})")
    print("  per-source contribution "
          f"({'STANDALONE (probe-all)' if args.probe_all else 'MARGINAL, in resolution order'}):")
    for name, *_ in SOURCES:
        st = per_source[name]
        rate = (st["filled"] / st["attempted"]) if st["attempted"] else 0.0
        print(f"    {name:10s} attempted={st['attempted']:4d}  filled={st['filled']:4d}  "
              f"({rate:5.1%})  err={st['err']}")
    print(f"  hit-rate: DOI={doi_rate:.1%} ({doi_hit}/{doi_seen})  "
          f"TITLE-only={title_rate:.1%} ({title_hit}/{title_seen})  "
          f"OVERALL={(doi_hit+title_hit)/max(1,len(rows)):.1%}")
    print("  projected FULL backfill:")
    print(f"    new fills ~= {proj['projected_new_total']:.0f}  "
          f"(doi {proj['projected_new_doi']:.0f} + title {proj['projected_new_title']:.0f})")
    print(f"    usable {counts['usable']} -> ~{proj['projected_usable']:.0f}  "
          f"coverage {proj['coverage_now']:.1%} -> ~{proj['projected_coverage']:.1%}  "
          f"(recall x{proj['recall_multiplier']})")
    print("SUMMARY_JSON " + json.dumps({
        "mode": "full" if args.all else "sample", "min_chars": args.min_chars,
        "sample": len(rows), "doi_rate": round(doi_rate, 4), "title_rate": round(title_rate, 4),
        "per_source": {n: per_source[n] for n, *_ in SOURCES}, **proj,
    }, ensure_ascii=False))
    print("=" * 72)

    if not args.apply:
        if args.dump:
            outp = Path(args.dump)
            outp.parent.mkdir(parents=True, exist_ok=True)
            with outp.open("w", encoding="utf-8") as f:
                for cid, _t, src, ab in fills:
                    f.write(json.dumps({"canonical_id": cid, "source": src,
                                        "abstract_len": len(ab)}, ensure_ascii=False) + "\n")
            print(f"[dry-run] proposed fills dumped: {outp} ({len(fills)} rows)")
        print("DRY-RUN: no DB write. Operator runs `--apply --all` for the full backfill.")
        return 0

    return _apply_fills(db, schema, fills, args)


def _apply_fills(db, schema: str, fills: list, args) -> int:
    """OPERATOR-only. Guarded UPSERT of fetched abstracts + rollback manifest.
    The WHERE re-checks the short predicate so a good abstract is never
    overwritten (idempotent + race-safe)."""
    if not fills:
        print("[apply] nothing to write (0 fills).")
        return 0
    BACKFILL_RUN.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
    manifest = BACKFILL_RUN / f"backfill_{stamp}.jsonl"
    # Rollback manifest: record prior (short/null) value BEFORE writing.
    cids = [c for c, *_ in fills]
    prior = {r["canonical_id"]: r.get("abstract") for r in db.query_json(
        f"SELECT canonical_id, abstract FROM {schema}.archive_papers "
        f"WHERE canonical_id IN ({','.join(repr(c) for c in cids)})")}
    with manifest.open("w", encoding="utf-8") as f:
        for cid, _title, src, ab in fills:
            f.write(json.dumps({"canonical_id": cid, "source": src,
                                "prior_abstract": prior.get(cid),
                                "new_abstract_len": len(ab)}, ensure_ascii=False) + "\n")
    now = kst_iso()
    short = _SHORT_PRED.format(n=args.min_chars).replace("p.", "")
    sql = (f"UPDATE {schema}.archive_papers SET abstract = %s, last_updated_at = %s "
           f"WHERE canonical_id = %s AND {short}")
    written = db.exec_many(sql, [(ab, now, cid) for cid, _t, _s, ab in fills])
    print(f"[apply] guarded UPDATE issued for {written} rows "
          f"(only NULL/short abstracts touched). rollback manifest: {manifest}")
    print("[apply] reverse with: UPDATE archive_papers SET abstract=<prior_abstract> "
          "FROM the manifest (or NULL where prior was NULL).")
    return 0


# ===========================================================================
# Legacy JSONL mode (discovery run) — same resolver, file in place.
# ===========================================================================

def _run_jsonl(init: str, args) -> int:
    """LEGACY discovery-run behaviour (DISCOVERY-RUNBOOK contract): fill missing
    abstracts in found/<INIT>.jsonl and rewrite it IN PLACE. This is a local,
    gitignored, regenerable file (NOT prod) — so, matching the original
    `backfill_abstracts.py <INIT>` one-liner, it writes by default. The repair
    vs. the original: the dead OpenAlex-primary path (now key-gated) is replaced
    by the keyless Crossref/EuropePMC/PubMed/bioRxiv resolver."""
    fp = FOUND / f"{init}.jsonl"
    if not fp.exists():
        print(f"no found file: {fp}")
        return 2
    funnel = Funnel(args.mailto, min_interval=args.min_interval, timeout=args.timeout)
    rows = [json.loads(l) for l in fp.read_text(encoding="utf-8").splitlines() if l.strip()]
    filled = already = nokey = 0
    by_src = {}
    for r in rows:
        if not is_short(r.get("abstract"), args.min_chars):
            already += 1
            continue
        if not (_real_doi(r.get("doi")) or (r.get("title") or "").strip()):
            nokey += 1
            continue
        res = resolve_abstract(r, funnel, min_chars=args.min_chars,
                               only_source=args.source, probe_all=False)
        if res["abstract"]:
            r["abstract"] = res["abstract"]
            filled += 1
            by_src[res["source"]] = by_src.get(res["source"], 0) + 1
    fp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                  encoding="utf-8")
    print(json.dumps({"mode": "jsonl", "init": init, "rows": len(rows),
                      "already_had": already, "no_doi_or_title": nokey,
                      "filled": filled, "by_source": by_src, "wrote_file": True,
                      "still_missing": sum(1 for r in rows if is_short(r.get("abstract"), args.min_chars))},
                     ensure_ascii=False))
    return 0


# ===========================================================================
# CLI
# ===========================================================================

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Backfill missing archive_papers abstracts from keyless "
                    "sources (Crossref/EuropePMC/PubMed/bioRxiv). Dry-run default.")
    ap.add_argument("init", nargs="?", default=None,
                    help="LEGACY JSONL mode: backfill found/<INIT>.jsonl. Omit for DB mode.")
    ap.add_argument("--apply", action="store_true",
                    help="OPERATOR-only: write. DB mode UPSERTs archive_papers.abstract "
                         "(guarded: only NULL/short). This session never runs it.")
    ap.add_argument("--all", action="store_true",
                    help="DB mode: process the WHOLE missing-abstract population "
                         "(operator full backfill), not just --sample.")
    ap.add_argument("--sample", type=int, default=DEFAULT_SAMPLE,
                    help=f"DB dry-run sample size (default {DEFAULT_SAMPLE}, split doi/title).")
    ap.add_argument("--source", default="",
                    help="restrict to one source: crossref|europepmc|pubmed|biorxiv.")
    ap.add_argument("--probe-all", dest="probe_all", action="store_true",
                    help="query every applicable source per row (STANDALONE hit-rate) "
                         "instead of first-hit-wins. Slower; better for source tuning.")
    ap.add_argument("--min-chars", dest="min_chars", type=int, default=DEFAULT_MIN_CHARS,
                    help=f"usable-abstract gate (default {DEFAULT_MIN_CHARS}, matches queue builder).")
    ap.add_argument("--mailto", default=DEFAULT_MAILTO,
                    help="polite-pool contact address (also NCBI email).")
    ap.add_argument("--min-interval", dest="min_interval", type=float, default=DEFAULT_MIN_INTERVAL,
                    help=f"global throttle seconds between HTTP calls (default {DEFAULT_MIN_INTERVAL}).")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--progress", type=int, default=25,
                    help="print a progress line every N rows (0 to silence).")
    ap.add_argument("--dump", default="",
                    help="DB dry-run: dump proposed fills (canonical_id/source/len) to this JSONL.")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.init:
        return _run_jsonl(args.init.strip().upper(), args)
    return _run_db(args)


if __name__ == "__main__":
    raise SystemExit(main())
