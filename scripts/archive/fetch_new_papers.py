#!/usr/bin/env python3
"""
scripts/archive/fetch_new_papers.py — P33 Track B (B2). FETCH-ONLY discovery.

Fetch newly-indexed candidate papers from keyless scholarly APIs and emit them
in the EXACT `state/archive/discovery_run/found/<INIT>.jsonl` shape that
`ingest_live_papers.py` already consumes. NO parallel stack: the operator then
runs the UNCHANGED `ingest_live_papers.py` + `wire_live_to_queue_inputs.py` +
`record_relevance.py` (which apply the reasoning gate and write prod).

WHAT THIS FILE DOES (fetch only):
  1. ADAPTER layer over `paper_search_mcp.academic_platforms.*` clients — never
     call the raw clients: bounded socket timeout, per-source try/except
     isolation (one source failing is logged+skipped, never fatal), a single
     politeness/throttle funnel (real mailto/User-Agent for the polite pools;
     browser UA for DOAJ's Cloudflare), pub_date captured at fetch time, and
     bioRxiv/medRxiv run as a FIREHOSE-then-local-filter (a capped date window
     matched locally — the profile is NEVER passed as an API category).
  2. TRI-STATE fetch — every source returns `ok+papers` | `ok+empty` |
     `FAILED (raises)`; it NEVER returns `[]` to signal an error. The watermark
     advances via GREATEST(stored, batch-max) ONLY on a non-exception run.
  3. WATERMARK — `archive_discovery_watermark` cursor on ingest/index date with
     a lookback-overlap window so late-indexed papers are still fetched. The DB
     read/advance lives behind `--apply` (commit-then-advance). The pure advance
     math (`compute_watermark_advance`) is offline-unit-tested; only the thin
     DB wrapper touches Postgres, and only the OPERATOR runs `--apply`.
  4. S2 RECOMMENDATIONS — map `archive_papers.doi -> 'DOI:<doi>'` and synthetic
     `'arxiv:<id>' -> 'ARXIV:<id>'`, seed the S2 recommendations endpoint with a
     researcher's pos(save/read)/neg(not_relevant) lists, 429/Retry-After
     backoff, post-filter by pub_date. EGRESS RULE: the request body carries
     ONLY public paper IDs — never researcher_id / name / email / any .env value.

BOUNDARIES (P33, non-negotiable):
  * DEFAULT = dry-run (prints the plan; NO network, NO DB).
  * `--apply` writes ONLY `found/*.jsonl` files + (optionally) advances the
    watermark row. It writes NO other prod table and sends nothing. It is
    OPERATOR-run, attended — this harness session never runs it.
  * The pos/neg DOI lists and per-researcher query terms are read from an
    operator-produced inputs file (or a fixture), NOT by connecting to prod
    from here — so the fetch stays prod-DB-free on the read side too.
  * `found/*.jsonl` is gitignored + regenerable (reversible: delete the files;
    prod side reverses via `DELETE FROM archive_papers WHERE source='live_search'`).

REVERSIBLE: new file; found/*.jsonl regenerable; watermark row operator-dropped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

# _common is pure (no DB connection at import). We reuse the FIXED P33 contracts:
#   norm_doi / is_arxiv_synthetic / same_work / dedup_same_work / canonical_id.
from _common import (  # noqa: E402
    canonical_id,
    dedup_same_work,
    is_arxiv_synthetic,  # noqa: F401  (re-exported for callers/tests)
    kst_iso,
    norm_doi,
    norm_title,
)

FOUND = ROOT / "state/archive/discovery_run/found"
INPUTS = ROOT / "state/archive/discovery_run/inputs"

SCOUT_VERSION = "p33-fetch-2026-07-20"

# Politeness — a real mailto joins the polite pools (Crossref/OpenAlex/NCBI/
# EuropePMC). This is a standard User-Agent courtesy, NOT researcher identity;
# it never rides in the S2 recommendations *body* (see the egress rule).
DEFAULT_MAILTO = "lab@example.org"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

DEFAULT_LOOKBACK_DAYS = 30      # cold-start window when no watermark exists
DEFAULT_OVERLAP_DAYS = 7        # re-scan overlap so late-indexed rows aren't lost
DEFAULT_FIREHOSE_DAYS = 30      # bioRxiv/medRxiv capped firehose window
DEFAULT_MAX_RESULTS = 50        # per-source per-run cap
S2_REC_URL = "https://api.semanticscholar.org/recommendations/v1/papers/"
S2_MAX_RETRIES = 4
S2_MAX_POS = 100
S2_MAX_NEG = 100

PREPRINT_SOURCES = {"arxiv", "biorxiv", "medrxiv", "chemrxiv", "psyarxiv", "ssrn"}
PREPRINT_VENUE_HINTS = (
    "arxiv", "biorxiv", "medrxiv", "chemrxiv", "psyarxiv", "preprint",
    "research square", "ssrn", "osf",
)
_PRETTY_SOURCE = {
    "arxiv": "arXiv", "biorxiv": "bioRxiv", "medrxiv": "medRxiv",
    "chemrxiv": "chemRxiv", "pubmed": "PubMed",
}

# The S2 recommendations body is allowed EXACTLY these keys and nothing else.
_S2_ALLOWED_BODY_KEYS = frozenset({"positivePaperIds", "negativePaperIds"})


# ---------------------------------------------------------------------------
# Source registry — adapter specs over paper_search_mcp clients.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SourceSpec:
    name: str          # source_api provenance tag
    module: str        # paper_search_mcp.academic_platforms.<module>
    cls: str           # *Searcher class name
    mode: str          # 'query' (topic search) | 'firehose' (date-window sweep)
    preprint: bool = False
    browser_ua: bool = False   # DOAJ sits behind Cloudflare -> browser UA


SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("crossref", "crossref", "CrossRefSearcher", "query"),
    SourceSpec("europepmc", "europepmc", "EuropePMCSearcher", "query"),
    SourceSpec("arxiv", "arxiv", "ArxivSearcher", "query", preprint=True),
    SourceSpec("pubmed", "pubmed", "PubMedSearcher", "query"),
    SourceSpec("doaj", "doaj", "DOAJSearcher", "query", browser_ua=True),
    SourceSpec("semantic", "semantic", "SemanticSearcher", "query"),
    SourceSpec("biorxiv", "biorxiv", "BioRxivSearcher", "firehose", preprint=True),
    SourceSpec("medrxiv", "medrxiv", "MedRxivSearcher", "firehose", preprint=True),
)


# ===========================================================================
# PURE functions — no network, no DB. These are the offline-unit-tested core.
# ===========================================================================

def compute_watermark_advance(
    stored: Optional[date], batch_dates, *, failed: bool
) -> Optional[date]:
    """Tri-state cursor advance.

    Returns the NEW watermark index-date given the previously `stored` cursor and
    the `batch_dates` observed this run:

      * FAILED run (exception / 429 / timeout): return `stored` UNCHANGED — a
        failure must never move the cursor forward (would silently skip a window).
      * ok+empty (no dates this run): return `stored` unchanged — nothing to
        advance past; conservative, so the same window is re-queried next run.
      * ok+papers: advance to GREATEST(stored, max(batch_dates)).

    Pure + deterministic; `batch_dates` may contain None (ignored).
    """
    if failed:
        return stored
    dates = [d for d in (batch_dates or []) if d is not None]
    if not dates:
        return stored
    bmax = max(dates)
    return bmax if stored is None else max(stored, bmax)


def compute_fetch_window_start(
    stored_index_date: Optional[date],
    *,
    lookback_days: int = DEFAULT_OVERLAP_DAYS,
    default_lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    today: Optional[date] = None,
) -> date:
    """From-date for the next fetch, with a lookback-overlap so a paper indexed
    *after* the last run (but within `lookback_days` of the stored cursor) is
    still re-fetched (dedup then drops the ones already ingested).

      * cold start (no cursor): today - default_lookback_days.
      * warm: stored_index_date - lookback_days (never later than today).
    """
    today = today or date.today()
    if stored_index_date is None:
        return today - timedelta(days=default_lookback_days)
    start = stored_index_date - timedelta(days=lookback_days)
    return min(start, today)


def to_s2_id(doi: Optional[str]) -> Optional[str]:
    """Map a DOI (or synthetic arxiv key) to a Semantic Scholar external ID.

      * real DOI          -> 'DOI:<normalised-doi>'
      * synthetic arxiv   -> 'ARXIV:<id>'
      * empty / None       -> None (caller skips: no-DOI papers are dropped)
    """
    nd = norm_doi(doi)
    if not nd:
        return None
    if nd.startswith("arxiv:"):
        return "ARXIV:" + nd[len("arxiv:"):]
    return "DOI:" + nd


def build_s2_recommendation_body(
    pos_dois, neg_dois=None, *, max_pos: int = S2_MAX_POS, max_neg: int = S2_MAX_NEG
) -> dict:
    """Build the Semantic Scholar recommendations POST body from DOI lists.

    Maps + de-dups + caps each list; drops no-DOI entries. EGRESS RULE: the
    returned dict carries ONLY `positivePaperIds` / `negativePaperIds` (public
    paper IDs). It NEVER contains researcher_id / name / email / mailto / any
    .env value — call `egress_is_clean()` on it before POSTing.
    """
    def _map(seq, cap):
        out, seen = [], set()
        for d in (seq or []):
            sid = to_s2_id(d)
            if not sid or sid in seen:
                continue
            seen.add(sid)
            out.append(sid)
            if len(out) >= cap:
                break
        return out

    body = {"positivePaperIds": _map(pos_dois, max_pos)}
    neg = _map(neg_dois, max_neg)
    if neg:
        body["negativePaperIds"] = neg
    return body


# Strict public-paper-ID allowlist for the S2 egress guardrail. ONLY these three
# shapes may leave the machine in a recommendations body. Anything else — a
# researcher initial ('BHL'), a name, an email, a mailto, an ntn_/env secret —
# fails closed. '@' is additionally rejected outright (defence in depth, since a
# DOI suffix is otherwise permissive).
_S2_ID_RE = re.compile(
    r"^(?:DOI:10\.\d+/\S+"                     # DOI:10.<registrant>/<suffix>
    r"|ARXIV:[A-Za-z0-9][A-Za-z0-9._/\-]*"     # ARXIV:<id>
    r"|CorpusId:[0-9]+"                        # CorpusId:<digits>
    r")$"
)
_S2_ID_MAXLEN = 256


def egress_is_clean(body: dict) -> bool:
    """True iff `body` is safe to POST to Semantic Scholar — STRICT ALLOWLIST.

    Requires: only the permitted ID-list keys; each present key maps to a *list*
    (never a bare str); every element a str <= 256 chars matching `DOI:10.x/...`,
    `ARXIV:<id>` or `CorpusId:<digits>`, containing no '@'.

    Fail-closed hardening (P33 guardrail fix). The previous version only rejected
    extra keys and '@'-shaped values, leaving two holes:
      (a) a bare researcher identifier such as 'BHL' PASSED (it is a str with
          no '@'), so an identity string could ride out in an ID list;
      (b) a raw-string body {"positivePaperIds": "BHL"} was CHAR-ITERATED —
          'B','H','L' each look like clean strings — and also PASSED.
    Both now fail. This is the boundary that keeps researcher identity and .env
    values out of a third-party request body; it must fail closed, not open.
    """
    if not isinstance(body, dict):
        return False
    if set(body.keys()) - _S2_ALLOWED_BODY_KEYS:
        return False
    for k in _S2_ALLOWED_BODY_KEYS:
        if k not in body:
            continue
        vals = body[k]
        if not isinstance(vals, list):   # a bare str would be char-iterated
            return False
        for v in vals:
            if not isinstance(v, str) or len(v) > _S2_ID_MAXLEN:
                return False
            if "@" in v or not _S2_ID_RE.match(v):
                return False
    return True


def local_profile_match(text: Optional[str], terms) -> bool:
    """bioRxiv/medRxiv firehose local filter: substring (case-insensitive) match
    of any profile term against the paper's title+abstract. Pure."""
    if not text or not terms:
        return False
    low = text.lower()
    return any(t and t.lower() in low for t in terms)


