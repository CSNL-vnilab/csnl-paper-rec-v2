#!/usr/bin/env python3
"""
scripts/archive/build_fingerprints.py — extract per-researcher scientific
fingerprint from `csnl_research.projects` text. Writes one JSON per
researcher at `state/archive/fingerprints/<INIT>.json`.

This is P19a (ship-with-cuts after codex adversarial review — see
docs/HARNESS-ALGORITHM-DESIGN.md). Implements Voice A Pass-A only:
lexicon-anchored multi-word noun phrases. Pass B (capitalized-NP regex)
and Pass C (bilingual unigrams/bigrams) are deferred until Pass-A
coverage proves insufficient.

P34 S9-fp adds a SURVEY channel: the fingerprint is seeded from the
researcher's OWN confirmed vocabulary in `archive_survey_keywords`
(keyword + operational_def) and `archive_survey_aims`
(domain/phenomenon/task/mechanism), joined by researcher_id = init (no
paper resolution). This is the anchored signal 5/7 fingerprints lacked —
they had ≤1 Pass-A phrase and fell through to Pass-B TF-IDF junk
('none'/'item'/'reference'). Survey phrases take precedence over / augment
the lexicon channel and, by entering the phrase list BEFORE the Pass-B
gate, suppress the junk salvage for any populated survey. Curated
lexicon-channel anchors are EXEMPT from the phrase cap, so a survey flood
AUGMENTS a rich fingerprint (JOP: 'channel capacity'/'Blahut-Arimoto')
rather than DISPLACING it. Ambiguous keywords never seed their bare
homonym term (the P24 landmine) — only multi-word phrases from their OWN
operational_def. Def-only mining is the protection: the batch10
`phrase in conflict` guard was a false safety net (never fired) and its
token-intersection replacement over-drops own-sense phrases on live data,
so it is removed — the definition-aware veto lives downstream in
recommend.py (see _build_survey_phrases).

Operator-run:
    ! python scripts/archive/build_fingerprints.py             # dry-run
    ! python scripts/archive/build_fingerprints.py --apply     # write JSON
    ! python scripts/archive/build_fingerprints.py JOP --apply # single researcher

The queue builder (`build_researcher_queue.py`) reads the fingerprint
when present and falls back to the legacy `_derive_dim_prefs()` when
absent — backward compat by absence.

No LLM. No network beyond what `_db.py` already does (csnl_research
read-only).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ARCHIVE   = _REPO_ROOT / "state" / "archive"
_LEXICON   = _ARCHIVE / "known_phrases.txt"
_IDF       = _ARCHIVE / "lexicon_idf.json"
_TAXONOMY  = _REPO_ROOT / "plugin" / "data" / "taxonomy.json"
_OUT_DIR   = _ARCHIVE / "fingerprints"

KST = timezone(timedelta(hours=9))

# Channel weights (Voice A §1.1)
_W_ANCHOR   = 1.0
_W_PARADIGM = 0.8
_W_CONTEXT  = 0.4

# Pass-A: lexicon-anchored multi-word phrases (primary path).
# Pass-B (P19b): TF-IDF salvage. Fires ONLY when Pass-A yields ≤ 3 phrases.
# Mitigates the cold-start asymmetry codex finding #2 (BHL/SMJ/SYJ got 0-1
# Pass-A phrases). Uses the same corpus IDF + a stoplist of generic
# scientific filler ("research", "analysis", etc.) that would otherwise
# top the TF-IDF ranking on sparse project text.

_PASSB_TRIGGER_THRESHOLD = 3   # if Pass-A yields ≤ N, fire Pass-B salvage
_PASSB_TOP_K            = 15  # take top-K TF-IDF terms from researcher text
_PASSB_MIN_IDF          = 4.0 # only rare terms; bge-m3 corpus had idf≈4.26 for "fmri"
_PASSB_MIN_LEN          = 4   # skip short tokens
_STOPLIST_EN = {
    "research", "researcher", "study", "studies", "analysis", "results",
    "approach", "method", "methods", "data", "model", "models", "experiment",
    "experiments", "experimental", "task", "tasks", "stimulus", "stimuli",
    "subject", "subjects", "participant", "participants", "condition",
    "conditions", "trial", "trials", "session", "sessions", "effect", "effects",
    "across", "between", "during", "while", "after", "before", "within",
    "different", "various", "several", "many", "more", "less", "however",
    "therefore", "thus", "also", "based", "shown", "showed", "show", "find",
    "found", "using", "used", "use", "uses", "via", "yet", "even", "well",
    "able", "see", "given", "such", "make", "made", "makes",
    "first", "second", "third", "one", "two", "three",
    "year", "years", "previous", "current",
    "value", "values", "level", "levels", "type", "types", "form", "forms",
    "way", "ways", "case", "cases", "set", "sets", "part", "parts",
    "could", "would", "might", "may", "must", "can",
    # P19b — observed false positives in actual fingerprint output:
    "name", "names", "utilize", "utilizes", "utilized", "utilization",
    "value", "values", "include", "includes", "included", "across",
    "specific", "specifically", "general", "generally", "particular",
    "respect", "regarding", "indicate", "indicates", "indicated",
    "represent", "represents", "represented", "representation",
    "function", "functions", "functional",
    # JSON-key noise from manipulation_variables_jsonb stringification
    "categorical", "continuous", "unit",
}
_STOPLIST_KO = {
    # Korean filler particles and common research-text fragments that
    # are not scientific content.
    "기반", "기준", "관련", "통해", "위해", "과정", "결과", "조사", "분석",
    "이용", "사용", "실험", "연구", "참여자", "피험자",
}
_STOPLIST = _STOPLIST_EN | _STOPLIST_KO

# --------------------------------------------------------- survey channel cfg
# Survey channel (P34 S9-fp) — seed the fingerprint from the researcher's OWN
# confirmed vocabulary (archive_survey_keywords + archive_survey_aims), joined
# by researcher_id = init (no paper resolution). This is the anchored signal
# that 5/7 fingerprints lacked (they fell through to Pass-B TF-IDF junk like
# 'none'/'item'/'reference'). Survey phrases TAKE PRECEDENCE over / augment the
# lexicon channel and, by entering `phrases` before the Pass-B gate, suppress
# the junk salvage for any researcher whose survey is populated.
#
# Weights: keyword > aim > ambiguous-def (least-certain, prose-derived). These
# land survey phrases at score ≈ weight·idf ≈ 6–9, above a single-channel
# lexicon hit (~5) and the Pass-B floor, so collisions resolve to the survey.
_W_SURVEY_KW     = 1.2   # §검색 키워드, is_ambiguous=false (confirmed specific term)
_W_SURVEY_AIM    = 1.0   # §연구 프로젝트 domain/phenomenon/task/mechanism phrases
_W_SURVEY_AMBDEF = 0.9   # multi-word phrases mined from an ambiguous keyword's
                         # operational_def (its OWN sense), minus conflict_term.

# THE P24 LANDMINE, honoured at extraction: an ambiguous keyword's BARE term
# ('bias','noise','drift','attractor','reference','scene',...) is a homonym the
# researcher flagged — seeding it as a substring phrase re-introduces the exact
# generic-word noise this batch removes, and the fingerprint's substring BM25
# cannot disambiguate it. So ambiguous keywords contribute ONLY multi-word
# phrases from their operational_def (never the bare term) — DEF-ONLY MINING is
# the protection. There is no conflict_term token/substring guard: batch10's
# `phrase in conflict` never fired, and a token-intersection replacement
# over-drops own-sense phrases because conflict_term is not a clean wrong-sense
# field (see _build_survey_phrases). The definition-aware veto lives downstream
# in recommend.py, which sees the paper text; extraction cannot.

# Single-word survey phrases are the risky ones (generic → off-topic matches).
# Keep a single word only if it is genuinely rare (corpus idf ≥ this floor) OR
# unseen (truly novel), AND not in the stoplist. Multi-word survey phrases are
# specific by construction and always kept. NB: this corpus is small/specialised
# so idf is a WEAK filter ('none'=6.85, 'item'=6.18) — the stoplist below is the
# real backstop for high-idf junk singles.
_SURVEY_SW_MIN_IDF = 6.0
_SURVEY_SW_EXTRA = {
    # high-idf-but-contentless singles observed leaking through Pass-B / survey
    "none", "false", "true", "item", "items", "target", "targets", "paradigm",
    "whether", "strength", "actively", "induce", "working", "input", "present",
    "reference", "comparison", "object", "shape", "size", "mean", "centered",
    "ignore", "lineage", "index",
}
# Function words used only as an n-gram BOUNDARY filter for the survey channel
# (keeps 'method of adjustment' whole but drops 'of adjustment' / 'vs reference'
# / 'for object'). Scoped to the survey pass — the shared _STOPLIST that other
# passes consume is left untouched.
_SURVEY_NGRAM_STOP = _STOPLIST | {
    "of", "vs", "for", "and", "or", "the", "to", "in", "on", "at", "by", "as",
    "an", "a", "with", "from", "than", "then", "into", "per", "not", "but",
    "is", "are", "be", "this", "that", "these", "those",
}
_INIT_RE = re.compile(r"^[A-Z]{2,8}$")

# Fingerprint size cap. Lexicon-channel phrases (Pass-A curated anchors) are
# EXEMPT from this cap (see _apply_phrase_cap): a flood of survey phrases must
# AUGMENT a curated fingerprint, never DISPLACE its distinctive anchors — 5 of
# JOP's 23 Pass-A anchors ('channel capacity','Blahut-Arimoto', …) were being
# evicted past rank 50 by higher-scored survey phrases (the MEDIUM V-consume
# finding).
_PHRASE_CAP = 50
_LEXICON_CHANNELS = frozenset({"anchor", "paradigm", "context"})


def _has_lexicon_channel(p: dict) -> bool:
    """True iff a phrase carries a Pass-A lexicon channel (anchor/paradigm/
    context) — i.e. it is a curated fingerprint anchor, not a pure survey/passB
    phrase. Such phrases are exempt from the cap in _apply_phrase_cap."""
    return any(ch in _LEXICON_CHANNELS for ch in p.get("channels") or ())


def _apply_phrase_cap(phrases: list[dict], cap: int = _PHRASE_CAP) -> list[dict]:
    """Cap the merged phrase list at `cap`, but NEVER evict a curated
    lexicon-channel anchor.

    Every phrase carrying a lexicon channel (anchor/paradigm/context) is kept;
    the remaining budget (cap − #protected, floored at 0) is filled with the
    top-scoring survey/passB phrases. So survey AUGMENTS a rich curated
    fingerprint rather than DISPLACING it (the MEDIUM V-consume finding: JOP
    silently lost 5 of 23 Pass-A anchors — 'channel capacity','Blahut-Arimoto',
    … — once survey phrases flooded the top 50). `phrases` is assumed
    score-sorted; the
    kept list is re-sorted by score. A researcher whose protected anchors alone
    exceed `cap` keeps them all (exemption) and simply gets no survey top-up —
    a rich fingerprint is never truncated to make room for survey phrases."""
    protected = [p for p in phrases if _has_lexicon_channel(p)]
    extra     = [p for p in phrases if not _has_lexicon_channel(p)]
    budget    = max(cap - len(protected), 0)
    kept      = protected + extra[:budget]
    kept.sort(key=lambda x: -x["score"])
    return kept


def _load_lexicon() -> list[str]:
    if not _LEXICON.exists():
        raise SystemExit(f"lexicon not found: {_LEXICON}")
    out = []
    for raw in _LEXICON.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    # Sort longest-first so greedy matching prefers longer phrases.
    out.sort(key=lambda s: -len(s))
    return out


def _load_idf() -> dict:
    if not _IDF.exists():
        raise SystemExit(f"idf not built: {_IDF} — run build_corpus_idf.py --apply first")
    return json.loads(_IDF.read_text("utf-8"))


def _load_taxonomy() -> dict:
    if not _TAXONOMY.exists():
        return {"dimensions": {}}
    return json.loads(_TAXONOMY.read_text("utf-8"))


def _fetch_active_researchers() -> list[str]:
    """All researchers with at least one active project above threshold."""
    sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
    from _db import load_env, query_json
    load_env()
    # Eligibility predicate (P34 S5) — kept identical at every call site.
    # `csnl_research.projects.phase` is FREE TEXT written only by csnl-ops: no
    # CHECK, no enum type (live values already include 'mapping (Stage 1 broad
    # map)' and 'deprecated_stub'). This whitelist is therefore a CLOSED LIST
    # OVER AN OPEN VOCABULARY. `NULL IN (...)` is NULL, never true, so an unset
    # phase silently deleted a live project (SMJ/visual_search, conf 0.95).
    # NULL must stay eligible.
    rows = query_json(
        "SELECT DISTINCT init FROM csnl_research.projects "
        "WHERE (phase IS NULL "
        "       OR phase IN ('data_collection','analysis','manuscript_draft')) "
        "  AND confidence_avg >= 0.7 ORDER BY init"
    )
    return [r["init"] for r in rows]


def _fetch_projects(init: str) -> list[dict]:
    sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
    from _db import load_env, query_json
    load_env()
    rows = query_json(f"""
        SELECT init, project_slug, title, phase, confidence_avg,
               purpose_jsonb::text AS purpose_text,
               background_jsonb::text AS background_text,
               manipulation_variables_jsonb::text AS mv_text,
               modalities_jsonb::text AS modalities_text,
               connected_graph_jsonb::text AS cg_text
          FROM csnl_research.projects
         WHERE init = '{init}'
           -- Eligibility predicate (P34 S5) — see _fetch_active_researchers:
           -- `phase` is free text written only by csnl-ops (no CHECK, no enum),
           -- a closed whitelist over an open vocabulary; NULL stays eligible.
           AND (phase IS NULL
                OR phase IN ('data_collection','analysis','manuscript_draft'))
           AND confidence_avg >= 0.7
         ORDER BY project_slug
    """)
    # Parse each JSON text column once.
    for r in rows:
        for k in ("purpose_text", "background_text", "mv_text",
                  "modalities_text", "cg_text"):
            v = r.get(k)
            if v:
                try:
                    r[k.removesuffix("_text")] = json.loads(v)
                except Exception:
                    r[k.removesuffix("_text")] = {}
            else:
                r[k.removesuffix("_text")] = {}
    return rows


# ----------------------------------------------------------- channel text

def _channel_text(row: dict) -> dict[str, str]:
    """Extract the three channels of source text for one project row."""
    purpose = row.get("purpose") or {}
    bg      = row.get("background") or {}
    mv      = row.get("mv") or row.get("manipulation_variables") or {}
    cg      = row.get("cg") or row.get("connected_graph") or {}
    mods    = row.get("modalities") or {}

    anchor_parts: list[str] = []
    if purpose.get("research_question"):
        anchor_parts.append(str(purpose["research_question"]))
    if purpose.get("hypothesis"):
        anchor_parts.append(str(purpose["hypothesis"]))
    if purpose.get("scientific_aim"):
        anchor_parts.append(str(purpose["scientific_aim"]))
    if bg.get("conceptual_anchor"):
        anchor_parts.append(str(bg["conceptual_anchor"]))

    paradigm_parts: list[str] = []
    for k in ("independent_vars", "dependent_vars", "fitted_parameters",
              "regression_model", "model_factors", "key_quantities"):
        v = mv.get(k)
        if isinstance(v, list):
            for item in v:
                paradigm_parts.append(str(item))
        elif isinstance(v, (str, int, float)):
            paradigm_parts.append(str(v))
        elif isinstance(v, dict):
            paradigm_parts.append(json.dumps(v, ensure_ascii=False))

    context_parts: list[str] = []
    if row.get("title"):
        context_parts.append(row["title"])
    if cg.get("shared_paradigm_with"):
        spw = cg["shared_paradigm_with"]
        if isinstance(spw, list):
            context_parts.extend(str(x) for x in spw)
    if mods:
        for v in mods.values() if isinstance(mods, dict) else []:
            if v:
                context_parts.append(str(v))

    return {
        "anchor":   " · ".join(p for p in anchor_parts if p),
        "paradigm": " · ".join(p for p in paradigm_parts if p),
        "context":  " · ".join(p for p in context_parts if p),
    }


# ----------------------------------------------------------- phrase extract

_PASSB_TOKEN_RE = re.compile(
    r"[A-Za-z0-9]{2,}(?:[-_][A-Za-z0-9]{2,})*"   # English-style tokens
    r"|"
    r"[가-힣]{2,}",                       # Hangul runs ≥ 2 syllables
)


def _passB_tfidf_salvage(channel_texts: dict[str, str], idf: dict) -> list[dict]:
    """TF-IDF top-K over researcher's combined project text. Used ONLY
    when Pass-A is sparse. Returns rows in the same shape Pass-A produces
    so the rest of the pipeline doesn't need to distinguish them.
    """
    unigram_idf = idf.get("idf") or {}
    # Pool text across channels with the same channel weights Pass-A uses.
    weighted_tokens: dict[str, float] = {}
    for ch, text in channel_texts.items():
        if not text:
            continue
        w = {"anchor": _W_ANCHOR, "paradigm": _W_PARADIGM,
             "context": _W_CONTEXT}[ch]
        low = text.lower()
        for tok in _PASSB_TOKEN_RE.findall(low):
            if len(tok) < _PASSB_MIN_LEN:
                continue
            if tok in _STOPLIST:
                continue
            if tok.isdigit():
                continue
            weighted_tokens[tok] = weighted_tokens.get(tok, 0.0) + w
    # Score = TF × IDF, only keep tokens whose corpus IDF is high enough.
    scored = []
    for tok, tf in weighted_tokens.items():
        tok_idf = unigram_idf.get(tok)
        if tok_idf is None or tok_idf < _PASSB_MIN_IDF:
            continue
        scored.append({
            "phrase":   tok,
            "score":    round(tf * tok_idf, 3),
            "channels": ["passB.tfidf"],
            "n_hits":   int(round(tf)),
        })
    scored.sort(key=lambda r: -r["score"])
    return scored[:_PASSB_TOP_K]


def _extract_phrases_one_channel(text: str, lexicon: list[str],
                                 idf: dict, channel_weight: float
                                 ) -> dict[str, dict]:
    """Greedy longest-match lexicon scan. Returns {phrase: {score, hits}}.

    Score = channel_weight * idf(phrase) * 1.0  (Pass-A only;
    Pass-B/C novelty discount deferred). hits = list of (start, end)
    positions in lower(text), for downstream provenance.
    """
    low = text.lower()
    out: dict[str, dict] = {}
    consumed = bytearray(len(low))  # 1 byte per char; True == claimed
    for ph in lexicon:
        ph_low = ph.lower()
        start = 0
        while True:
            idx = low.find(ph_low, start)
            if idx < 0:
                break
            end = idx + len(ph_low)
            # Skip if any underlying span already claimed by a longer phrase.
            if any(consumed[i] for i in range(idx, end)):
                start = end
                continue
            for i in range(idx, end):
                consumed[i] = 1
            entry = out.setdefault(ph, {"score": 0.0, "hits": []})
            phrase_idf = idf.get("phrase_idf", {}).get(ph_low,
                          # fallback IDF for lexicon phrases not seen in corpus
                          5.0)
            entry["score"] += channel_weight * phrase_idf
            entry["hits"].append([idx, end])
            start = end
    return out


def _merge_channel_phrases(by_channel: dict[str, dict[str, dict]]
                           ) -> list[dict]:
    """Combine across channels. Each phrase carries the union of channel
    weights and the maximum score across appearances."""
    merged: dict[str, dict] = {}
    for ch, hits in by_channel.items():
        for ph, info in hits.items():
            m = merged.setdefault(ph, {
                "phrase":    ph,
                "score":     0.0,
                "channels":  set(),
                "n_hits":    0,
            })
            m["score"] += info["score"]
            m["channels"].add(ch)
            m["n_hits"] += len(info["hits"])
    out = []
    for ph, m in merged.items():
        out.append({
            "phrase":   m["phrase"],
            "score":    round(m["score"], 3),
            "channels": sorted(m["channels"]),
            "n_hits":   m["n_hits"],
        })
    out.sort(key=lambda x: -x["score"])
    return out


# -------------------------------------------------------- survey channel
# P34 S9-fp. archive_survey_keywords (§검색 키워드 + operational_def) and
# archive_survey_aims (domain/phenomenon/task/mechanism) hold the researcher's
# CONFIRMED vocabulary, keyed by researcher_id = init. We turn those rows into
# clean, substring-matchable phrases and merge them into the fingerprint with
# precedence over the lexicon channel. Read-only SELECT; graceful no-op if the
# tables are absent (legacy env) or empty (e.g. MSY blank survey).

_ENG_RUN_RE = re.compile(r"[^A-Za-z0-9\- ]+")   # split on anything but ascii word / hyphen / space


def _english_ngrams(text: str, max_n: int = 3) -> list[str]:
    """Multi-word English phrases + unigrams from a Korean-mixed field.

    Split the field into contiguous English runs (Korean, slashes, parens,
    '+', commas are delimiters), then window each run into 1..max_n grams.
    Bi/tri-grams whose boundary token is a stopword are dropped — this
    ungueles concatenated concepts (e.g. 'EMonly delayed-estimation recurrent
    neural network' → 'recurrent neural network') without emitting filler.
    """
    out: list[str] = []
    for run in _ENG_RUN_RE.split(text or ""):
        toks = [t.strip("-") for t in run.split() if t.strip("-")]
        L = len(toks)
        for n in range(1, max_n + 1):
            for i in range(L - n + 1):
                gram = toks[i:i + n]
                if n >= 2 and (gram[0].lower() in _SURVEY_NGRAM_STOP
                               or gram[-1].lower() in _SURVEY_NGRAM_STOP):
                    continue
                phrase = " ".join(gram)
                if len(phrase) >= 3:
                    out.append(phrase)
    return out


def _clean_survey_keyword(raw: str) -> list[str]:
    """Turn one §검색 키워드 cell into clean matchable phrase(s).

    Survey keywords carry inline annotations — trailing/leading parentheticals
    ('working memory (WM)', '(scene) parsing', 'distractor (effect)') and
    bracketed operator/audit notes (〔…〕, […]). Emit forms that occur as a
    contiguous substring in real abstracts:
      * base  — the string with balanced parenthetical groups removed
      * merged — parens characters removed but content kept inline (recovers
                 continuations: '(scene) parsing' → 'scene parsing'), skipped
                 when the paren content is a short abbreviation (WM/LCI/RNN)
      * multi-word paren content on its own ('Local Concentricity Index')
    """
    s = re.sub(r"〔[^〕]*〕", " ", raw or "")
    s = re.sub(r"\[[^\]]*\]", " ", s).strip()
    if not s:
        return []
    groups = re.findall(r"\(([^()]*)\)", s)

    def _norm(t: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[()]", " ", t)).strip(" ·,;-")

    out: list[str] = []
    base = re.sub(r"\([^()]*\)", " ", s)          # drop balanced groups
    base = _norm(base)
    if base:
        out.append(base)
    abbrev_only = bool(groups) and all(
        re.fullmatch(r"[A-Z0-9/]{1,6}", g.strip()) for g in groups if g.strip())
    # The merged form (parens dropped, content kept inline) is only clean for a
    # LEADING parenthetical ('(scene) parsing' → 'scene parsing') or a
    # single-word base with single-word groups ('distractor (effect)' →
    # 'distractor effect'). For a multi-word base with a trailing group it just
    # garbles ('error (estimation error)' → 'error estimation error'); there the
    # base + the separately-promoted paren content already carry the signal.
    leading = s.lstrip().startswith("(")
    groups_single = all(" " not in g.strip() for g in groups if g.strip())
    merged = _norm(s)
    if (groups and not abbrev_only and merged and merged.lower() != base.lower()
            and (leading or (len(base.split()) == 1 and groups_single))):
        out.append(merged)
    for g in groups:                              # promote multi-word paren content
        g = g.strip()
        if " " in g and not re.fullmatch(r"[A-Z0-9/]{1,6}", g):
            out.append(_norm(g))
    # dedup preserving order
    seen, final = set(), []
    for p in out:
        pl = p.lower()
        if len(pl) >= 3 and pl not in seen:
            seen.add(pl)
            final.append(p)
    return final


def _survey_single_ok(word: str, idf: dict) -> bool:
    low = word.lower()
    if len(low) < 4 or low in _STOPLIST or low in _SURVEY_SW_EXTRA:
        return False
    uv = (idf.get("idf") or {}).get(low)
    return uv is None or uv >= _SURVEY_SW_MIN_IDF   # rare/unseen only


def _survey_phrase_ok(phrase: str, idf: dict) -> bool:
    toks = phrase.lower().split()
    if not toks:
        return False
    if len(toks) == 1:
        return _survey_single_ok(toks[0], idf)
    if len(toks) > 6:
        return False
    if toks[0] in _SURVEY_NGRAM_STOP or toks[-1] in _SURVEY_NGRAM_STOP:
        return False
    return not all(t in _SURVEY_NGRAM_STOP for t in toks)


def _survey_phrase_idf(phrase: str, idf: dict) -> float:
    low = phrase.lower()
    toks = low.split()
    if len(toks) >= 2:
        pv = (idf.get("phrase_idf") or {}).get(low)
        if pv is not None:
            return pv
        return 5.0 + 0.4 * (len(toks) - 1)          # specificity bump, unseen multiword
    uv = (idf.get("idf") or {}).get(low)
    return uv if uv is not None else 6.0


def _fetch_survey(init: str) -> tuple[list[dict], list[dict]]:
    """(keyword rows, aim rows) from the ledger schema; ([],[]) on any error."""
    if not _INIT_RE.match(init):
        return [], []
    try:
        sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
        from _db import load_env, query_json, ledger_schema
        load_env()
        sch = ledger_schema()
        kw = query_json(
            f"SELECT keyword, is_ambiguous, operational_def, conflict_term "
            f"FROM {sch}.archive_survey_keywords WHERE researcher_id = '{init}'")
        aims = query_json(
            f"SELECT domain, phenomenon, task, mechanism, confidence "
            f"FROM {sch}.archive_survey_aims WHERE researcher_id = '{init}'")
        return kw or [], aims or []
    except Exception as e:  # noqa: BLE001 — survey layer is optional overlay
        print(f"[fp] {init}: survey channel unavailable ({e.__class__.__name__}: {e})")
        return [], []


def _build_survey_phrases(init: str, idf: dict) -> list[dict]:
    """Confirmed-vocabulary phrases for one researcher, fingerprint-shaped."""
    kw_rows, aim_rows = _fetch_survey(init)
    # accumulate: lower(phrase) -> {phrase, weight (max), subs:set}
    acc: dict[str, dict] = {}

    def _add_one(phrase: str, weight: float, sub: str) -> None:
        if not _survey_phrase_ok(phrase, idf):
            return
        key = phrase.lower()
        m = acc.get(key)
        if m is None:
            acc[key] = {"phrase": phrase, "weight": weight, "subs": {sub}}
        else:
            m["weight"] = max(m["weight"], weight)
            m["subs"].add(sub)

    def _add(phrase: str, weight: float, sub: str) -> None:
        # Survey source text is inconsistently hyphenated ('ideal-observer' vs
        # 'ideal observer', 'delay-period' vs 'delay period'). Substring BM25 is
        # spelling-exact, so also seed the space-joined variant of any internal
        # word-hyphen-word so the confirmed term matches both spellings.
        _add_one(phrase, weight, sub)
        variant = re.sub(r"(?<=\w)-(?=\w)", " ", phrase)
        if variant != phrase:
            _add_one(variant, weight, sub)

    for r in kw_rows:
        if r.get("is_ambiguous") is True:
            # homonym: ONLY multi-word phrases from its OWN operational_def.
            # The bare term is never seeded (P24 landmine).
            #
            # DEF-ONLY MINING IS THE PROTECTION. We mine exclusively from the
            # researcher's own-sense operational_def, so every seeded phrase is
            # own-sense by construction. There is deliberately NO conflict_term
            # guard here. Batch10 shipped a `phrase in conflict` SUBSTRING test
            # that NEVER fired (a multi-word mined phrase is essentially never a
            # substring of the short conflict term — a false safety net). Its
            # obvious replacement — dropping any phrase whose tokens intersect
            # the conflict_term — was verified against live data and OVER-DROPS
            # legitimate own-sense phrases, because conflict_term is not a clean
            # wrong-sense signal:
            #   * it mixes the negation with a right-sense clarification —
            #     SYJ 'bias': "response priming 아님; bias = systematic shift 자체"
            #     (drops the own-sense 'systematic shift'); SYJ 'reference':
            #     "reference frame 아님; 여기선 reference stimulus/comparison"
            #     (drops own-sense 'reference/active comparison');
            #   * generic tokens are shared across senses — BYL 'precision':
            #     conflict 'inverse-variance' vs own 'variance of estimate';
            #     JYK 'dynamics': conflict 'high-dimensional' vs own
            #     'low-dimensional attractor'.
            # No token/substring test over this field is safe. The definition-
            # aware veto that CAN disambiguate (it sees the paper text) lives
            # downstream in recommend.py; extraction only guarantees own-sense
            # provenance.
            for ph in _english_ngrams(r.get("operational_def") or ""):
                if len(ph.split()) < 2:
                    continue
                _add(ph, _W_SURVEY_AMBDEF, "survey.kwdef")
        else:
            for ph in _clean_survey_keyword(r.get("keyword") or ""):
                _add(ph, _W_SURVEY_KW, "survey.kw")

    for r in aim_rows:
        cw = {"high": 1.0, "medium": 0.85, "low": 0.7}.get(
            r.get("confidence") or "medium", 0.85)
        for field in ("domain", "phenomenon", "task", "mechanism"):
            for ph in _english_ngrams(r.get(field) or ""):
                _add(ph, _W_SURVEY_AIM * cw, "survey.aim")

    out = []
    for m in acc.values():
        out.append({
            "phrase":   m["phrase"],
            "score":    round(m["weight"] * _survey_phrase_idf(m["phrase"], idf), 3),
            "channels": sorted(m["subs"]),
            "n_hits":   len(m["subs"]),
        })
    out.sort(key=lambda x: -x["score"])
    return out


def _merge_survey_into(lex_phrases: list[dict], survey_phrases: list[dict]
                       ) -> list[dict]:
    """Union survey phrases with the lexicon-channel phrases. Survey takes
    precedence: on a same-text collision the score is the max and the channels
    union (so provenance shows both), which — because survey scores sit above a
    single-channel lexicon hit — floats the confirmed term up the ranking."""
    by: dict[str, dict] = {}
    for p in lex_phrases:
        by[p["phrase"].lower()] = dict(p)
    for p in survey_phrases:
        k = p["phrase"].lower()
        if k in by:
            ex = by[k]
            ex["score"] = round(max(ex["score"], p["score"]), 3)
            ex["channels"] = sorted(set(ex["channels"]) | set(p["channels"]))
            ex["n_hits"] = ex.get("n_hits", 0) + p.get("n_hits", 0)
        else:
            by[k] = dict(p)
    out = list(by.values())
    out.sort(key=lambda x: -x["score"])
    return out


# -------------------------------------------------------- taxonomy priors

def _build_taxonomy_priors(phrases: list[dict], taxonomy: dict) -> dict:
    """For each taxonomy category, sum the scores of researcher-phrases
    that match any of its keywords. Normalize per-dim so the strongest cat
    gets 1.0. This is the soft prior for the Bayesian-update layer.
    """
    priors: dict[str, dict[str, float]] = {
        d: {} for d in taxonomy.get("dimensions", {})
    }
    phrase_index: dict[str, float] = {p["phrase"].lower(): p["score"] for p in phrases}
    for dim, cats in taxonomy.get("dimensions", {}).items():
        for code, c in cats.items():
            kws = (c.get("kw") or []) + (c.get("kw_ko") or [])
            s = 0.0
            for kw in kws:
                if kw.lower() in phrase_index:
                    s += phrase_index[kw.lower()]
            if s > 0:
                priors[dim][code] = s
    # Normalize per dim — strongest cat in each dim becomes 1.0.
    for dim, scores in priors.items():
        if not scores:
            continue
        m = max(scores.values()) or 1.0
        for code in list(scores.keys()):
            scores[code] = round(scores[code] / m, 3)
    return priors


# -------------------------------------------------- method signature build

def _build_method_signature(projects: list[dict]) -> dict:
    """Typed multiset of IV/DV names + paradigm compounds.

    Per Voice A §3.1. The compound 'IV+DV' key lets downstream scoring
    bonus papers that share BOTH an IV and a DV the researcher uses.
    """
    iv_names: Counter = Counter()
    iv_values: Counter = Counter()
    dv_names: Counter = Counter()
    paradigm_compounds: set[str] = set()
    for p in projects:
        mv = p.get("mv") or p.get("manipulation_variables") or {}
        ivs = mv.get("independent_vars") or []
        dvs = mv.get("dependent_vars") or []
        proj_ivs = []
        proj_dvs = []
        for iv in ivs:
            if isinstance(iv, dict):
                name = iv.get("name")
                if name:
                    iv_names[str(name)] += 1
                    proj_ivs.append(str(name))
                vals = iv.get("values") or []
                if isinstance(vals, list):
                    for v in vals:
                        iv_values[str(v)] += 1
            elif isinstance(iv, str):
                proj_ivs.append(iv)
                iv_names[iv] += 1
        for dv in dvs:
            if isinstance(dv, str):
                dv_names[dv] += 1
                proj_dvs.append(dv)
            elif isinstance(dv, dict):
                name = dv.get("name")
                if name:
                    dv_names[str(name)] += 1
                    proj_dvs.append(str(name))
        # paradigm compounds: every IV × every DV in the same project.
        for iv in proj_ivs:
            for dv in proj_dvs:
                paradigm_compounds.add(f"{iv}+{dv}")
    return {
        "iv_names":  dict(iv_names),
        "iv_values": dict(iv_values),
        "dv_names":  dict(dv_names),
        "paradigm_compound": sorted(paradigm_compounds),
    }


# ----------------------------------------------------------------- main

def _build_fingerprint(init: str, lexicon: list[str], idf: dict,
                       taxonomy: dict, use_survey: bool = True) -> dict:
    projects = _fetch_projects(init)
    if not projects:
        return {
            "researcher_id": init,
            "error": "no_active_projects",
            "version": 1,
            "built_at": datetime.now(KST).isoformat(timespec="seconds"),
        }

    # Per-project phrase extraction.
    per_project: dict[str, dict] = {}
    by_channel: dict[str, dict[str, dict]] = {"anchor": {}, "paradigm": {}, "context": {}}
    for proj in projects:
        text_by_ch = _channel_text(proj)
        for ch, text in text_by_ch.items():
            if not text:
                continue
            w = {"anchor": _W_ANCHOR, "paradigm": _W_PARADIGM,
                 "context": _W_CONTEXT}[ch]
            phrases_here = _extract_phrases_one_channel(text, lexicon, idf, w)
            for ph, info in phrases_here.items():
                pp = per_project.setdefault(proj["project_slug"], {})
                pp.setdefault(ph, []).append(ch)
                bc_ph = by_channel[ch].setdefault(ph, {"score": 0.0, "hits": []})
                bc_ph["score"] += info["score"]
                bc_ph["hits"].extend(info["hits"])

    lex_phrases = _merge_channel_phrases(by_channel)

    # P34 S9-fp — seed from the researcher's confirmed survey vocabulary and
    # merge it in with precedence. Done BEFORE the Pass-B gate so a populated
    # survey suppresses the TF-IDF junk salvage entirely. `use_survey=False`
    # (--no-survey) reverts to the legacy passA+passB behaviour so the eval can
    # A/B the channel without a git revert.
    survey_phrases = _build_survey_phrases(init, idf) if use_survey else []
    n_survey = len(survey_phrases)
    phrases = _merge_survey_into(lex_phrases, survey_phrases)
    # Cap at top _PHRASE_CAP phrases per researcher, but EXEMPT curated
    # lexicon-channel anchors from eviction so a survey flood augments rather
    # than displaces a rich fingerprint (JOP). See _apply_phrase_cap.
    phrases = _apply_phrase_cap(phrases)

    # P19b — Pass-B salvage for cold-start researchers. Fingerprints with
    # ≤ 3 Pass-A phrases get a TF-IDF top-15 boost so they have ANY
    # keyword signal at queue-build time. Marks the phrases distinctly so
    # downstream code can apply lower confidence if needed.
    passB_phrases: list[dict] = []
    if len(phrases) <= _PASSB_TRIGGER_THRESHOLD:
        # Re-collect text by channel for Pass-B (cheaper than passing it
        # down explicitly through the per-channel loop above).
        pooled_channels: dict[str, str] = {"anchor": "", "paradigm": "", "context": ""}
        for proj in projects:
            text_by_ch = _channel_text(proj)
            for ch, text in text_by_ch.items():
                if text:
                    pooled_channels[ch] = (pooled_channels[ch] + " " + text).strip()
        passB_phrases = _passB_tfidf_salvage(pooled_channels, idf)
        # De-dup against Pass-A (same phrase shouldn't double-count).
        existing = {p["phrase"].lower() for p in phrases}
        passB_phrases = [p for p in passB_phrases
                         if p["phrase"].lower() not in existing]
        phrases.extend(passB_phrases)

    # novel_terms = phrases NOT in the existing 52-tag taxonomy.
    tax_kws = set()
    for dim, cats in taxonomy.get("dimensions", {}).items():
        for c in cats.values():
            for kw in (c.get("kw") or []):
                tax_kws.add(kw.lower())
            for kw in (c.get("kw_ko") or []):
                tax_kws.add(kw.lower())
    novel_terms = [p["phrase"] for p in phrases
                   if p["phrase"].lower() not in tax_kws][:30]

    tag_priors = _build_taxonomy_priors(phrases, taxonomy)
    method_signature = _build_method_signature(projects)

    # Seed DOIs from prior_studies (for the deferred citation graph step).
    seed_dois = []
    for p in projects:
        bg = p.get("background") or {}
        prior = bg.get("prior_studies") or []
        if isinstance(prior, list):
            for s in prior:
                if isinstance(s, dict) and s.get("doi"):
                    seed_dois.append({
                        "doi":      s["doi"],
                        "project":  p["project_slug"],
                    })

    return {
        "researcher_id":  init,
        "version":        1,
        "built_at":       datetime.now(KST).isoformat(timespec="seconds"),
        "source_projects": [p["project_slug"] for p in projects],
        "phrases":        phrases,
        "novel_terms":    novel_terms,
        "tag_priors":     tag_priors,
        "method_signature": method_signature,
        "seed_dois":      seed_dois,
        "provenance": {
            "extractor_version": "fp.v3.survey-anchored+passA+passB",
            "lexicon_n_entries": len(lexicon),
            "idf_version":       idf.get("version"),
            "taxonomy_version":  taxonomy.get("version"),
            # Counts are pre-cap channel yields; `phrases` is the capped union.
            "lexicon_n_phrases": len(lex_phrases),
            "survey_n_phrases":  n_survey,
            "survey_seeded":     n_survey > 0,
            "passB_n_phrases":   len(passB_phrases),
            "passB_triggered":   len(passB_phrases) > 0,
        },
        # Backward-compat: a `dim_preferences` projection so legacy
        # readers (build_researcher_queue.py) get the shape they expect.
        # The projection puts each populated tax_prior into the `focus/
        # method/stim/subj` keys; downstream code can still call
        # `_dim_score()` on this exactly as it does today.
        "dim_preferences": {
            "focus":   tag_priors.get("focus", {}),
            "method":  tag_priors.get("method", {}),
            "stim":    tag_priors.get("stim", {}),
            "subj":    tag_priors.get("subj", {}),
            "combo_bonus":     [],
            "project_weights": {},
            "source":  "fingerprint.v1",
            "version": 1,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("researcher", nargs="?", default=None,
                    help="Researcher init. If omitted, build for all active.")
    ap.add_argument("--apply", action="store_true",
                    help="Write JSON files. Default: dry-run.")
    ap.add_argument("--no-survey", action="store_true",
                    help="Disable the P34 survey channel (legacy passA+passB "
                         "only). For A/B evaluating the survey seed.")
    args = ap.parse_args()

    lexicon  = _load_lexicon()
    idf      = _load_idf()
    taxonomy = _load_taxonomy()
    print(f"[fp] lexicon={len(lexicon)} phrases  idf_version={idf.get('version')}  "
          f"tax_version={taxonomy.get('version')}  "
          f"survey_channel={'off' if args.no_survey else 'on'}")

    if args.researcher:
        inits = [args.researcher.strip().upper()]
    else:
        inits = _fetch_active_researchers()
    print(f"[fp] researchers to process: {inits}")

    if args.apply:
        _OUT_DIR.mkdir(parents=True, exist_ok=True)

    for init in inits:
        fp = _build_fingerprint(init, lexicon, idf, taxonomy,
                                use_survey=not args.no_survey)
        if fp.get("error"):
            print(f"[fp] {init}: SKIP ({fp['error']})")
            continue
        # Console summary.
        top = fp["phrases"][:5]
        prov = fp.get("provenance", {})
        print(f"[fp] {init}: phrases={len(fp['phrases'])}  "
              f"(lex={prov.get('lexicon_n_phrases')} survey={prov.get('survey_n_phrases')} "
              f"passB={prov.get('passB_n_phrases')})  novel={len(fp['novel_terms'])}  "
              f"seed_dois={len(fp['seed_dois'])}  ivs={len(fp['method_signature']['iv_names'])}")
        for p in top:
            print(f"        '{p['phrase']}' score={p['score']:.2f} channels={p['channels']}")
        if args.apply:
            out = _OUT_DIR / f"{init}.json"
            out.write_text(json.dumps(fp, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            print(f"        → {out.relative_to(_REPO_ROOT)}")

    if not args.apply:
        print("[fp] dry-run only. Re-run with --apply to write JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
