#!/usr/bin/env python3
"""
scripts/archive/quarantine_bad_records.py — corpus-coherence checker (P34 / S3).

WHY THIS EXISTS
---------------
The batch06 adversarial review (state/archive/_explore/batch06/L3-risk.md §L3-2,
L2-recommender.md §5) found a *Frankenstein* row sitting A-tier in 6 of 7 live
queues and fully eligible for the next refill:

    canonical_id 417e4953f0c03eb30028183920dbdd35
      title   : Мултиплициране на „българщината”      (Bulgarian humanities)
      authors : ["Евгения Иванова"]
      venue   : Journal of Vision
      year    : 2010
      doi     : 10.1167/19.13.21   → JoV vol.19 = 2019 (year wrong by 9)
      abstract: "Perceptual decisions ... serial dependence ..."  (the REAL paper)

Nothing on the send path checks record coherence: `render.py` builds the APA-7
card straight from title/authors/venue/year, the tone lint sees no banned term,
and `mirror_history.py` then copies the bogus citation into the researcher's
permanent 논문 리스트. MSY has zero responses — this row would be her cold start.

WHAT THIS DOES
--------------
Read-only scan of `archive_papers` (+ synopses / queues / responses / digests
for context and ranking) applying deterministic coherence rules:

  identity     ID_DRIFT, DOI_DUPLICATE, TITLE_DUPLICATE
  title        TITLE_MISSING, TITLE_IS_FILENAME, TITLE_IS_IDENTIFIER,
               TITLE_MOSTLY_PUNCT, TITLE_SCRIPT_MISMATCH,
               TITLE_VENUE_SCRIPT_MISMATCH, TITLE_VENUE_SWAPPED
  date         YEAR_MISSING, YEAR_IMPLAUSIBLE, YEAR_DOI_MISMATCH
  body         ABSTRACT_MISSING, ABSTRACT_BOILERPLATE
  coherence    SYNOPSIS_TITLE_MISMATCH, VENUE_DOI_MISMATCH

Rows are RANKED BY LIVE-QUEUE EXPOSURE first (a bad row nobody can receive is
not urgent; a bad row that is A-tier in 6 queues is a P0).

BOUNDARIES (hard)
-----------------
* Default is DRY-RUN. It prints a report and writes JSONL. It writes nothing.
* `--apply` is OPERATOR-ONLY and does exactly two things: create
  `<ledger>.archive_paper_quarantine` if absent, and INSERT/UPSERT flagged rows
  into it. It NEVER updates or deletes `archive_papers`, never touches
  `archive_responses`, never touches `csnl_research`. `_assert_safe_sql()`
  enforces this at run time, not by convention.
* Nothing here sends anything. No Notion, no SMTP, no LLM.
* Reversal of `--apply`: `DROP TABLE <ledger>.archive_paper_quarantine;`
  (the quarantine is a *sidecar* table precisely so reversal is one statement).

USAGE
-----
  # dry-run against prod (read-only), full report + JSONL
  python3 scripts/archive/quarantine_bad_records.py

  # just the live-queue-exposed rows, terse
  python3 scripts/archive/quarantine_bad_records.py --queued-only

  # inspect one record (the review's example)
  python3 scripts/archive/quarantine_bad_records.py --only 417e4953f0c03eb30028183920dbdd35 -v

  # offline rule-engine test (no DB, no network)
  python3 scripts/archive/quarantine_bad_records.py --self-test

  # OPERATOR ONLY — mark high/critical rows in the sidecar table
  ! python3 scripts/archive/quarantine_bad_records.py --apply --yes-i-am-operator

A consumer (queue builder / build_digest) is expected to exclude
`canonical_id IN (SELECT canonical_id FROM archive_paper_quarantine
                 WHERE released_at IS NULL)`.
Wiring that exclusion is a SEPARATE unit — this file only reports and marks.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "archive"))

KST = timezone(timedelta(hours=9))
DETECTOR = "quarantine_bad_records@v1"

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
TIER_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3, None: 4}


def kst_iso() -> str:
    return datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")


# =========================================================================
# text / script helpers  (pure — offline testable)
# =========================================================================

_SCRIPT_FAMILY = {
    "LATIN": "LATIN",
    "CYRILLIC": "CYRILLIC",
    "GREEK": "GREEK",
    "HANGUL": "HANGUL",
    "CJK": "CJK",
    "HIRAGANA": "CJK",
    "KATAKANA": "CJK",
    "ARABIC": "ARABIC",
    "HEBREW": "HEBREW",
    "DEVANAGARI": "DEVANAGARI",
    "THAI": "THAI",
    "ARMENIAN": "ARMENIAN",
    "GEORGIAN": "GEORGIAN",
}


def script_profile(text: Optional[str]) -> tuple[Optional[str], float, int]:
    """(dominant script family, its share of alphabetic chars, alpha count).

    Non-alphabetic characters are ignored, so digits/punctuation/whitespace
    never dilute the signal. Returns (None, 0.0, 0) for text with no letters.
    """
    counts: collections.Counter = collections.Counter()
    for ch in text or "":
        if not ch.isalpha():
            continue
        try:
            name = unicodedata.name(ch)
        except ValueError:
            continue
        fam = _SCRIPT_FAMILY.get(name.split()[0])
        if fam:
            counts[fam] += 1
    total = sum(counts.values())
    if not total:
        return None, 0.0, 0
    fam, n = counts.most_common(1)[0]
    return fam, n / total, total


_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "these", "those",
    "into", "onto", "over", "under", "between", "within", "during", "after",
    "before", "using", "used", "based", "study", "studies", "effect",
    "effects", "role", "than", "then", "when", "what", "which", "while",
    "does", "doe", "are", "was", "were", "have", "has", "had", "can", "may",
    "not", "but", "its", "their", "our", "your", "his", "her", "they",
    "toward", "towards", "across", "about", "also", "more", "most", "such",
    "here", "there", "some", "both", "each", "other", "others", "however",
    "paper", "article", "abstract", "introduction", "results", "conclusion",
    "conclusions", "method", "methods", "data", "analysis", "new", "novel",
}


_WORD_RX = re.compile(r"[^\W\d_](?:[^\W\d_]|-)*", re.UNICODE)


def content_words(text: Optional[str]) -> set[str]:
    """Lower-cased alphabetic tokens of length >= 4, stopwords removed.

    Unicode-aware on purpose: a Cyrillic/Hangul/CJK title must yield tokens so
    that the title-vs-synopsis overlap rule can see that the overlap is ZERO
    (an ASCII-only tokenizer would return an empty set and silently skip the
    check — exactly the 417e4953 case).
    """
    if not text:
        return set()
    out = set()
    for tok in _WORD_RX.findall(text.lower()):
        t = tok.strip("-")
        if len(t) >= 4 and t not in _STOPWORDS:
            out.add(t)
    return out


def _stems(words: Iterable[str], n: int = 5) -> set[str]:
    """Crude prefix stemming — enough to make perception/perceptual and
    encode/encoding compare equal without pulling in a stemmer dependency."""
    return {w[:n] for w in words if len(w) >= 4}


def _stem_hits(a: Iterable[str], b: Iterable[str]) -> set[str]:
    return _stems(a) & _stems(b)


def _stem_cov(need: set[str], have: Iterable[str]) -> float:
    ns = _stems(need)
    if not ns:
        return 0.0
    return len(ns & _stems(have)) / len(ns)


def alnum_ratio(text: str) -> float:
    stripped = [c for c in text if not c.isspace()]
    if not stripped:
        return 0.0
    return sum(1 for c in stripped if c.isalnum()) / len(stripped)


# ------------------------------------------------------------------ DOI year

_DOI_YEAR_RULES = (
    # bioRxiv / medRxiv: 10.1101/2020.05.12.091934
    ("biorxiv_date", re.compile(r"^10\.1101/((?:19|20)\d{2})\.\d{2}\.\d{2}"),
     lambda m: int(m.group(1))),
    # arXiv DOI (10.48550/arXiv.2301.01234) or synthetic id (arxiv:2301.01234)
    ("arxiv_id", re.compile(r"(?:^10\.48550/arxiv\.|^arxiv:)(\d{2})(\d{2})\.\d{4,5}"),
     lambda m: 2000 + int(m.group(1)) if 7 <= int(m.group(1)) <= 99 else None),
    # Journal of Vision: 10.1167/<vol>.<iss>.<art>  or 10.1167/jov.<vol>...
    # JoV volume 1 = 2001, so year = 2000 + volume (verified against 15 corpus
    # rows: 10.1167/10.10.6→2010 … 10.1167/16.12.1112→2016).
    ("jov_volume", re.compile(r"^10\.1167/(?:jov\.)?(\d{1,2})\.\d"),
     lambda m: 2000 + int(m.group(1)) if 1 <= int(m.group(1)) <= 40 else None),
)


def doi_implied_year(doi: Optional[str]) -> tuple[Optional[int], Optional[str]]:
    """Year decodable from the DOI string itself, with the rule name.

    Only *confidently* decodable patterns are used. A DOI that merely happens
    to contain four digits (e.g. 10.1073/pnas.2003383117) is NOT decoded — that
    would mint false positives at scale.
    """
    if not doi:
        return None, None
    d = doi.strip().lower()
    for name, rx, fn in _DOI_YEAR_RULES:
        m = rx.search(d)
        if m:
            y = fn(m)
            if y:
                return y, name
    return None, None


def doi_prefix(doi: Optional[str]) -> Optional[str]:
    if not doi:
        return None
    m = re.match(r"^(10\.\d{3,9})/", doi.strip().lower())
    return m.group(1) if m else None


# ------------------------------------------------------------- title shapes

_FILENAME_RX = re.compile(
    r"\.(pdf|docx?|pptx?|xlsx?|txt|rtf|epub|djvu|zip|tex|bib|csv|json|html?)\s*$",
    re.IGNORECASE)
_PATHY_RX = re.compile(r"(^|[^A-Za-z])[A-Za-z]:\\|/{1,2}[\w\-.]+/[\w\-.]+")
_SCANNAME_RX = re.compile(r"^[\w\-]+[_\-]\d{4}[_\-][\w\-]+$")
_IDENTIFIER_RX = re.compile(
    r"^\s*(https?://|www\.|doi\s*:|10\.\d{3,9}/|arxiv\s*:|isbn\b|pmid\b)",
    re.IGNORECASE)
# trailing 6-8 hex disambiguation token, e.g. "... visual working memory cedba4"
_HEXTAIL_RX = re.compile(r"\s[0-9a-f]{6,8}\s*$")

_STRONG_VENUE_RX = re.compile(
    r"\b(journal|proceedings|transactions|annals|bulletin|acta|archiv(es|)"
    r"|conference|symposium|quarterly|gazette|vestnik|revue|zeitschrift"
    r"|frontiers in|trends in|advances in|annual review)\b",
    re.IGNORECASE)

_BOILERPLATE_RX = (
    re.compile(r"^\s*(no\s+abstract|abstract\s+(is\s+)?(not\s+available|unavailable)"
               r"|not\s+available|n/?a|none|null|-+|\.+)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(this\s+article\s+is\s+protected\s+by\s+copyright"
               r"|all\s+rights\s+reserved"
               r"|downloaded\s+from"
               r"|©|\(c\)\s*\d{4}"
               r"|the\s+publisher'?s?\s+final\s+edited\s+version"
               r"|abstract\s*$)", re.IGNORECASE),
)


# =========================================================================
# rule engine  (pure — every rule takes a plain dict, no DB)
# =========================================================================

def _f(code: str, severity: str, message: str, **evidence) -> dict:
    return {"code": code, "severity": severity, "message": message,
            "evidence": evidence}


def check_record(rec: dict, ctx: Optional[dict] = None) -> list[dict]:
    """Return findings for one record. `ctx` carries corpus-derived stats:

        ctx["doi_counts"]        : {normalised doi -> n canonical_ids}
        ctx["title_counts"]      : {title_norm -> n canonical_ids}
        ctx["venue_strings"]     : {venue string (casefold) -> n rows}
        ctx["prefix_venue"]      : {doi prefix -> (dominant venue, share, n)}
        ctx["current_year"]      : int
        ctx["recompute_id"]      : callable(doi,title,year) -> id  (optional)

    All keys are optional; a missing key simply disables the rules that need it.
    """
    ctx = ctx or {}
    out: list[dict] = []
    cur_year = ctx.get("current_year") or datetime.now(KST).year

    title = (rec.get("title") or "")
    title_s = title.strip()
    venue = (rec.get("venue") or "")
    venue_s = venue.strip()
    abstract = (rec.get("abstract") or "")
    abstract_s = abstract.strip()
    year = rec.get("year")
    doi = (rec.get("doi") or "").strip() or None

    # ---------------------------------------------------------- title shape
    letters = sum(1 for c in title_s if c.isalpha())
    if not title_s:
        out.append(_f("TITLE_MISSING", "high",
                      "title is NULL or blank", title=title_s))
    else:
        # Shape rules run FIRST and are mutually exclusive with the
        # short-title rule: "10.1038/nn.4238" is an identifier, not a short
        # title, and "?? ---- ****" is punctuation soup, not a short title.
        shaped = False
        _pat = ("file_extension" if _FILENAME_RX.search(title_s)
                else "path_or_url" if _PATHY_RX.search(title_s)
                else "scan_name" if _SCANNAME_RX.match(title_s) else None)
        if _pat:
            out.append(_f("TITLE_IS_FILENAME", "high",
                          "title looks like a filename / path / embedded URL "
                          "or markup, not a title",
                          title=title_s[:160], pattern=_pat))
            shaped = True
        if _IDENTIFIER_RX.match(title_s):
            out.append(_f("TITLE_IS_IDENTIFIER", "high",
                          "title is a DOI/URL/identifier, not a title",
                          title=title_s))
            shaped = True
        ar = alnum_ratio(title_s)
        if ar < 0.55:
            out.append(_f("TITLE_MOSTLY_PUNCT", "high",
                          "title is mostly punctuation/symbols",
                          title=title_s, alnum_ratio=round(ar, 3)))
            shaped = True
        if _HEXTAIL_RX.search(title_s):
            # Systematic ingest artifact, not per-row corruption (1,114 / 9,015
            # rows, measured 2026-07-22): a 6-8 hex disambiguation token was
            # appended to the stored title, and several are also head-truncated
            # ("nt The extent of the vertical meridian 16a614"). render.py
            # prints `title` verbatim into the APA card, so this IS
            # researcher-visible — but it is a cosmetic/derivation defect, not
            # a wrong-paper defect, so it stays `low` and never quarantines.
            out.append(_f("TITLE_HEX_SUFFIX", "low",
                          "title carries a trailing hex disambiguation token "
                          "(ingest artifact; would be rendered verbatim in the "
                          "APA card)", title=title_s[-60:]))
        if not shaped and (len(title_s) < 8 or letters < 4):
            out.append(_f("TITLE_MISSING", "high",
                          "title is too short to be a paper title",
                          title=title_s, chars=len(title_s), letters=letters))

    # -------------------------------------------------- script coherence
    t_fam, t_share, t_n = script_profile(title_s)
    a_fam, a_share, a_n = script_profile(abstract_s)
    v_fam, v_share, v_n = script_profile(venue_s)

    if t_fam and a_fam and t_n >= 6 and a_n >= 120 \
            and t_share >= 0.6 and a_share >= 0.6 and t_fam != a_fam:
        # The 417e4953 signature: a Cyrillic title carrying an English abstract
        # *under an English venue* — the title is the odd column out, so the
        # bibliography was spliced onto someone else's body.
        #
        # If the venue agrees with the TITLE instead, this is far more likely a
        # genuine foreign-language article whose abstract was stored in the
        # other language (measured on prod: two 'Advances in Psychological
        # Science' rows). Still worth reporting — the synopsis and embedding
        # for such a row are unreliable — but not a splice, so `medium`.
        odd_one_out = (v_fam is not None and v_fam == a_fam)
        out.append(_f(
            "TITLE_SCRIPT_MISMATCH", "critical" if odd_one_out else "medium",
            f"title script ({t_fam}) does not match abstract script ({a_fam})"
            + (" while the venue agrees with the abstract — the title column "
               "belongs to a different work"
               if odd_one_out else
               " (venue does not corroborate; may be a foreign-language "
               "article rather than a spliced record)"),
            title=title_s[:120], title_script=t_fam,
            title_script_share=round(t_share, 2),
            abstract_script=a_fam, abstract_script_share=round(a_share, 2),
            venue_script=v_fam, abstract_head=abstract_s[:160]))

    if t_fam and v_fam and t_n >= 6 and v_n >= 6 \
            and t_share >= 0.6 and v_share >= 0.6 and t_fam != v_fam:
        out.append(_f(
            "TITLE_VENUE_SCRIPT_MISMATCH", "medium",
            f"title script ({t_fam}) does not match venue script ({v_fam})",
            title=title_s[:120], venue=venue_s[:120],
            title_script=t_fam, venue_script=v_fam))

    # ------------------------------------------------ title / venue swapped
    if title_s and _STRONG_VENUE_RX.search(title_s) and len(title_s.split()) <= 8:
        venue_like_venue = bool(venue_s) and bool(_STRONG_VENUE_RX.search(venue_s))
        if not venue_like_venue:
            out.append(_f(
                "TITLE_VENUE_SWAPPED", "high",
                "title reads as a journal/proceedings name while venue does not",
                title=title_s, venue=venue_s))
    vs = ctx.get("venue_strings") or {}
    if title_s and vs.get(title_s.casefold(), 0) >= 3:
        out.append(_f(
            "TITLE_VENUE_SWAPPED", "high",
            "this exact string is used as the VENUE by >=3 other records",
            title=title_s, used_as_venue_by=vs.get(title_s.casefold())))

    # ------------------------------------------------------------- year
    if year is None:
        out.append(_f("YEAR_MISSING", "medium", "year is NULL"))
    else:
        try:
            yi = int(year)
        except (TypeError, ValueError):
            out.append(_f("YEAR_IMPLAUSIBLE", "high",
                          "year is not an integer", year=year))
            yi = None
        if yi is not None and (yi < 1800 or yi > cur_year + 1):
            out.append(_f("YEAR_IMPLAUSIBLE", "high",
                          f"year outside [1800, {cur_year + 1}]", year=yi))
        if yi is not None:
            implied, rule = doi_implied_year(doi)
            if implied and abs(implied - yi) > 1:
                # ASYMMETRIC on purpose. A record cannot predate its own DOI
                # registration, so stored < implied is a hard contradiction
                # (the 417e4953 signature: year 2010 on a 2019 JoV volume).
                # stored > implied is usually benign — a bioRxiv preprint DOI
                # carrying the later journal year. Exception: the JoV volume
                # number fixes the year exactly, so both directions are hard.
                exact = rule == "jov_volume"
                if yi < implied - 1:
                    sev, why = "high", ("a record cannot predate its own DOI "
                                        "registration")
                elif exact:
                    sev, why = "high", "the volume number fixes the year exactly"
                else:
                    sev, why = "medium", ("plausible preprint-DOI / journal-year "
                                          "lag, but worth an eyeball")
                out.append(_f(
                    "YEAR_DOI_MISMATCH", sev,
                    f"stored year {yi} contradicts the year implied by the DOI "
                    f"({implied}, rule={rule}) — {why}",
                    year=yi, doi=doi, doi_implied_year=implied, rule=rule))

    # ---------------------------------------------------------- abstract
    # NOTE severity: a missing abstract is the corpus NORM here (6,616 / 9,015
    # rows, measured 2026-07-22 — the classics were ingested from PDFs without
    # one) and 0 of them sit in a live queue, because the queue builder needs
    # an embedding. So it is reported at `low`: real signal, never a
    # quarantine reason on its own.
    if not abstract_s:
        out.append(_f("ABSTRACT_MISSING", "low", "abstract is NULL or blank"))
    elif len(abstract_s) < 100:
        out.append(_f("ABSTRACT_MISSING", "low",
                      "abstract shorter than 100 chars",
                      chars=len(abstract_s), abstract=abstract_s[:120]))
    else:
        for rx in _BOILERPLATE_RX:
            if rx.match(abstract_s):
                out.append(_f("ABSTRACT_BOILERPLATE", "medium",
                              "abstract is publisher boilerplate, not content",
                              abstract_head=abstract_s[:160]))
                break

    # ------------------------------------------------- synopsis coherence
    syn = rec.get("synopsis")
    if syn and title_s:
        syn_terms = content_words(" ".join(filter(None, [
            syn.get("core_question") or "",
            " ".join(_as_str_list(syn.get("connecting_signals"))),
            " ".join(_as_str_list(syn.get("key_findings"))),
        ])))
        t_terms = content_words(title_s)
        a_terms = content_words(abstract_s)
        # >=2 title content words so a one-word title cannot trip it, and a
        # high abstract coverage so the synopsis is demonstrably grounded in
        # THIS row's body — the disagreement is then localised to the title.
        #
        # The title is compared against the abstract AS WELL AS the synopsis,
        # under 5-char prefix stemming. Both guards were added after measuring
        # false positives on prod (2026-07-22):
        #   * exact-token matching called "A new law of human perception" a
        #     mismatch because the synopsis says "perceptual", not "perception";
        #   * synopsis-only matching called "The frugal brain" a mismatch even
        #     though its own abstract shares terms with the title.
        # A genuine Frankenstein row disagrees with BOTH.
        if syn_terms and len(t_terms) >= 2 and a_terms:
            a_cov = _stem_cov(syn_terms, a_terms)
            t_hits_syn = _stem_hits(t_terms, syn_terms)
            t_hits_abs = _stem_hits(t_terms, a_terms)
            if not t_hits_syn and not t_hits_abs and a_cov >= 0.25:
                out.append(_f(
                    "SYNOPSIS_TITLE_MISMATCH", "high",
                    "the synopsis is grounded in the abstract, but the title "
                    "shares ZERO content stems with either the synopsis or the "
                    "abstract — the title describes a different work",
                    title=title_s[:120],
                    core_question=(syn.get("core_question") or "")[:160],
                    abstract_coverage=round(a_cov, 2),
                    title_terms=sorted(t_terms)[:10],
                    synopsis_terms=sorted(syn_terms)[:12]))

    # ------------------------------------------------ venue vs DOI registrant
    pv = ctx.get("prefix_venue") or {}
    pfx = doi_prefix(doi)
    if pfx and venue_s and pfx in pv:
        dom_venue, share, n_rows = pv[pfx]
        if share >= 0.70 and n_rows >= 20:
            dom_terms = content_words(dom_venue)
            row_terms = content_words(venue_s)
            v_fam2, _, _ = script_profile(venue_s)
            d_fam2, _, _ = script_profile(dom_venue)
            disjoint = bool(dom_terms) and not (dom_terms & row_terms)
            if disjoint and (v_fam2 != d_fam2 or not row_terms):
                out.append(_f(
                    "VENUE_DOI_MISMATCH", "high",
                    f"venue is unrelated to the dominant venue for DOI prefix "
                    f"{pfx} ({dom_venue!r}, {share:.0%} of {n_rows} rows)",
                    doi=doi, venue=venue_s[:120],
                    prefix=pfx, dominant_venue=dom_venue,
                    dominant_share=round(share, 2), prefix_rows=n_rows))

    # ------------------------------------------------------------ identity
    recompute = ctx.get("recompute_id")
    stored_id = rec.get("canonical_id")
    if recompute and stored_id:
        try:
            expect = recompute(doi, rec.get("title"), rec.get("year"))
            title_key = recompute(None, rec.get("title"), rec.get("year"))
        except Exception:
            expect = title_key = None
        if expect and expect != stored_id:
            if doi and title_key == stored_id:
                # BENIGN, and the dominant case in this corpus (206/9015 rows,
                # measured 2026-07-22): the row was keyed on title+year while
                # it had no DOI, then a later backfill (P26 OpenAlex /
                # backfill_abstracts) added the DOI without re-keying. The
                # bibliography is coherent; only the key is stale. Re-keying
                # would orphan canonical_id-keyed archive_responses, so this is
                # informational ONLY and must never reach quarantine severity.
                out.append(_f(
                    "ID_KEY_STALE", "low",
                    "canonical_id is title-keyed but the row now carries a DOI "
                    "(benign post-hoc DOI backfill; re-keying would orphan "
                    "archive_responses — do not 'fix')",
                    stored=stored_id, doi_keyed_would_be=expect, doi=doi))
            else:
                out.append(_f(
                    "ID_DRIFT", "critical",
                    "canonical_id matches neither the DOI-key nor the "
                    "title-key recomputed from this row — the identity fields "
                    "were overwritten after the row was keyed",
                    stored=stored_id, recomputed=expect,
                    title_key=title_key, doi=doi,
                    title=title_s[:120], year=year))

    dc = ctx.get("doi_counts") or {}
    if doi and dc.get(doi.casefold(), 0) >= 2:
        # Medium, not high: a shared DOI means one of the twins is redundant,
        # not that either row is *incoherent*. Twin collapse is already owned
        # by P33 `_common.same_work` / `dedup_same_work` on the build path;
        # quarantining both halves of a twin would drop a good paper.
        out.append(_f("DOI_DUPLICATE", "medium",
                      "the same DOI is carried by >=2 canonical_ids "
                      "(twin — collapsed by P33 same_work on the build path, "
                      "not a coherence defect in itself)",
                      doi=doi, n_canonical_ids=dc.get(doi.casefold())))

    tc = ctx.get("title_counts") or {}
    tn = (rec.get("title_norm") or "").strip()
    if tn and tc.get(tn, 0) >= 2:
        out.append(_f("TITLE_DUPLICATE", "low",
                      "the same normalised title is carried by >=2 "
                      "canonical_ids (preprint/published twin or true dup)",
                      title_norm=tn[:80], n_canonical_ids=tc.get(tn)))

    return out


def _as_str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return [v]
    if isinstance(v, dict):
        v = list(v.values())
    if not isinstance(v, (list, tuple)):
        return [str(v)]
    out = []
    for item in v:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            out.extend(str(x) for x in item.values() if isinstance(x, str))
    return out


def worst_severity(findings: Iterable[dict]) -> Optional[str]:
    sevs = [f["severity"] for f in findings]
    if not sevs:
        return None
    return sorted(sevs, key=lambda s: SEVERITY_ORDER.get(s, 9))[0]


# =========================================================================
# corpus context
# =========================================================================

def build_context(papers: list[dict]) -> dict:
    doi_counts: collections.Counter = collections.Counter()
    title_counts: collections.Counter = collections.Counter()
    venue_strings: collections.Counter = collections.Counter()
    prefix_venues: dict[str, collections.Counter] = {}

    for p in papers:
        d = (p.get("doi") or "").strip()
        if d:
            doi_counts[d.casefold()] += 1
        tn = (p.get("title_norm") or "").strip()
        if tn:
            title_counts[tn] += 1
        v = (p.get("venue") or "").strip()
        if v:
            venue_strings[v.casefold()] += 1
        pfx = doi_prefix(d)
        if pfx and v:
            prefix_venues.setdefault(pfx, collections.Counter())[v.casefold()] += 1

    prefix_venue: dict[str, tuple[str, float, int]] = {}
    for pfx, counter in prefix_venues.items():
        n = sum(counter.values())
        venue_cf, cnt = counter.most_common(1)[0]
        prefix_venue[pfx] = (venue_cf, cnt / n, n)

    ctx: dict = {
        "doi_counts": dict(doi_counts),
        "title_counts": dict(title_counts),
        "venue_strings": dict(venue_strings),
        "prefix_venue": prefix_venue,
        "current_year": datetime.now(KST).year,
    }
    try:
        from _common import canonical_id  # type: ignore
        ctx["recompute_id"] = canonical_id
    except Exception:  # pragma: no cover - _common always present in repo
        pass
    return ctx


# =========================================================================
# DB loading (SELECT ONLY)
# =========================================================================

def load_from_db(limit: Optional[int] = None, only: Optional[str] = None) -> dict:
    import _db  # pipeline/_db.py
    _db.load_env()
    schema = _db.ledger_schema()

    where = ""
    if only:
        safe = re.sub(r"[^0-9a-fA-F]", "", only)
        if not safe:
            raise SystemExit("--only must be a hex canonical_id (prefix ok)")
        where = f"WHERE p.canonical_id LIKE '{safe}%'"
    lim = f"LIMIT {int(limit)}" if limit else ""

    papers = _db.query_json(f"""
        SELECT p.canonical_id, p.doi, p.title, p.title_norm, p.venue, p.year,
               p.pub_date, p.authors_json, p.abstract, p.source, p.is_preprint,
               p.first_seen_at, p.last_updated_at
          FROM {schema}.archive_papers p
        {where}
        ORDER BY p.canonical_id
        {lim}
    """)

    synopses = _db.query_json(f"""
        SELECT canonical_id, core_question, connecting_signals, key_findings,
               out_of_scope_note, review_status
          FROM {schema}.archive_paper_synopses
    """)
    queues = _db.query_json(f"""
        SELECT researcher_id, canonical_id, chunk, tier, composite,
               rank_in_chunk, builder
          FROM {schema}.archive_researcher_queues
    """)
    responses = _db.query_json(f"""
        SELECT researcher_id, canonical_id, choice
          FROM {schema}.archive_responses
    """)
    digests = _db.query_json(f"""
        SELECT researcher_id, canonical_id, week_iso, response_choice
          FROM {schema}.archive_weekly_digests
    """)
    return {"schema": schema, "papers": papers, "synopses": synopses,
            "queues": queues, "responses": responses, "digests": digests}


def attach_context(bundle: dict) -> list[dict]:
    """Fold synopsis / queue / response / digest context onto each paper row."""
    syn_by = {s["canonical_id"]: s for s in bundle.get("synopses", [])}
    q_by: dict[str, list] = {}
    for q in bundle.get("queues", []):
        q_by.setdefault(q["canonical_id"], []).append(q)
    r_by: dict[str, list] = {}
    for r in bundle.get("responses", []):
        r_by.setdefault(r["canonical_id"], []).append(r)
    d_by: dict[str, list] = {}
    for d in bundle.get("digests", []):
        d_by.setdefault(d["canonical_id"], []).append(d)

    recs = []
    for p in bundle.get("papers", []):
        cid = p["canonical_id"]
        rec = dict(p)
        rec["synopsis"] = syn_by.get(cid)
        rec["_queues"] = q_by.get(cid, [])
        rec["_responses"] = r_by.get(cid, [])
        rec["_digests"] = d_by.get(cid, [])
        recs.append(rec)
    return recs


def queue_summary(rec: dict) -> dict:
    qs = rec.get("_queues") or []
    tiers = [q.get("tier") for q in qs]
    best = sorted(tiers, key=lambda t: TIER_ORDER.get(t, 9))[0] if tiers else None
    rids = sorted({q.get("researcher_id") for q in qs if q.get("researcher_id")})
    chunks = sorted({q.get("chunk") for q in qs if q.get("chunk")})
    responded = {r.get("researcher_id") for r in (rec.get("_responses") or [])}
    sent = {d.get("researcher_id") for d in (rec.get("_digests") or [])}
    eligible = [r for r in rids if r not in responded and r not in sent]
    return {
        "n_queues": len(qs),
        "researchers": rids,
        "best_tier": best,
        "chunks": chunks,
        "n_responses": len(rec.get("_responses") or []),
        "n_digests": len(rec.get("_digests") or []),
        "eligible_researchers": eligible,
        "n_eligible": len(eligible),
    }


# =========================================================================
# scan / rank / report
# =========================================================================

def scan(records: list[dict], ctx: dict, min_severity: str = "low") -> list[dict]:
    cutoff = SEVERITY_ORDER.get(min_severity, 3)
    flagged = []
    for rec in records:
        findings = check_record(rec, ctx)
        findings = [f for f in findings
                    if SEVERITY_ORDER.get(f["severity"], 9) <= cutoff]
        if not findings:
            continue
        qs = queue_summary(rec)
        sev = worst_severity(findings)
        flagged.append({
            "canonical_id": rec.get("canonical_id"),
            "severity": sev,
            "codes": sorted({f["code"] for f in findings}),
            "findings": findings,
            "title": rec.get("title"),
            "venue": rec.get("venue"),
            "year": rec.get("year"),
            "doi": rec.get("doi"),
            "source": rec.get("source"),
            "queue": qs,
            "quarantine_recommended": SEVERITY_ORDER.get(sev, 9) <= 1,
        })
    flagged.sort(key=_rank_key)
    return flagged


def _rank_key(row: dict):
    q = row["queue"]
    return (
        0 if q["n_queues"] > 0 else 1,          # live-queue exposure first
        SEVERITY_ORDER.get(row["severity"], 9),
        TIER_ORDER.get(q["best_tier"], 9),
        -q["n_eligible"],
        -q["n_queues"],
        row["canonical_id"] or "",
    )


def render_report(flagged: list[dict], n_scanned: int, *, verbose: bool = False,
                  top: int = 40, queued_only: bool = False) -> str:
    rows = [r for r in flagged if r["queue"]["n_queues"] > 0] if queued_only else flagged
    L = []
    L.append("=" * 78)
    L.append("archive_papers coherence scan — DRY RUN (no writes)")
    L.append(f"detector={DETECTOR}   at={kst_iso()}")
    L.append("=" * 78)
    L.append(f"scanned rows           : {n_scanned}")
    L.append(f"rows with >=1 finding  : {len(flagged)}")
    in_q = [r for r in flagged if r["queue"]["n_queues"] > 0]
    L.append(f"  ...of which in a LIVE QUEUE : {len(in_q)} "
             f"({sum(r['queue']['n_queues'] for r in in_q)} queue rows)")
    rec_q = [r for r in flagged if r["quarantine_recommended"]]
    rec_q_live = [r for r in rec_q if r["queue"]["n_queues"] > 0]
    L.append(f"quarantine-recommended (severity high/critical) : {len(rec_q)}")
    L.append(f"  ...of which in a LIVE QUEUE : {len(rec_q_live)} "
             f"({sum(r['queue']['n_queues'] for r in rec_q_live)} queue rows, "
             f"{sum(r['queue']['n_eligible'] for r in rec_q_live)} still eligible "
             f"for a refill)")
    L.append("")

    by_sev = collections.Counter(r["severity"] for r in flagged)
    L.append("by severity : " + ", ".join(
        f"{s}={by_sev[s]}" for s in ("critical", "high", "medium", "low")
        if by_sev.get(s)))
    by_code: collections.Counter = collections.Counter()
    for r in flagged:
        for c in r["codes"]:
            by_code[c] += 1
    L.append("by rule     :")
    for code, n in by_code.most_common():
        live = sum(1 for r in flagged
                   if code in r["codes"] and r["queue"]["n_queues"] > 0)
        L.append(f"    {code:<28} {n:>5}   (in live queues: {live})")
    L.append("")

    L.append("-" * 78)
    L.append(f"TOP {min(top, len(rows))} (ranked by live-queue exposure, then severity)")
    L.append("-" * 78)
    for r in rows[:top]:
        q = r["queue"]
        mark = "!!" if r["quarantine_recommended"] else "  "
        qtxt = (f"queues={q['n_queues']}({','.join(q['researchers'])}) "
                f"tier={q['best_tier']} eligible={q['n_eligible']}"
                if q["n_queues"] else "queues=0")
        L.append(f"{mark} {r['canonical_id'][:16]}  {r['severity']:<8} {qtxt}")
        L.append(f"     title : {(r['title'] or '<NULL>')[:96]}")
        L.append(f"     doi   : {r['doi'] or '<NULL>'}   venue: "
                 f"{(r['venue'] or '<NULL>')[:48]}   year: {r['year']}")
        L.append(f"     codes : {', '.join(r['codes'])}")
        if verbose:
            for f in r["findings"]:
                L.append(f"       - [{f['severity']}] {f['code']}: {f['message']}")
                for k, v in f["evidence"].items():
                    L.append(f"           {k} = {v!r}"[:200])
        L.append("")
    if len(rows) > top:
        L.append(f"... {len(rows) - top} more (see the JSONL)")
    return "\n".join(L)


# =========================================================================
# --apply  (OPERATOR ONLY; INSERT-only into a sidecar table)
# =========================================================================

_QUARANTINE_TABLE = "archive_paper_quarantine"

_FORBIDDEN_SQL = re.compile(
    r"\b(delete\s+from|drop\s+(table|schema|database)|truncate|alter\s+table)\b"
    # a bare `UPDATE <table> SET`; `... ON CONFLICT DO UPDATE SET` is allowed
    r"|(?<!do\s)\bupdate\s+[\w.\"]+\s+set\b", re.IGNORECASE)
_FORBIDDEN_TARGETS = re.compile(
    r"\b(csnl_research|archive_responses|archive_papers\b(?!_quarantine)"
    r"|archive_weekly_digests|archive_researcher_queues"
    r"|archive_paper_synopses)\b", re.IGNORECASE)


def _assert_safe_sql(sql: str) -> None:
    """Refuse to run anything that could mutate real data.

    The only statements this script may execute are: CREATE TABLE IF NOT
    EXISTS / CREATE INDEX IF NOT EXISTS / COMMENT ON, and INSERT ... ON
    CONFLICT into the quarantine sidecar. Anything else — or any mention of a
    protected table — aborts before a connection is opened.
    """
    # Strip line comments and single-quoted literals first: a keyword inside a
    # string (the table COMMENT literally says "Reverse with DROP TABLE") is
    # inert, and must not trip the guard.
    body = re.sub(r"--[^\n]*", " ", sql)
    body = re.sub(r"'(?:[^']|'')*'", " '' ", body)
    if _FORBIDDEN_SQL.search(body):
        raise SystemExit(f"REFUSING unsafe SQL (mutating verb): {body[:160]!r}")
    if _FORBIDDEN_TARGETS.search(body):
        raise SystemExit(f"REFUSING unsafe SQL (protected table): {body[:160]!r}")
    head = body.strip().lower()
    ok = (head.startswith("create table if not exists")
          or head.startswith("create index if not exists")
          or head.startswith("comment on")
          or head.startswith("insert into"))
    if not ok:
        raise SystemExit(f"REFUSING unsafe SQL (verb not allowlisted): {body[:160]!r}")
    if head.startswith("insert into") and _QUARANTINE_TABLE not in head:
        raise SystemExit("REFUSING INSERT into a table other than "
                         f"{_QUARANTINE_TABLE}")


def quarantine_ddl(schema: str) -> list[str]:
    t = f"{schema}.{_QUARANTINE_TABLE}"
    return [
        f"""CREATE TABLE IF NOT EXISTS {t}(
  canonical_id   TEXT PRIMARY KEY,
  severity       TEXT NOT NULL,
  codes          TEXT NOT NULL,
  evidence_json  JSONB NOT NULL,
  in_live_queue  INTEGER NOT NULL DEFAULT 0,
  n_eligible     INTEGER NOT NULL DEFAULT 0,
  detected_by    TEXT NOT NULL,
  detected_at    TEXT NOT NULL,
  released_at    TEXT,
  note           TEXT
)""",
        f"CREATE INDEX IF NOT EXISTS ix_{_QUARANTINE_TABLE}_live "
        f"ON {t}(in_live_queue)",
        f"COMMENT ON TABLE {t} IS "
        f"'P34/S3: rows whose bibliographic record is incoherent "
        f"(quarantine_bad_records.py). Sidecar only — archive_papers is never "
        f"modified. Consumers exclude WHERE released_at IS NULL. "
        f"Reverse with DROP TABLE.'",
    ]


def apply_quarantine(flagged: list[dict], schema: str, *, dry: bool = True) -> int:
    rows = [r for r in flagged if r["quarantine_recommended"]]
    if not rows:
        print("nothing at severity high/critical — nothing to mark")
        return 0
    stmts = quarantine_ddl(schema)
    insert = (
        f"INSERT INTO {schema}.{_QUARANTINE_TABLE} "
        f"(canonical_id, severity, codes, evidence_json, in_live_queue, "
        f" n_eligible, detected_by, detected_at) "
        f"VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s) "
        f"ON CONFLICT (canonical_id) DO UPDATE SET "
        f"  severity      = EXCLUDED.severity, "
        f"  codes         = EXCLUDED.codes, "
        f"  evidence_json = EXCLUDED.evidence_json, "
        f"  in_live_queue = EXCLUDED.in_live_queue, "
        f"  n_eligible    = EXCLUDED.n_eligible, "
        f"  detected_by   = EXCLUDED.detected_by, "
        f"  detected_at   = EXCLUDED.detected_at"
    )
    for s in stmts:
        _assert_safe_sql(s)
    _assert_safe_sql(insert)

    now = kst_iso()
    params = [(
        r["canonical_id"], r["severity"], ",".join(r["codes"]),
        json.dumps(r["findings"], ensure_ascii=False),
        r["queue"]["n_queues"], r["queue"]["n_eligible"], DETECTOR, now,
    ) for r in rows]

    if dry:
        print("-- would execute (dry) --")
        for s in stmts:
            print(s + ";")
        print(f"-- then {len(params)} x:\n{insert};")
        return 0

    import _db
    _db.load_env()
    for s in stmts:
        _db.exec_sql(s + ";")
    n = _db.exec_many(insert + ";", params)
    print(f"marked {n} rows in {schema}.{_QUARANTINE_TABLE} "
          f"(reverse: DROP TABLE {schema}.{_QUARANTINE_TABLE};)")
    return n


# =========================================================================
# self-test  (offline; no DB, no network)
# =========================================================================

_FIXTURES = [
    # --- the record the batch06 review names (verbatim from prod) -----------
    {
        "_name": "417e4953 Frankenstein (review's example)",
        "rec": {
            "canonical_id": "417e4953f0c03eb30028183920dbdd35",
            "doi": "10.1167/19.13.21",
            "title": "Мултиплициране на „българщината”",
            "title_norm": "",
            "venue": "Journal of Vision",
            "year": 2010,
            "authors_json": ["Евгения Иванова"],
            "abstract": (
                "Perceptual decisions about current sensory input are biased "
                "toward input of the recent past-a phenomenon termed serial "
                "dependence. Serial dependence may serve to stabilize neural "
                "representations in the face of noise, but it is unclear "
                "whether feature-based attention modulates this bias in "
                "orientation judgements across trials."),
            "source": "archive",
            "synopsis": {
                "core_question": (
                    "Does focusing feature-based attention on a particular "
                    "feature of the previous stimulus modulate serial "
                    "dependence in orientation judgements?"),
                "connecting_signals": ["serial dependence", "feature-based attention"],
                "key_findings": ["Attention to the previous orientation increased serial dependence."],
            },
        },
        "expect": {"TITLE_SCRIPT_MISMATCH", "YEAR_DOI_MISMATCH",
                   "SYNOPSIS_TITLE_MISMATCH"},
        "expect_severity": "critical",
    },
    # --- the second Cyrillic row (PNAS DOI, Russian architecture journal) ---
    {
        "_name": "74dea6f4 Cyrillic title + PNAS DOI",
        "rec": {
            "canonical_id": "74dea6f44c77591d6f5594ae0f11c17e",
            "doi": "10.1073/pnas.2003383117",
            "title": "Церкви полуострова Карпасия, Кипр",
            "venue": "Академический  вестник УралНИИпроект РААСН",
            "year": 2012,
            "abstract": ("Humans and other animals adapt their behaviour to the "
                         "statistics of the environment, and this efficient "
                         "coding principle predicts perceptual biases across a "
                         "wide range of tasks and modalities in visual cortex."),
            "source": "archive",
        },
        "ctx": {"prefix_venue": {"10.1073": ("proceedings of the national academy of sciences", 0.95, 300)}},
        # title+venue agree (both Cyrillic) so the script rule alone stays
        # `medium`; the DOI-registrant rule is what makes this quarantinable.
        "expect": {"TITLE_SCRIPT_MISMATCH", "VENUE_DOI_MISMATCH"},
        "expect_severity": "high",
    },
    # --- clean control: must produce ZERO findings --------------------------
    {
        "_name": "clean control",
        "rec": {
            "canonical_id": "a" * 32,
            "doi": "10.1167/16.12.1112",
            "title": "A new law defining the relationship between perceptual bias and discrimination threshold",
            "title_norm": "anewlawdefining",
            "venue": "Journal of Vision",
            "year": 2016,
            "abstract": ("We derive a lawful relationship between perceptual bias "
                         "and discrimination threshold under an efficient coding "
                         "model of the sensory representation, and validate it "
                         "against psychophysical measurements of orientation "
                         "perception in human observers."),
            "source": "archive",
            "synopsis": {
                "core_question": "How are perceptual bias and discrimination threshold related?",
                "connecting_signals": ["efficient coding", "perceptual bias"],
                "key_findings": ["Bias is proportional to the derivative of the threshold."],
            },
        },
        "expect": set(),
        "expect_severity": None,
    },
    # --- title is a filename ------------------------------------------------
    {
        "_name": "filename title",
        "rec": {"canonical_id": "b" * 32, "doi": None,
                "title": "Fritsche_2017_serial_dependence.pdf",
                "venue": "Current Biology", "year": 2017,
                "abstract": "x" * 400},
        "expect": {"TITLE_IS_FILENAME"},
        "expect_severity": "high",
    },
    # --- title is a DOI -----------------------------------------------------
    {
        "_name": "DOI as title",
        "rec": {"canonical_id": "c" * 32, "doi": "10.1038/nn.4238",
                "title": "10.1038/nn.4238", "venue": "Nature Neuroscience",
                "year": 2016, "abstract": "y" * 400},
        "expect": {"TITLE_IS_IDENTIFIER"},
        "expect_severity": "high",
    },
    # --- punctuation soup ---------------------------------------------------
    {
        "_name": "punctuation title",
        "rec": {"canonical_id": "d" * 32, "doi": None,
                "title": "?? ---- **** ,,,, ;;;; ....",
                "venue": "Vision Research", "year": 2001,
                "abstract": "z" * 400},
        "expect": {"TITLE_MOSTLY_PUNCT"},
        "expect_severity": "high",
    },
    # --- title/venue swapped ------------------------------------------------
    {
        "_name": "title/venue swapped",
        "rec": {"canonical_id": "e" * 32, "doi": None,
                "title": "Journal of Experimental Psychology",
                "venue": "Serial dependence in visual perception",
                "year": 2014, "abstract": "w" * 400},
        "expect": {"TITLE_VENUE_SWAPPED"},
        "expect_severity": "high",
    },
    # --- missing year + boilerplate abstract --------------------------------
    {
        "_name": "no year + boilerplate abstract",
        "rec": {"canonical_id": "f" * 32, "doi": None,
                "title": "Adaptation and efficient coding in early vision",
                "venue": "Vision Research", "year": None,
                "abstract": "No abstract available"},
        "expect": {"YEAR_MISSING", "ABSTRACT_MISSING"},
        "expect_severity": "medium",
    },
    # --- implausible year + duplicate DOI -----------------------------------
    {
        "_name": "implausible year + duplicate DOI",
        "rec": {"canonical_id": "1" * 32, "doi": "10.1016/j.cub.2017.05.006",
                "title": "Opposite effects of recent history on perception and decision",
                "venue": "Current Biology", "year": 1492,
                "abstract": "q" * 400},
        "ctx": {"doi_counts": {"10.1016/j.cub.2017.05.006": 2}},
        "expect": {"YEAR_IMPLAUSIBLE", "DOI_DUPLICATE"},
        "expect_severity": "high",
    },
    # --- bioRxiv DOI year mismatch ------------------------------------------
    {
        "_name": "bioRxiv DOI year mismatch",
        "rec": {"canonical_id": "2" * 32, "doi": "10.1101/2021.03.15.435487",
                "title": "Cortical dynamics of working memory maintenance",
                "venue": "bioRxiv", "year": 2011,
                "abstract": "r" * 400},
        "expect": {"YEAR_DOI_MISMATCH"},
        "expect_severity": "high",
    },
    # --- identity drift -----------------------------------------------------
    {
        "_name": "canonical_id drift",
        "rec": {"canonical_id": "0" * 32, "doi": "10.1038/s41593-018-0104-6",
                "title": "Bayesian inference in the visual cortex",
                "venue": "Nature Neuroscience", "year": 2018,
                "abstract": "s" * 400},
        "ctx_recompute": True,
        "expect": {"ID_DRIFT"},
        "expect_severity": "critical",
    },
]


def self_test(verbose: bool = False) -> int:
    failures = 0
    for fx in _FIXTURES:
        ctx = dict(fx.get("ctx") or {})
        ctx.setdefault("current_year", 2026)
        if fx.get("ctx_recompute"):
            from _common import canonical_id
            ctx["recompute_id"] = canonical_id
        findings = check_record(fx["rec"], ctx)
        codes = {f["code"] for f in findings}
        sev = worst_severity(findings)
        want = fx["expect"]
        missing = want - codes
        ok = not missing and sev == fx["expect_severity"]
        if fx["expect"] == set():
            ok = not codes and sev is None
        status = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"[{status}] {fx['_name']}")
        if not ok or verbose:
            print(f"        expected codes >= {sorted(want)} severity={fx['expect_severity']}")
            print(f"        got      codes  = {sorted(codes)} severity={sev}")
            for f in findings:
                print(f"          - [{f['severity']}] {f['code']}: {f['message']}")
    # --- SQL guard regression -------------------------------------------
    allowed = quarantine_ddl("csnl_paper_rec") + [
        f"INSERT INTO csnl_paper_rec.{_QUARANTINE_TABLE} (canonical_id) "
        f"VALUES (%s) ON CONFLICT (canonical_id) DO UPDATE SET "
        f"severity = EXCLUDED.severity"]
    refused = [
        "DELETE FROM csnl_paper_rec.archive_papers WHERE canonical_id='x'",
        "UPDATE csnl_paper_rec.archive_papers SET title=NULL",
        "DROP TABLE csnl_paper_rec.archive_responses",
        "TRUNCATE csnl_paper_rec.archive_responses",
        "INSERT INTO csnl_paper_rec.archive_responses VALUES (1)",
        "INSERT INTO csnl_paper_rec.archive_researcher_queues VALUES (1)",
        "SELECT * FROM csnl_research.projects",
        "ALTER TABLE csnl_paper_rec.archive_papers ADD COLUMN q BOOLEAN",
    ]
    guard_fail = 0
    for s in allowed:
        try:
            _assert_safe_sql(s)
        except SystemExit as e:
            guard_fail += 1
            print(f"[FAIL] sql-guard wrongly refused: {s[:60]!r} ({e})")
    for s in refused:
        try:
            _assert_safe_sql(s)
            guard_fail += 1
            print(f"[FAIL] sql-guard LEAKED: {s[:60]!r}")
        except SystemExit:
            pass
    print(f"[{'PASS' if not guard_fail else 'FAIL'}] sql-guard "
          f"({len(allowed)} allowed / {len(refused)} refused)")
    failures += guard_fail

    print()
    print(f"self-test: {len(_FIXTURES) + 1 - failures}/{len(_FIXTURES) + 1} "
          f"checks passed")
    return 1 if failures else 0


# =========================================================================
# CLI
# =========================================================================

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Scan archive_papers for incoherent bibliographic records "
                    "(read-only by default).")
    ap.add_argument("--apply", action="store_true",
                    help="OPERATOR ONLY: mark high/critical rows in the "
                         "archive_paper_quarantine sidecar table (INSERT only; "
                         "archive_papers is never modified)")
    ap.add_argument("--yes-i-am-operator", action="store_true",
                    help="required confirmation for --apply")
    ap.add_argument("--min-severity", default="low",
                    choices=["critical", "high", "medium", "low"],
                    help="report findings at this severity or worse "
                         "(default: low = everything)")
    ap.add_argument("--only", help="scan a single canonical_id (hex prefix ok)")
    ap.add_argument("--limit", type=int, help="scan at most N papers")
    ap.add_argument("--queued-only", action="store_true",
                    help="report only rows that sit in a live queue")
    ap.add_argument("--top", type=int, default=40, help="rows to print (default 40)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print every finding's evidence")
    ap.add_argument("--json", dest="json_path",
                    default=str(_REPO_ROOT / "state" / "archive" /
                               "quarantine_report.jsonl"),
                    help="JSONL output path (gitignored by default)")
    ap.add_argument("--no-json", action="store_true", help="skip JSONL output")
    ap.add_argument("--self-test", action="store_true",
                    help="run the offline fixture suite (no DB) and exit")
    ap.add_argument("--print-ddl", action="store_true",
                    help="print the quarantine table DDL and exit")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test(verbose=args.verbose)

    if args.print_ddl:
        for s in quarantine_ddl("csnl_paper_rec"):
            print(s + ";")
        return 0

    if args.apply and not args.yes_i_am_operator:
        print("--apply requires --yes-i-am-operator (operator-run only; the "
              "agent must not run it).", file=sys.stderr)
        return 2

    bundle = load_from_db(limit=args.limit, only=args.only)
    records = attach_context(bundle)
    # Context is always built from the FULL corpus when scanning the full
    # corpus; with --only/--limit the corpus-derived rules (duplicate DOI,
    # venue-vs-prefix) would be computed from a slice, so re-load the light
    # columns for the whole table to keep them honest.
    if args.only or args.limit:
        import _db
        _db.load_env()
        full = _db.query_json(
            f"SELECT canonical_id, doi, title, title_norm, venue, year "
            f"FROM {bundle['schema']}.archive_papers")
        ctx = build_context(full)
    else:
        ctx = build_context(bundle["papers"])

    flagged = scan(records, ctx, min_severity=args.min_severity)
    print(render_report(flagged, len(records), verbose=args.verbose,
                        top=args.top, queued_only=args.queued_only))

    if not args.no_json:
        out = Path(args.json_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for r in flagged:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\nJSONL -> {out}  ({len(flagged)} rows)")

    if args.apply:
        apply_quarantine(flagged, bundle["schema"], dry=False)
    else:
        n_rec = sum(1 for r in flagged if r["quarantine_recommended"])
        print(f"\nDRY RUN — nothing written. {n_rec} rows would be marked by "
              f"--apply (operator only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