def query_hash(source: str, terms) -> str:
    """Stable per-(source, query-set) hash for the watermark PK."""
    key = source + "|" + "|".join(sorted({(t or "").strip().lower() for t in (terms or []) if t}))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _paper_venue(p) -> Optional[str]:
    extra = getattr(p, "extra", None) or {}
    for k in ("journal", "container_title", "venue", "journalTitle", "container-title"):
        v = extra.get(k)
        if v:
            return str(v).strip()
    return None


def _is_preprint(source: str, venue: Optional[str]) -> bool:
    if (source or "").lower() in PREPRINT_SOURCES:
        return True
    v = (venue or "").lower()
    return any(h in v for h in PREPRINT_VENUE_HINTS)


def paper_to_record(p, *, source_api: str, scout_version: str = SCOUT_VERSION) -> dict:
    """Pure transform: a paper_search_mcp `Paper` (or any duck-typed object with
    the same attributes) -> a found/<INIT>.jsonl record in the EXACT shape
    ingest_live_papers.py consumes (canonical_id, doi, title, year, venue,
    abstract) plus the additive P33 fields (authors, pub_date, source,
    is_preprint) + provenance (source_api, scout_version). No network."""
    src = (getattr(p, "source", "") or "").lower()
    doi = norm_doi(getattr(p, "doi", None))
    if not doi:
        # arXiv rows often carry no DOI -> mint a synthetic 'arxiv:<id>' so
        # same_work()/dedup treat it as DOI-less (title-only merge).
        pid = (getattr(p, "paper_id", "") or "").strip()
        if src == "arxiv" and pid:
            doi = "arxiv:" + pid.lower()
    title = (getattr(p, "title", "") or "").strip() or None
    pub_dt = getattr(p, "published_date", None)
    pub_date = None
    year = None
    if pub_dt is not None:
        try:
            pub_date = pub_dt.strftime("%Y-%m-%d")
            year = int(pub_dt.year)
        except Exception:
            pub_date, year = None, None
    venue = _paper_venue(p)
    is_pre = _is_preprint(src, venue)
    if not venue and src:
        venue = _PRETTY_SOURCE.get(src)
    authors = [a for a in (getattr(p, "authors", None) or []) if a]
    abstract = (getattr(p, "abstract", "") or "").strip() or None
    return {
        "canonical_id": canonical_id(doi, title, year),
        "doi": doi,
        "title": title,
        "year": year,
        "venue": venue,
        "abstract": abstract,
        "authors": authors,
        "pub_date": pub_date,
        "source": "live_search",   # corpus classification (ingest hardcodes this too)
        "is_preprint": is_pre,
        "source_api": source_api,  # which API surfaced it (fetch provenance)
        "scout_version": scout_version,
    }


