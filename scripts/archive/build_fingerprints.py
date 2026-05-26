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
    rows = query_json(
        "SELECT DISTINCT init FROM csnl_research.projects "
        "WHERE phase IN ('data_collection','analysis','manuscript_draft') "
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
           AND phase IN ('data_collection','analysis','manuscript_draft')
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
                       taxonomy: dict) -> dict:
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

    phrases = _merge_channel_phrases(by_channel)
    # Cap at top 50 phrases per researcher.
    phrases = phrases[:50]

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
            "extractor_version": "fp.v2.passA-plus-passB-salvage",
            "lexicon_n_entries": len(lexicon),
            "idf_version":       idf.get("version"),
            "taxonomy_version":  taxonomy.get("version"),
            "passA_n_phrases":   len(phrases) - len(passB_phrases),
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
    args = ap.parse_args()

    lexicon  = _load_lexicon()
    idf      = _load_idf()
    taxonomy = _load_taxonomy()
    print(f"[fp] lexicon={len(lexicon)} phrases  idf_version={idf.get('version')}  "
          f"tax_version={taxonomy.get('version')}")

    if args.researcher:
        inits = [args.researcher.strip().upper()]
    else:
        inits = _fetch_active_researchers()
    print(f"[fp] researchers to process: {inits}")

    if args.apply:
        _OUT_DIR.mkdir(parents=True, exist_ok=True)

    for init in inits:
        fp = _build_fingerprint(init, lexicon, idf, taxonomy)
        if fp.get("error"):
            print(f"[fp] {init}: SKIP ({fp['error']})")
            continue
        # Console summary.
        top = fp["phrases"][:5]
        print(f"[fp] {init}: phrases={len(fp['phrases'])}  novel={len(fp['novel_terms'])}  "
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
