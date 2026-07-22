#!/usr/bin/env python3
"""
scripts/archive/build_discovery_inputs.py — P33 Track B (B1). The MISSING
producer for `state/archive/discovery_run/inputs/<INIT>.json`.

WHY THIS EXISTS
  `scripts/archive/fetch_new_papers.py` reads one JSON file per researcher
  (`INPUTS` / `load_inputs()`) and SKIPS every researcher that lacks one (both
  `_dry_run` and `_apply` print "no inputs file"). Its `load_inputs` docstring
  says the inputs come from "a small DB-read step (or a fixture)" run by the
  operator — that step was never written, so the whole P33 discovery track
  could never run for anybody. This is that step.
  (Symbols, not line numbers: fetch_new_papers.py is owned elsewhere and moves.)

CONTRACT (must stay byte-compatible with fetch_new_papers.load_inputs())
  inputs/<INIT>.json = {
      "init": "BHL",
      "query_terms":           [...],   # topic-search phrases (quoted + OR-joined)
      "biorxiv_profile_terms": [...],   # firehose LOCAL substring filter
      "pos_dois":              [...],   # save_later + already_read seeds  (S2 positive)
      "neg_dois":              [...]    # not_relevant seeds               (S2 negative)
  }
  Extra keys are additive metadata only. Every consumer key is read with
  `.get(...)`, and only the five above are ever consumed — the `_meta` /
  `_known_negatives` blocks written here are provenance for the OPERATOR and
  are ignored by the fetcher.

WHERE THE DATA COMES FROM
  * pos/neg DOIs — `archive_responses ⋈ archive_papers` (SELECT only):
        save_later | already_read  -> pos_dois
        not_relevant               -> neg_dois
        skipped                    -> ignored (no signal)
    DOIs are normalised with `_common.norm_doi` (so the synthetic 'arxiv:<id>'
    key survives and `fetch_new_papers.to_s2_id` maps it to 'ARXIV:<id>'), then
    de-duplicated most-recent-first and capped.
  * query_terms — the best AVAILABLE profile source, in this precedence:
        1. `archive_survey_keywords`  (P28 survey memory — authoritative, but
           the P28 migration may be unapplied; probed, never assumed)
        2. `state/archive/profiles/<INIT>.json`  (P26a Opus-extracted
           aims/phenomena/mechanisms — present for all 7 researchers)
        3. `state/archive/fingerprints/<INIT>.json`  (P19a/P24 phrases; the
           TF-IDF-salvage unigrams are filtered out — see `terms_from_fingerprint`)
    `--profile-source` pins one source; the default `auto` walks the chain and
    stops at the first source yielding >= --min-terms. A researcher with NO
    usable source is SKIPPED with a warning — terms are never fabricated.

BOUNDARIES (P33, non-negotiable)
  * READS prod, NEVER writes it. Every DB call in this file goes through
    `_db.query_json` (SELECT). `exec_sql` / `exec_many` are not imported.
    `csnl_research` is not touched at all; only the ledger schema is read.
  * NO network, NO Notion, NO SMTP, NO Slack, NO sends. Nothing here can
    deliver anything to a researcher.
  * DEFAULT = dry-run (prints the plan; writes nothing). `--apply` writes ONLY
    `inputs/<INIT>.json` files.
  * `--no-db` runs fully offline (profile/fingerprint terms only, empty DOI
    lists) so the term logic can be verified without touching prod.

REVERSIBLE: new file; its only output is `state/archive/discovery_run/inputs/`
which is gitignored (.gitignore:29 `state/archive/discovery_run/`) and
regenerable — undo with `rm -r state/archive/discovery_run/inputs`.

USAGE (operator, attended)
    python3 scripts/archive/build_discovery_inputs.py                 # dry-run, all
    python3 scripts/archive/build_discovery_inputs.py --init BHL --print-json
    python3 scripts/archive/build_discovery_inputs.py --apply         # write files
    python3 scripts/archive/build_discovery_inputs.py --no-db --apply # offline terms
  then the existing (UNCHANGED) chain:
    python3 scripts/archive/fetch_new_papers.py            # dry-run plan
    python3 scripts/archive/fetch_new_papers.py --apply --mailto <real address>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "pipeline"))

from _common import kst_iso, norm_doi  # noqa: E402

GENERATOR = "p33-build-discovery-inputs-2026-07-22"

PROFILES_DIR = ROOT / "state/archive/profiles"
FINGERPRINTS_DIR = ROOT / "state/archive/fingerprints"

# Canonical roster fallback (matches ingest_survey.RESEARCHERS / ingest_replies).
ROSTER_FALLBACK = ("BHL", "BYL", "JOP", "JYK", "MSY", "SMJ", "SYJ")
_YAML_ROW_RE = re.compile(r"^\s*([A-Z]{2,8})\s*:\s*\{")
_INIT_RE = re.compile(r"^[A-Z]{2,8}$")

# archive_responses.choice -> polarity. 'skipped' carries no signal and is
# deliberately absent (a skip is "I could not judge", not "not relevant").
POS_CHOICES = ("save_later", "already_read")
NEG_CHOICES = ("not_relevant",)

DEFAULT_MAX_TERMS = 12       # OR-joined into ONE query string by the fetcher
DEFAULT_MIN_TERMS = 3        # below this, `auto` falls through to the next source
# The fetcher wraps every term in double quotes -> an EXACT-PHRASE query. A
# 6+-word descriptive phrase ("repulsive and attractive biases in orientation
# estimation") is a real phrase for a human and a zero-hit query for Crossref /
# PubMed / arXiv, so long candidates are shredded into their fragments instead.
DEFAULT_MAX_WORDS = 5
DEFAULT_MAX_DOIS = 200       # per polarity (the S2 body caps again at 100)
FIREHOSE_MIN_WORDS = 2       # substring filter: a unigram matches far too much
FIREHOSE_MAX_WORDS = 4       # ...and a 5+-word phrase matches almost nothing
DEFAULT_MAX_FIREHOSE = 20

# Fingerprint channels that mark a CURATED phrase (P19a lexicon anchor /
# paradigm / context, or a P24 evolution add). 'passB.tfidf' is the cold-start
# TF-IDF salvage — it yields junk unigrams ("reference", "none", "ignore"), so
# a passB-only phrase is kept ONLY when it is multiword.
CURATED_CHANNEL_PREFIXES = ("anchor", "context", "paradigm", "evo.")

# Unigrams that are true in every paper ever written — never a query term.
GENERIC_UNIGRAMS = frozenset({
    "none", "ignore", "target", "item", "reference", "centered", "center",
    "present", "absent", "condition", "conditions", "task", "tasks", "trial",
    "trials", "stimulus", "stimuli", "response", "responses", "effect",
    "effects", "model", "models", "data", "analysis", "study", "studies",
    "experiment", "experiments", "participant", "participants", "subject",
    "subjects", "result", "results", "method", "methods", "measure",
    "measures", "value", "values", "input", "output", "false", "true",
    "description", "level", "levels", "group", "groups", "type", "types",
})

# A phrase that STARTS or ENDS on one of these is a shredded fragment, not a
# search phrase ("accounts of memory under", "and sequential sampling").
BOUNDARY_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "vs", "versus", "of", "in", "on", "at",
    "to", "for", "from", "by", "with", "as", "into", "under", "over",
    "between", "across", "during", "than", "that", "which", "when", "while",
    "both", "either", "neither", "its", "their", "this", "these", "those",
    "is", "are", "be", "been", "such", "via", "per", "how", "why", "whether",
})

_PAREN_RE = re.compile(r"\([^()]*\)")
_WS_RE = re.compile(r"\s+")
# Apostrophes are DELETED, not spaced ("Gambler's fallacy" -> "Gamblers
# fallacy", never "Gambler s fallacy").
_APOSTROPHE_RE = re.compile(r"[’‘'`´]")
# Characters that would corrupt the fetcher's `" OR ".join(f'"{t}"')` phrase
# query (quotes) or a URL query string (brackets/colons/backslashes).
_STRIP_CHARS_RE = re.compile(r"[\"“”()\[\]{}:;|\\<>]")
_SPLIT_LONG_RE = re.compile(r"\s*[/;,]\s*|\s+vs\.?\s+|\s+versus\s+")


# ===========================================================================
# PURE helpers — no DB, no network, no filesystem. Unit-testable.
# ===========================================================================

def strip_parens(raw: Optional[str]) -> str:
    """Remove (possibly nested) parentheticals. Pure.

    Applied BEFORE any splitting: a parenthetical is an aside, and splitting
    the raw text on '/' or 'vs' inside one manufactures nonsense fragments
    ("Task-dependent (active vs. passive) utilization" -> "Task-dependent
    active").
    """
    s = str(raw or "")
    for _ in range(3):                       # nested parentheticals
        s2 = _PAREN_RE.sub(" ", s)
        if s2 == s:
            break
        s = s2
    return s


def clean_term(raw: Optional[str], *, max_words: int = DEFAULT_MAX_WORDS) -> Optional[str]:
    """Normalise one candidate phrase into a safe search term, or None.

    Drops parentheticals ("serial dependence (attraction ...)" -> "serial
    dependence"), strips query-metacharacters, collapses whitespace, and
    REJECTS: empties, <4-char terms, >max_words phrases, bare generic unigrams,
    and fragments that start or end on a function word. Pure + deterministic;
    case is preserved (APIs match case-insensitively) but callers de-duplicate
    case-insensitively.
    """
    if not raw:
        return None
    s = strip_parens(raw)
    s = _APOSTROPHE_RE.sub("", s)
    s = _STRIP_CHARS_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip(" -–—.,")
    if not s:
        return None
    words = s.split()
    if len(words) > max_words:
        return None
    if len(s) < 4:
        return None
    if len(words) == 1 and words[0].lower() in GENERIC_UNIGRAMS:
        return None
    if len(words) > 1 and (words[0].lower() in BOUNDARY_STOPWORDS
                           or words[-1].lower() in BOUNDARY_STOPWORDS):
        return None
    return s


def slash_variants(term: str, *, max_words: int = DEFAULT_MAX_WORDS) -> list:
    """Resolve a slash-form phrase into searchable alternatives.

    "orientation/feature estimation" is one idea to a human and a zero-hit
    exact-phrase query to every API we call. Split on '/', keep the fragments
    that are still >= 2 words, and fall back to the de-slashed form when no
    fragment survives (so nothing is silently lost).
    """
    if "/" not in term:
        return [term]
    frags = []
    for part in term.split("/"):
        f = clean_term(part, max_words=max_words)
        if f and len(f.split()) >= 2:
            frags.append(f)
    if frags:
        return frags
    flat = clean_term(term.replace("/", " "), max_words=max_words)
    return [flat] if flat else []


def expand_terms(raw_terms, *, max_words: int = DEFAULT_MAX_WORDS) -> list:
    """Clean a list of candidate phrases, splitting the over-long ones.

    A phrase that survives cleaning is kept (slash-forms resolved). A phrase
    that is too long (a profile *aim* is a full sentence) is split on '/', ';',
    ',' and 'vs' and each fragment is re-cleaned; fragments that are still
    sentence-shaped are dropped. Order is preserved (callers rely on source
    ordering carrying the ranking signal); duplicates are removed
    case-insensitively.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(t: Optional[str]) -> None:
        if not t:
            return
        k = t.casefold()
        if k in seen:
            return
        seen.add(k)
        out.append(t)

    for raw in (raw_terms or []):
        t = clean_term(raw, max_words=max_words)
        if t:
            for v in slash_variants(t, max_words=max_words):
                _add(v)
            continue
        for frag in _SPLIT_LONG_RE.split(strip_parens(raw)):
            f = clean_term(frag, max_words=max_words)
            # A 1-word fragment of a split sentence is noise; require >= 2.
            if f and len(f.split()) >= 2:
                _add(f)
    return out


def rank_terms(terms, *, max_terms: int = DEFAULT_MAX_TERMS) -> list:
    """Cap the term list, preferring SPECIFIC-but-findable phrases.

    Buckets, each keeping source order (which encodes the source's own ranking:
    fingerprint score, profile ordering, survey ordering):
      0. 2-4 words  — the sweet spot for an exact-phrase literature query
      1. 5+ words   — precise but low-recall; kept only to fill the cap
      2. 1 word     — high recall, low precision; last resort
    Stable and deterministic — the fetcher's watermark PK hashes the *set* of
    terms (`fetch_new_papers.query_hash`), so churn here churns the cursor.
    """
    def _bucket(t: str) -> int:
        w = len(t.split())
        if w == 1:
            return 2
        return 0 if w <= 4 else 1

    ordered = sorted(((_bucket(t), i, t) for i, t in enumerate(terms or [])),
                     key=lambda kv: (kv[0], kv[1]))
    return [t for _b, _i, t in ordered][:max_terms]


def pick_firehose_terms(terms, *, max_terms: int = DEFAULT_MAX_FIREHOSE) -> list:
    """Terms for the bioRxiv/medRxiv LOCAL substring filter.

    `fetch_new_papers.local_profile_match` does a plain case-insensitive
    substring test over title+abstract, so a unigram over-matches and a long
    phrase never matches. Keep the 2..4-word band; if that is empty, fall back
    to the full list rather than filtering the firehose down to nothing.
    """
    band = [t for t in terms
            if FIREHOSE_MIN_WORDS <= len(t.split()) <= FIREHOSE_MAX_WORDS]
    return (band or list(terms))[:max_terms]


def terms_from_profile(profile_json: dict, *, include_aims: bool = False,
                       max_words: int = DEFAULT_MAX_WORDS) -> list:
    """P26a profile -> query terms.

    `phenomena` and `mechanisms_theories` are already phrase-shaped and are the
    P26 relevance contract's axes B and C, so they are the primary source.
    `aims` (axis A) are full sentences — included only under --include-aims,
    where `expand_terms` shreds them into fragments.
    """
    prof = (profile_json or {}).get("profile") or {}
    raw: list = []
    raw += list(prof.get("phenomena") or [])
    raw += list(prof.get("mechanisms_theories") or [])
    if include_aims:
        raw += list(prof.get("aims") or [])
    return expand_terms(raw, max_words=max_words)


def profile_known_negatives(profile_json: dict) -> list:
    """The profile's `known_negatives` — informational passthrough only.

    NO consumer reads this today (the fetcher's contract has no negative-term
    key); it is written into `_known_negatives` so the operator can see what
    the gate WILL veto downstream (`recommend.py` / the P26 reasoning gate).
    """
    prof = (profile_json or {}).get("profile") or {}
    return [str(x).strip() for x in (prof.get("known_negatives") or []) if str(x).strip()]


def _is_curated_channel(channels) -> bool:
    for c in (channels or []):
        cl = str(c).lower()
        if any(cl.startswith(p) for p in CURATED_CHANNEL_PREFIXES):
            return True
    return False


def terms_from_fingerprint(fp_json: dict, *, max_words: int = DEFAULT_MAX_WORDS) -> list:
    """P19a/P24 fingerprint -> query terms, score-ordered.

    Keeps a phrase when it is curated (channel anchor/context/paradigm/evo.*)
    OR multiword. A `passB.tfidf`-only UNIGRAM is dropped: that channel is the
    P19b cold-start TF-IDF salvage and yields terms like "reference" /
    "none" / "ignore" that would poison a literature query.
    """
    phrases = (fp_json or {}).get("phrases") or []
    try:
        phrases = sorted(phrases, key=lambda p: -float(p.get("score") or 0.0))
    except (TypeError, ValueError):
        pass
    raw: list = []
    for p in phrases:
        txt = (p or {}).get("phrase")
        if not txt:
            continue
        if _is_curated_channel(p.get("channels")) or len(str(txt).split()) > 1:
            raw.append(txt)
    return expand_terms(raw, max_words=max_words)


def terms_from_survey_rows(rows, *, max_words: int = DEFAULT_MAX_WORDS) -> list:
    """`archive_survey_keywords` rows -> query terms.

    Unambiguous keywords first, then the §G2 ambiguous ones (they still make
    fine *fetch* terms — their conflict sense is subtracted later, at ranking
    time, by `recommend.py`'s definition-aware pass, not here).
    """
    plain, ambiguous = [], []
    for r in (rows or []):
        kw = (r or {}).get("keyword")
        if not kw:
            continue
        (ambiguous if r.get("is_ambiguous") else plain).append(kw)
    return expand_terms(plain + ambiguous, max_words=max_words)


def group_response_dois(rows, *, max_dois: int = DEFAULT_MAX_DOIS) -> dict:
    """`archive_responses ⋈ archive_papers` rows -> {INIT: {"pos": [...], "neg": [...]}}.

    Rows are (researcher_id, choice, doi, responded_at). Newest-first so the
    cap keeps the freshest signal; DOIs are normalised (synthetic 'arxiv:<id>'
    preserved) and de-duplicated. A DOI that appears on both sides for one
    researcher cannot happen (archive_responses PK is (researcher, paper)), but
    if it ever did, the POSITIVE list wins and the negative copy is dropped —
    never seed S2 with a contradiction.
    """
    ordered = sorted(
        (r for r in (rows or []) if r),
        key=lambda r: str(r.get("responded_at") or ""),
        reverse=True,
    )
    out: dict[str, dict] = {}
    for r in ordered:
        rid = str(r.get("researcher_id") or "").strip().upper()
        if not _INIT_RE.match(rid):
            continue
        doi = norm_doi(r.get("doi"))
        if not doi:
            continue
        choice = str(r.get("choice") or "")
        if choice in POS_CHOICES:
            key = "pos"
        elif choice in NEG_CHOICES:
            key = "neg"
        else:
            continue
        bucket = out.setdefault(rid, {"pos": [], "neg": [], "_seen": set()})
        if doi in bucket["_seen"]:
            continue
        bucket["_seen"].add(doi)
        if len(bucket[key]) < max_dois:
            bucket[key].append(doi)
    for b in out.values():
        b.pop("_seen", None)
    return out


def build_record(init: str, *, query_terms, firehose_terms, pos_dois, neg_dois,
                 meta: Optional[dict] = None, known_negatives=None) -> dict:
    """Assemble the exact `inputs/<INIT>.json` payload (contract keys first)."""
    rec = {
        "init": init,
        "query_terms": list(query_terms or []),
        "biorxiv_profile_terms": list(firehose_terms or []),
        "pos_dois": list(pos_dois or []),
        "neg_dois": list(neg_dois or []),
    }
    if known_negatives:
        # Informational only — no consumer key. Documented in the docstring.
        rec["_known_negatives"] = list(known_negatives)
    rec["_meta"] = dict(meta or {})
    return rec


# ===========================================================================
# Sources — filesystem
# ===========================================================================

def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"  ! unreadable {path}: {exc.__class__.__name__}: {exc}")
        return None


def load_roster(explicit: str = "") -> list:
    """Researcher inits: --init list, else config/researchers.yaml, else the
    canonical 7. Leniently parsed (no hard PyYAML dependency), same pattern as
    scripts/weekly/ingest_grm_nas.load_registry()."""
    if explicit:
        got = [i.strip().upper() for i in explicit.split(",") if i.strip()]
        bad = [i for i in got if not _INIT_RE.match(i)]
        if bad:
            raise SystemExit(f"invalid --init value(s): {', '.join(bad)}")
        return got
    path = ROOT / "config" / "researchers.yaml"
    inits: list[str] = []
    try:
        in_block = False
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.startswith("researchers:"):
                in_block = True
                continue
            if in_block and raw and not raw[0].isspace():
                break
            if in_block:
                m = _YAML_ROW_RE.match(raw)
                if m:
                    inits.append(m.group(1).upper())
    except OSError:
        pass
    return inits or list(ROSTER_FALLBACK)


def default_inputs_dir() -> Path:
    """The fetcher's INPUTS path — imported from it so the two can never drift."""
    try:
        from fetch_new_papers import INPUTS as _I  # noqa: WPS433 (single source of truth)
        return Path(_I)
    except Exception:
        return ROOT / "state/archive/discovery_run/inputs"


# ===========================================================================
# Sources — Postgres. SELECT ONLY. `_db` is imported LAZILY so --help / --no-db
# never open a connection, and `exec_sql`/`exec_many` are never imported here.
# ===========================================================================

class Reader:
    """Read-only ledger accessor. Every method is a SELECT through query_json."""

    def __init__(self):
        import _db  # lazy — no connection at module import
        self._db = _db
        self._db.load_env()
        self.schema = self._db.ledger_schema()   # validated identifier

    def table_exists(self, table: str) -> bool:
        if not re.match(r"^[a-z_][a-z0-9_]*$", table):
            return False
        rows = self._db.query_json(
            "SELECT 1 AS ok FROM information_schema.tables "
            f"WHERE table_schema = '{self.schema}' AND table_name = '{table}' LIMIT 1"
        )
        return bool(rows)

    def response_dois(self) -> list:
        """(researcher_id, choice, doi, responded_at) for every scored paper.

        One query for the whole lab (not N per researcher). Only papers that
        actually carry a DOI can seed S2, so the no-DOI rows are filtered in
        SQL. `archive_responses` is READ here and never written — it is the
        permanent truth source of read / to_read / not_interested.
        """
        choices = "','".join(POS_CHOICES + NEG_CHOICES)
        return self._db.query_json(
            "SELECT r.researcher_id, r.choice, p.doi, r.responded_at "
            f"FROM {self.schema}.archive_responses r "
            f"JOIN {self.schema}.archive_papers p ON p.canonical_id = r.canonical_id "
            f"WHERE r.choice IN ('{choices}') "
            "  AND p.doi IS NOT NULL AND btrim(p.doi) <> '' "
            "ORDER BY r.researcher_id, r.responded_at DESC"
        )

    def survey_keywords(self) -> dict:
        """{INIT: [rows]} from archive_survey_keywords, or {} when the P28
        migration is not applied (probed, never assumed — D-gaps §1.2)."""
        if not self.table_exists("archive_survey_keywords"):
            return {}
        rows = self._db.query_json(
            "SELECT researcher_id, keyword, is_ambiguous, operational_def "
            f"FROM {self.schema}.archive_survey_keywords "
            "ORDER BY researcher_id, is_ambiguous, keyword"
        )
        out: dict[str, list] = {}
        for r in rows or []:
            rid = str(r.get("researcher_id") or "").strip().upper()
            if _INIT_RE.match(rid):
                out.setdefault(rid, []).append(r)
        return out


# ===========================================================================
# Assembly
# ===========================================================================

def terms_for(init: str, *, source: str, survey_rows: dict, args) -> tuple:
    """Resolve (terms, primary_source, topup_sources, note) for one researcher.

    `auto` walks survey -> profile -> fingerprint and takes the first source
    yielding >= args.min_terms as PRIMARY (its ordering leads); if none clears
    the floor, the richest non-empty source is primary and the note says so.
    The remaining sources then TOP UP the list (union, de-duplicated, order
    preserved) unless --no-topup: the fetcher OR-joins the terms, so a union
    only widens recall, and a thin primary (e.g. MSY's 3 profile phrases) would
    otherwise under-fetch. Nothing is ever invented — an empty result means the
    caller skips the researcher.
    """
    def _try(name: str) -> list:
        if name == "survey":
            return terms_from_survey_rows(survey_rows.get(init) or [],
                                          max_words=args.max_words)
        if name == "profile":
            return terms_from_profile(_read_json(PROFILES_DIR / f"{init}.json") or {},
                                      include_aims=args.include_aims,
                                      max_words=args.max_words)
        if name == "fingerprint":
            return terms_from_fingerprint(_read_json(FINGERPRINTS_DIR / f"{init}.json") or {},
                                          max_words=args.max_words)
        return []

    chain = ["survey", "profile", "fingerprint"] if source == "auto" else [source]
    attempts = [(name, _try(name)) for name in chain]
    non_empty = [(n, t) for n, t in attempts if t]
    if not non_empty:
        return [], "none", [], "no usable profile source"

    primary_name, primary = next(
        ((n, t) for n, t in attempts if len(t) >= args.min_terms),
        max(non_empty, key=lambda kv: len(kv[1])),
    )
    note = f"{primary_name} ({len(primary)} terms"
    if len(primary) < args.min_terms:
        note += ", BELOW --min-terms"
    note += ")"

    terms = list(primary)
    topups: list[str] = []
    if not args.no_topup:
        seen = {t.casefold() for t in terms}
        for name, got in attempts:
            if name == primary_name or not got:
                continue
            added = 0
            for t in got:
                if t.casefold() in seen:
                    continue
                seen.add(t.casefold())
                terms.append(t)
                added += 1
            if added:
                topups.append(f"{name}(+{added})")
        if topups:
            note += " + topup " + ", ".join(topups)
    return terms, primary_name, topups, note


def build_for(init: str, *, survey_rows: dict, dois: dict, args) -> Optional[dict]:
    """Full record for one researcher, or None when there are no terms."""
    terms, src, topups, note = terms_for(init, source=args.profile_source,
                                         survey_rows=survey_rows, args=args)
    if not terms:
        print(f"  - {init}: SKIPPED — {note} "
              f"(looked in archive_survey_keywords, {PROFILES_DIR.name}/, "
              f"{FINGERPRINTS_DIR.name}/)")
        return None
    ranked = rank_terms(terms, max_terms=args.max_terms)
    firehose = pick_firehose_terms(ranked, max_terms=args.max_firehose)
    bucket = dois.get(init) or {}
    pos = bucket.get("pos") or []
    neg = bucket.get("neg") or []
    prof = _read_json(PROFILES_DIR / f"{init}.json") or {}
    meta = {
        "generator": GENERATOR,
        "generated_at": kst_iso(),
        "profile_source": src,
        "profile_source_note": note,
        "topup_sources": list(topups),
        "n_terms_available": len(terms),
        "n_terms_used": len(ranked),
        "max_words": args.max_words,
        "include_aims": bool(args.include_aims),
        "doi_source": "none (--no-db)" if args.no_db
                      else "archive_responses ⋈ archive_papers (SELECT only)",
        "n_pos_dois": len(pos),
        "n_neg_dois": len(neg),
        "max_dois_per_polarity": args.max_dois,
    }
    if not pos:
        meta["warning_no_pos_dois"] = (
            "S2 recommendations need >= 1 positive seed; that source will "
            "no-op for this researcher until they answer an interview/digest.")
    return build_record(init, query_terms=ranked, firehose_terms=firehose,
                        pos_dois=pos, neg_dois=neg, meta=meta,
                        known_negatives=profile_known_negatives(prof))


def write_record(out_dir: Path, init: str, rec: dict) -> Path:
    """Atomic write of one inputs file (tmp + os.replace — a crashed run can
    never leave a half-written JSON that `load_inputs` would choke on)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fp = out_dir / f"{init}.json"
    payload = json.dumps(rec, ensure_ascii=False, indent=1) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(out_dir), prefix=f".{init}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, fp)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return fp


def _preview(rec: dict, out_fp: Path, *, show_json: bool) -> None:
    terms = rec["query_terms"]
    head = ", ".join(terms[:5]) + (" …" if len(terms) > 5 else "")
    state = "OVERWRITE" if out_fp.exists() else "NEW"
    print(f"  - {rec['init']}: {len(terms)} query terms "
          f"[{rec['_meta']['profile_source']}] | firehose="
          f"{len(rec['biorxiv_profile_terms'])} | pos={len(rec['pos_dois'])} "
          f"neg={len(rec['neg_dois'])} -> {state} {out_fp}")
    print(f"      terms: {head}")
    if not rec["pos_dois"]:
        print("      ⚠ no positive DOI seeds — S2 recommendations will no-op "
              "for this researcher.")
    if show_json:
        print(json.dumps(rec, ensure_ascii=False, indent=1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Produce discovery_run/inputs/<INIT>.json for "
                    "fetch_new_papers.py (dry-run default; READ-ONLY on prod).")
    ap.add_argument("--apply", action="store_true",
                    help="write the inputs/*.json files (default: dry-run, no writes).")
    ap.add_argument("--init", default="",
                    help="comma-separated researcher inits (default: config/researchers.yaml).")
    ap.add_argument("--profile-source", dest="profile_source", default="auto",
                    choices=("auto", "survey", "profile", "fingerprint"),
                    help="term source; 'auto' = survey -> profile -> fingerprint.")
    ap.add_argument("--include-aims", dest="include_aims", action="store_true",
                    help="also shred the profile's aims (sentences) into terms.")
    ap.add_argument("--no-topup", dest="no_topup", action="store_true",
                    help="use ONLY the primary source (default: top up from the "
                         "other sources, de-duplicated, to fill --max-terms).")
    ap.add_argument("--max-terms", dest="max_terms", type=int, default=DEFAULT_MAX_TERMS)
    ap.add_argument("--min-terms", dest="min_terms", type=int, default=DEFAULT_MIN_TERMS,
                    help="'auto' falls through to the next source below this count.")
    ap.add_argument("--max-words", dest="max_words", type=int, default=DEFAULT_MAX_WORDS,
                    help="reject a phrase longer than this many words.")
    ap.add_argument("--max-firehose", dest="max_firehose", type=int,
                    default=DEFAULT_MAX_FIREHOSE,
                    help="cap on biorxiv_profile_terms (local substring filter).")
    ap.add_argument("--max-dois", dest="max_dois", type=int, default=DEFAULT_MAX_DOIS,
                    help="cap per polarity, newest response first.")
    ap.add_argument("--no-db", dest="no_db", action="store_true",
                    help="offline: skip every SELECT; DOI lists come out empty.")
    ap.add_argument("--out-dir", dest="out_dir", default="",
                    help="override the inputs directory (default: the path "
                         "fetch_new_papers.py reads).")
    ap.add_argument("--print-json", dest="print_json", action="store_true",
                    help="dry-run: dump each full record.")
    args = ap.parse_args(argv)

    if args.max_terms < 1 or args.min_terms < 1 or args.max_words < 1 or args.max_dois < 1:
        raise SystemExit("--max-terms/--min-terms/--max-words/--max-dois must be >= 1")

    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else default_inputs_dir()
    inits = load_roster(args.init)

    mode = "APPLY (writes inputs/*.json)" if args.apply else "DRY-RUN (no writes)"
    print(f"build_discovery_inputs — {mode}")
    print(f"  out_dir: {out_dir}")
    print(f"  researchers: {', '.join(inits)}")
    print(f"  term source: {args.profile_source} (max {args.max_terms}, "
          f"floor {args.min_terms}, <= {args.max_words} words)")

    survey_rows: dict = {}
    dois: dict = {}
    if args.no_db:
        print("  DB: SKIPPED (--no-db) — pos_dois/neg_dois will be empty.")
    else:
        reader = Reader()                      # SELECT-only accessor
        print(f"  DB: read-only SELECT on {reader.schema} "
              f"(archive_responses ⋈ archive_papers; archive_survey_keywords if present)")
        survey_rows = reader.survey_keywords()
        if not survey_rows:
            print("      archive_survey_keywords absent/empty (P28 migration "
                  "unapplied?) — falling back to profiles/ then fingerprints/.")
        dois = group_response_dois(reader.response_dois(), max_dois=args.max_dois)

    built, skipped = 0, 0
    for init in inits:
        rec = build_for(init, survey_rows=survey_rows, dois=dois, args=args)
        if rec is None:
            skipped += 1
            continue
        fp = out_dir / f"{init}.json"
        if args.apply:
            fp = write_record(out_dir, init, rec)
            print(f"  - {init}: wrote {len(rec['query_terms'])} terms / "
                  f"pos={len(rec['pos_dois'])} neg={len(rec['neg_dois'])} "
                  f"[{rec['_meta']['profile_source']}] -> {fp}")
        else:
            _preview(rec, fp, show_json=args.print_json)
        built += 1

    print(f"\n{'wrote' if args.apply else 'would write'} {built} file(s); "
          f"{skipped} researcher(s) skipped (no usable profile source).")
    if not args.apply:
        print("Default is dry-run. Re-run with --apply to write. "
              "This script never writes the DB and never sends anything.")
    else:
        print("Next (operator, attended): python3 scripts/archive/fetch_new_papers.py "
              "--apply --mailto <real lab address>")
    return 0 if built else 2


if __name__ == "__main__":
    raise SystemExit(main())