def dedup_records(records) -> list:
    """De-dup a per-researcher record list via the FIXED `_common.dedup_same_work`
    contract (real-DOI-equal collapse + synthetic<->real title-merge; two real
    distinct DOIs never merge; blank titles keep both). Keeps the richest
    representative (real DOI + abstract) of each group. Pure."""
    enriched = []
    for r in records:
        e = dict(r)
        e["title_norm"] = norm_title(r.get("title") or "")
        abstract = r.get("abstract") or ""
        # Richness tiers dominate (real DOI > synthetic; has-abstract > none);
        # abstract length only breaks ties within a tier (<1e-3, deterministic).
        score = 0.0
        if abstract:
            score += 1.0
        d = norm_doi(r.get("doi"))
        if d and not d.startswith("arxiv:"):
            score += 1.0
        score += min(len(abstract), 100000) / 1e8
        e["composite"] = score
        enriched.append(e)
    kept = dedup_same_work(enriched, composite_key="composite")
    out = []
    for e in kept:
        e = dict(e)
        e.pop("composite", None)
        e.pop("title_norm", None)
        out.append(e)
    return out


def retry_after_seconds(resp, attempt: int, *, base: float = 2.0, cap: float = 60.0) -> float:
    """Defensive Retry-After parse (P23 pattern): honour an integer/date header
    when present and sane, else exponential backoff. Never returns > `cap`."""
    hdr = None
    try:
        hdr = resp.headers.get("Retry-After")
    except Exception:
        hdr = None
    if hdr:
        try:
            return max(0.0, min(cap, float(int(hdr))))
        except (TypeError, ValueError):
            try:
                when = datetime.strptime(hdr, "%a, %d %b %Y %H:%M:%S %Z")
                delta = (when - datetime.utcnow()).total_seconds()
                if delta > 0:
                    return min(cap, delta)
            except Exception:
                pass
    return min(cap, base * (2 ** attempt))


# ===========================================================================
# Adapter layer (network) — instantiated + tuned; each fetch is tri-state.
# These are only exercised by the OPERATOR under --apply.
# ===========================================================================

def import_searchers() -> dict:
    """Import-guard smoke: assert each expected *Searcher class + .search exists.

    Returns {name: (class, search-signature)}. Raises on a missing class/method
    so the operator learns immediately that `requirements-discovery.txt` is not
    installed. NO instantiation, NO network."""
    import importlib
    import inspect

    out = {}
    for s in SOURCES:
        mod = importlib.import_module("paper_search_mcp.academic_platforms." + s.module)
        cls = getattr(mod, s.cls)
        if not hasattr(cls, "search"):
            raise AttributeError(f"{s.cls} has no .search method")
        out[s.name] = (cls, inspect.signature(cls.search))
    return out


class PolitenessFunnel:
    """Single throttle + UA/timeout funnel across every source (MF-8). Sources
    do not each get to hammer their host; a min-interval sleep gates all calls,
    and each searcher's session gets a real mailto UA (browser UA for DOAJ)."""

    def __init__(self, mailto: str, *, min_interval: float = 1.0, timeout: int = 25):
        self.mailto = mailto
        self.min_interval = min_interval
        self.timeout = timeout
        self._last = 0.0

    def wait(self) -> None:
        import time
        dt = time.monotonic() - self._last
        if dt < self.min_interval:
            time.sleep(self.min_interval - dt)
        self._last = time.monotonic()

    def tune(self, searcher, *, browser_ua: bool = False) -> None:
        ua = BROWSER_UA if browser_ua else f"csnl-paper-rec/1.0 (mailto:{self.mailto})"
        sess = getattr(searcher, "session", None)
        if sess is not None:
            try:
                sess.headers.update({"User-Agent": ua})
            except Exception:
                pass
        if hasattr(searcher, "timeout"):
            try:
                searcher.timeout = self.timeout
            except Exception:
                pass


def fetch_source(spec: SourceSpec, searcher, *, query: str, funnel: PolitenessFunnel,
                 max_results: int = DEFAULT_MAX_RESULTS,
                 firehose_days: int = DEFAULT_FIREHOSE_DAYS) -> list:
    """TRI-STATE single-source fetch. Returns a (possibly empty) list of Paper on
    success; RAISES on failure. It NEVER returns [] to signal an error — an empty
    list means 'the source succeeded and had nothing new'. Per-source try/except
    isolation is applied by the ORCHESTRATOR (run_all), not here."""
    funnel.wait()
    if spec.mode == "firehose":
        # bioRxiv/medRxiv: full date-window firehose (empty category), matched
        # locally afterwards — NEVER pass the profile as an API category.
        return searcher.search("", max_results=max_results, days=firehose_days)
    return searcher.search(query, max_results=max_results)


# ===========================================================================
# Watermark store (DB, operator-gated) — the pure math lives above; this thin
# wrapper is only reached under --apply. _db is imported LAZILY so merely
# importing this module never opens a connection.
# ===========================================================================

class WatermarkStore:
    """archive_discovery_watermark read/advance. commit-then-advance: callers
    write found/*.jsonl FIRST, then advance only the non-failed sources."""

    def __init__(self):
        import _db  # lazy — no connection at module import
        self._db = _db
        self._db.load_env()
        self.schema = self._db.ledger_schema()

    def read(self, source: str, qhash: str) -> Optional[date]:
        rows = self._db.query_json(
            f"SELECT last_index_date FROM {self.schema}.archive_discovery_watermark "
            f"WHERE source = '{source}' AND query_hash = '{qhash}'"
        )
        if not rows or not rows[0].get("last_index_date"):
            return None
        v = rows[0]["last_index_date"]
        if isinstance(v, date):
            return v
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()

    def advance(self, source: str, qhash: str, new_index_date: Optional[date],
                new_event_date: Optional[date]) -> None:
        """Idempotent UPSERT of the cursor. Only called for non-failed sources
        after the JSONL was committed to disk (commit-then-advance)."""
        idx = new_index_date.isoformat() if new_index_date else None
        evt = new_event_date.isoformat() if new_event_date else None
        idx_lit = f"'{idx}'" if idx else "NULL"
        evt_lit = f"'{evt}'" if evt else "NULL"
        self._db.exec_sql(
            f"INSERT INTO {self.schema}.archive_discovery_watermark "
            f"(source, query_hash, last_event_date, last_index_date, "
            f" last_success_at, updated_at) VALUES "
            f"('{source}', '{qhash}', {evt_lit}, {idx_lit}, now(), now()) "
            f"ON CONFLICT (source, query_hash) DO UPDATE SET "
            f"last_event_date = GREATEST("
            f"  {self.schema}.archive_discovery_watermark.last_event_date, EXCLUDED.last_event_date), "
            f"last_index_date = GREATEST("
            f"  {self.schema}.archive_discovery_watermark.last_index_date, EXCLUDED.last_index_date), "
            f"last_success_at = now(), updated_at = now()"
        )


# ===========================================================================
# S2 recommendations (network, operator-gated) — pure body-builder above.
# ===========================================================================

def s2_recommend(pos_dois, neg_dois, *, funnel: PolitenessFunnel, limit: int = 50,
                 api_key: Optional[str] = None,
                 min_pub_date: Optional[date] = None) -> list:
    """Seed the S2 recommendations endpoint with pos/neg DOI lists; post-filter
    by pub_date (recency is NOT native to the POST). Refuses to send a body that
    fails the egress check. 429/Retry-After backoff. Returns a list of raw S2
    recommendedPapers dicts (operator wires them into found records)."""
    import time

    import requests  # transitively pinned by requirements-discovery.txt

    body = build_s2_recommendation_body(pos_dois, neg_dois)
    if not egress_is_clean(body):
        raise ValueError("S2 egress body failed the identity/env leak check — refusing to POST")
    if not body["positivePaperIds"]:
        return []
    headers = {
        "User-Agent": f"csnl-paper-rec/1.0 (mailto:{funnel.mailto})",
        "Content-Type": "application/json",
    }
    if api_key:
        headers["x-api-key"] = api_key
    params = {"fields": "title,abstract,externalIds,publicationDate,venue", "limit": str(limit)}
    for attempt in range(S2_MAX_RETRIES):
        funnel.wait()
        resp = requests.post(S2_REC_URL, json=body, params=params, headers=headers,
                             timeout=funnel.timeout)
        if resp.status_code == 429:
            time.sleep(retry_after_seconds(resp, attempt))
            continue
        resp.raise_for_status()
        recs = (resp.json() or {}).get("recommendedPapers", []) or []
        if min_pub_date is None:
            return recs
        out = []
        for r in recs:
            pd = (r.get("publicationDate") or "")[:10]
            if not pd:
                out.append(r)  # keep dateless — better breadth than a silent drop
                continue
            try:
                if datetime.strptime(pd, "%Y-%m-%d").date() >= min_pub_date:
                    out.append(r)
            except Exception:
                out.append(r)
        return out
    raise RuntimeError("S2 recommendations exhausted retries (429)")


# ===========================================================================
# Inputs (operator-produced; NOT read from prod here)
# ===========================================================================

def load_inputs(init: str) -> Optional[dict]:
    """Read the operator-produced discovery inputs for a researcher.

    inputs/<INIT>.json = {
        "init": "BHL",
        "query_terms": ["serial dependence", "visual working memory", ...],
        "biorxiv_profile_terms": ["serial dependence", ...],   # firehose filter
        "pos_dois": ["10.x/...", "arxiv:2401.00001", ...],     # save/read seeds
        "neg_dois": ["10.y/..."]                               # not_relevant seeds
    }

    The AGENT never reads prod to build this; the operator runs a small DB-read
    step (or drops a fixture) to produce it. Absent -> None (source skipped)."""
    fp = INPUTS / f"{init}.json"
    if not fp.exists():
        return None
    return json.loads(fp.read_text(encoding="utf-8"))


# ===========================================================================
# Orchestration
# ===========================================================================

def run_all_for_researcher(init: str, inp: dict, *, funnel: PolitenessFunnel,
                           searchers: dict, args) -> tuple[list, dict]:
    """Fetch across all sources for one researcher. Returns (records, failed_map).

    Per-source try/except ISOLATION lives here: a source that raises is logged
    and skipped (its watermark won't advance), never fatal to the run."""
    terms = inp.get("query_terms") or []
    fire_terms = inp.get("biorxiv_profile_terms") or terms
    query = " OR ".join(f'"{t}"' for t in terms) if terms else ""
    records: list[dict] = []
    failed: dict[str, bool] = {}
    for spec in SOURCES:
        if args.source and spec.name != args.source:
            continue
        cls = searchers[spec.name][0]
        try:
            searcher = cls()
            funnel.tune(searcher, browser_ua=spec.browser_ua)
            papers = fetch_source(spec, searcher, query=query, funnel=funnel,
                                  max_results=args.max_results,
                                  firehose_days=args.firehose_days)
        except Exception as exc:  # per-source isolation — log + skip, never fatal
            failed[spec.name] = True
            print(f"  [{init}/{spec.name}] FAILED (skipped): {exc.__class__.__name__}: {exc}")
            continue
        kept_papers = papers
        if spec.mode == "firehose":
            kept_papers = [
                p for p in papers
                if local_profile_match(
                    ((getattr(p, "title", "") or "") + " " + (getattr(p, "abstract", "") or "")),
                    fire_terms)
            ]
        for p in kept_papers:
            records.append(paper_to_record(p, source_api=spec.name))
        print(f"  [{init}/{spec.name}] ok: {len(kept_papers)} kept "
              f"(of {len(papers)} fetched)")
    return dedup_records(records), failed


def _dry_run(args) -> None:
    print("DRY-RUN (no network, no DB). fetch_new_papers.py — plan:")
    print(f"  sources: {', '.join(s.name for s in SOURCES if not args.source or s.name == args.source)}")
    print(f"  max_results/source={args.max_results}  firehose_days={args.firehose_days}  "
          f"lookback/overlap={args.lookback_days}d")
    print(f"  mailto (polite pool)={args.mailto}   S2 seeds={'on' if args.s2 else 'off'}")
    inits = args.init.split(",") if args.init else [
        p.stem for p in sorted(INPUTS.glob("*.json"))]
    if not inits:
        print("  (no inputs/<INIT>.json found — operator must produce discovery "
              "inputs first: query_terms + pos/neg DOI lists.)")
    for init in inits:
        inp = load_inputs(init)
        if inp is None:
            print(f"  - {init}: NO inputs file (skipped)")
            continue
        terms = inp.get("query_terms") or []
        print(f"  - {init}: {len(terms)} query terms; "
              f"pos={len(inp.get('pos_dois') or [])} neg={len(inp.get('neg_dois') or [])}")
        if args.s2:
            body = build_s2_recommendation_body(inp.get("pos_dois"), inp.get("neg_dois"))
            clean = egress_is_clean(body)
            print(f"      S2 body: pos={len(body['positivePaperIds'])} "
                  f"neg={len(body.get('negativePaperIds', []))} egress_clean={clean}")
    print("\nDefault is dry-run. The OPERATOR runs `--apply` (attended) to fetch + "
          "write found/*.jsonl; this harness session does not.")


def _apply(args) -> None:
    # Operator-only path (attended). The agent never reaches here.
    searchers = import_searchers()  # import-guard smoke
    funnel = PolitenessFunnel(args.mailto, min_interval=args.min_interval,
                              timeout=args.timeout)
    wm = None if args.no_watermark else WatermarkStore()
    inits = args.init.split(",") if args.init else [
        p.stem for p in sorted(INPUTS.glob("*.json"))]
    FOUND.mkdir(parents=True, exist_ok=True)
    today = date.today()
    for init in inits:
        inp = load_inputs(init)
        if inp is None:
            print(f"[{init}] no inputs file — skipped")
            continue
        records, failed = run_all_for_researcher(
            init, inp, funnel=funnel, searchers=searchers, args=args)
        # commit-then-advance: write the JSONL FIRST.
        out_fp = FOUND / f"{init}.jsonl"
        with out_fp.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"[{init}] wrote {len(records)} records -> {out_fp}")
        # THEN advance the watermark for non-failed sources only.
        if wm is not None:
            terms = inp.get("query_terms") or []
            for spec in SOURCES:
                if args.source and spec.name != args.source:
                    continue
                if failed.get(spec.name):
                    continue  # failed source: cursor stays put
                qh = query_hash(spec.name, terms)
                stored = wm.read(spec.name, qh)
                batch_dates = [
                    datetime.strptime(r["pub_date"], "%Y-%m-%d").date()
                    for r in records
                    if r.get("source_api") == spec.name and r.get("pub_date")
                ]
                new_idx = compute_watermark_advance(stored, batch_dates, failed=False)
                new_evt = max(batch_dates) if batch_dates else stored
                wm.advance(spec.name, qh, new_idx, new_evt)
    print(f"[fetch] done ({kst_iso()}). found/*.jsonl written; operator now runs "
          "ingest_live_papers.py --apply (UNCHANGED).")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="P33 fetch-only discovery (dry-run default).")
    ap.add_argument("--apply", action="store_true",
                    help="OPERATOR-only: fetch + write found/*.jsonl (attended).")
    ap.add_argument("--init", default="", help="comma-separated researcher inits (default: all inputs/*.json).")
    ap.add_argument("--source", default="", help="restrict to a single source name.")
    ap.add_argument("--max-results", dest="max_results", type=int, default=DEFAULT_MAX_RESULTS)
    ap.add_argument("--firehose-days", dest="firehose_days", type=int, default=DEFAULT_FIREHOSE_DAYS)
    ap.add_argument("--lookback-days", dest="lookback_days", type=int, default=DEFAULT_OVERLAP_DAYS)
    ap.add_argument("--min-interval", dest="min_interval", type=float, default=1.0)
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--mailto", default=DEFAULT_MAILTO)
    ap.add_argument("--s2", action="store_true", help="include the S2 recommendations seed plan.")
    ap.add_argument("--no-watermark", dest="no_watermark", action="store_true",
                    help="OPERATOR: skip the watermark DB read/advance.")
    args = ap.parse_args(argv)
    if args.apply:
        _apply(args)
    else:
        _dry_run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
