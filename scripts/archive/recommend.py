#!/usr/bin/env python3
"""
scripts/archive/recommend.py — P28b connection-based recommender.

Implements the survey appendix's "Connection invariant": a paper is a candidate
iff it GENUINELY connects to one of the researcher's aims on aim ∨ phenomenon ∨
mechanism (same computational JOB) — NOT exact match, and species/domain/method
NEVER disqualify. It then vetoes phenomenon/research-focus negatives, penalises
wrong-sense keyword matches (definition-aware), and reranks the survivors.

This is the upgrade build_researcher_queue.py (P19/P26) never had: per-researcher
known_negatives veto, aim-tuple admission, definition-aware subtraction,
mechanism connection axis, PI polarity (survey appendix §B "미구현 소비자").

PIPELINE (per researcher)
  1. memory          : archive_survey_* (or pre-fill via ingest_survey) — aims
                       (the connection anchor), negatives, keywords+defs, PIs,
                       methods, models.
  2. candidate-gen   : structured-lexical overlap of each paper's synopsis
                       (connecting_signals / frameworks / core_question /
                       key_findings) + title/abstract against the aims' anchors
                       and keywords ∪ (optional) embedding cosine recall.
  3. connection score: per (candidate, aim) structured overlap on axis
                       A=aim(domain+task) / B=phenomenon / C=mechanism+framework.
                       STRONG → admit; clear-NONE → reject; BORDERLINE → the LLM
                       reasoning-gate (P26-style same-job test).
  4. veto            : drop a candidate whose phenomenon/research-focus matches a
                       negatives row (the always-missing per-researcher exclude).
  5. def-aware       : for ambiguous keywords (operational_def + conflict_term),
                       penalise a match made in the conflict_term's sense.
  6. rerank          : w·connection + w·mechanism + w·keyword(def-aware) +
                       w·PI polarity + w·method + w·domain-priority + w·recency.
  7. output          : per-researcher ranked queue → archive_researcher_queues
                       with provenance (connected_aim, connection_axis,
                       veto_checked), recent/mid/classic chunked, capped 200.

LLM REASONING-GATE — boundary-safe (NO LLM in the unattended path)
  --gate-mode structured : (default) the deterministic structured pre-filter
        makes admit/reject; borderline pairs are admitted only if their best
        axis clears a mid threshold. Fully offline. A faithful stand-in for a
        dry-run / cron build.
  --gate-mode emit       : write borderline (researcher, paper, aim, synopsis,
        axes) to --gate-queue JSONL. The ORCHESTRATOR (operator-attended) fans
        out Opus sub-agents to judge them (same-job test) — NOT an in-script API
        call; no key lives here — and writes verdicts back.
  --gate-mode cached     : read cached A/B/C/none verdicts (local JSONL and/or
        archive_relevance_decisions gate_engine='p28-connection'); unknown
        borderline pairs default to REJECT (conservative — never over-admit on a
        cache miss). This is the cron-safe, zero-LLM path.

BOUNDARY: csnl_research read-only; archive_responses is READ-ONLY and only touched
under the gated --use-behaviour flag (default OFF); default dry-run (writes JSONL
only); --apply UPSERTs archive_researcher_queues as the PARKED builder='p28' —
pruning only its own rows so it never deletes brq's authoritative queue (P28 not
live; --apply stays operator-gated). No researcher-facing send (.P23_ENABLED gate
stays off, owned elsewhere). Tier is derived from connection strength (R-TIER),
not hardcoded.

CLI
  python3 scripts/archive/recommend.py --only JOP                  # dry-run, structured
  python3 scripts/archive/recommend.py --only JOP --embed          # + cosine recall
  python3 scripts/archive/recommend.py --only JOP --gate-mode emit --gate-queue q.jsonl
  python3 scripts/archive/recommend.py --only JOP --gate-mode cached --gate-cache v.jsonl
  python3 scripts/archive/recommend.py --only JOP --use-behaviour  # GATED: +responses veto/boost
  ! python3 scripts/archive/recommend.py --all --gate-mode cached --apply   # operator (builder='p28')
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "weekly"))
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))

import ingest_survey as ing          # noqa: E402
import build_researcher_queue as brq  # noqa: E402 — reuse loaders + chunking

_SYNOPSIS_DIR = _REPO_ROOT / "state" / "archive" / "synopses"
_OUT_DIR = _REPO_ROOT / "state" / "archive" / "queues_p28"

CHUNK_MIX = {"recent": 120, "mid": 60, "classic": 20}
# QUEUE_CAP — the explicit per-researcher hard cap (P33 MF-12). It equals
# sum(CHUNK_MIX); the per-chunk caps already bound the queue to this total, so the
# final slice is belt-and-braces and holds even if CHUNK_MIX is retuned upward.
# This REPLACES the dead MAX_QUEUE=1000 constant + its unreachable `len(out) >
# MAX_QUEUE` guard, which never fired because the per-chunk caps sum to 200 < 1000.
QUEUE_CAP = sum(CHUNK_MIX.values())  # 200

# --- connection thresholds (axis overlap score, 0..1) --------------------
STRONG_CONN = 0.50    # ≥ → admit deterministically (genuine structured overlap)
WEAK_CONN = 0.18      # < AND low cosine → reject (no signal)
COS_BORDERLINE = 0.55  # high cosine w/ weak structured → borderline (gate it)
VETO_THRESH = 0.45    # phenomenon/focus overlap with a negative → veto

# --- rerank weights (tunable; logged) ------------------------------------
W_CONN, W_MECH, W_KW, W_PI, W_METHOD, W_DOMAIN, W_RECENCY = \
    0.40, 0.18, 0.12, 0.08, 0.06, 0.06, 0.10
W_AXIS_B = 0.06       # phenomenon-axis bonus (B = the connection criterion)
W_BEHAVIOUR = 0.08    # MF-7 (GATED): boost toward saved/read-paper signals. The
                      # term is non-zero ONLY when --use-behaviour is on (default
                      # OFF); it must pass an offline precision eval vs
                      # validate_drift before promotion (it changes rankings).
DEF_PENALTY = 0.50    # multiplicative penalty for a wrong-sense keyword match
RECENCY_TIER = {"recent": 1.0, "mid": 0.6, "classic": 0.4}

# Generic words that must NOT carry a connection on their own (the C-axis
# over-fire vector from the P26 review: model-name / generic methodology word).
_STOPish = {
    "model", "models", "modeling", "task", "effect", "effects", "bias",
    "learning", "memory", "attention", "perception", "representation", "neural",
    "brain", "behavior", "behaviour", "human", "cognitive", "dynamics",
    "analysis", "information", "estimation", "decision", "response", "stimulus",
    "visual", "the", "and", "for", "with", "from", "of", "in", "to", "as",
    # pure glue / meta tokens (adversarial review M3) — never a meaningful
    # bigram head/tail in this domain, so safe to drop as standalone phrases
    "only", "tool", "account", "general", "based", "using", "via", "study",
    "studies", "results", "across", "within", "between", "paper", "approach",
    "role", "both", "this", "that", "these", "those", "use", "used",
    # Korean generic / particle-ish tokens that survive normalisation
    "그", "및", "자체", "자체의", "모델링", "연구", "논문", "관련", "기반",
    "사용", "포함", "현상", "효과", "방법", "전체", "그자체", "있는", "대한",
    "매칭", "없음", "연결", "태그", "그것", "위한",
}


# ===========================================================================
# Text / phrase utilities
# ===========================================================================

_WS_RE = re.compile(r"[^0-9a-zA-Z가-힣]+")


def _norm(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").lower()).strip()


_OVERLAP_GAMMA = 1.0   # saturation sharpness: one salient bigram ⇒ ~0.55


def _ngrams(toks: list[str]) -> list[str]:
    """Adjacent 2- and 3-grams of `toks` plus significant unigrams. An n-gram
    that is entirely stop-ish words is dropped (it carries no connection)."""
    out: list[str] = []
    n = len(toks)
    for i in range(n):
        if toks[i] not in _STOPish and len(toks[i]) >= 4:
            out.append(toks[i])                       # significant unigram
        if i + 1 < n:
            bg = toks[i:i + 2]
            if not all(w in _STOPish for w in bg):
                out.append(" ".join(bg))
        if i + 2 < n:
            tg = toks[i:i + 3]
            if not all(w in _STOPish for w in tg):
                out.append(" ".join(tg))
    return out


def _phrases(*texts: str) -> list[str]:
    """Extract salient connection phrases: split each text on clause separators,
    then take 2-/3-grams + significant unigrams of each clause's tokens. This
    surfaces 'sensory adaptation' from 'sensory adaptation 그 자체의 모델링' and
    'serial dependence' from 'history effect / serial dependence (…)', which a
    whole-clause-as-one-phrase split would miss."""
    out: list[str] = []
    for t in texts:
        if not t:
            continue
        for part in re.split(r"[/·,;:|()→\[\]{}]+|\bvs\b|\b및\b|\b또는\b", t):
            pn = _norm(part)
            if not pn:
                continue
            toks = [w for w in pn.split() if len(w) > 1]
            out.extend(_ngrams(toks))
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _phrase_weight(p: str) -> float:
    nt = len(p.split())
    if nt >= 3:
        return 1.0
    if nt == 2:
        return 0.8
    return 0.45   # significant unigram


def _overlap(hay_norm: str, needles: list[str]) -> tuple[float, list[str]]:
    """SATURATING phrase-containment overlap: score = 1 - exp(-γ·Σ matched
    weight). One salient multiword match (weight 0.8) ⇒ ~0.55; two ⇒ ~0.80.
    Unlike a ratio over all needles, adding more aim phrases does NOT dilute a
    genuine match — the right behaviour for 'does this paper connect on ANY
    salient concept'. Longer-phrase matches subsume their sub-grams (a matched
    trigram suppresses its bigrams' double-count)."""
    if not needles:
        return 0.0, []
    matched = [n for n in needles if n and n in hay_norm]
    if not matched:
        return 0.0, []
    # suppress double-counting: drop a matched phrase that is a substring of
    # another, longer matched phrase (so 'serial dependence' inside a matched
    # 'serial dependence effect' counts once, at the longer weight).
    keep: list[str] = []
    for m in sorted(matched, key=len, reverse=True):
        if not any(m != k and m in k for k in keep):
            keep.append(m)
    wsum = sum(_phrase_weight(m) for m in keep)
    score = 1.0 - math.exp(-_OVERLAP_GAMMA * wsum)
    keep.sort(key=lambda x: -_phrase_weight(x))
    return min(1.0, score), keep


# ===========================================================================
# Corpus
# ===========================================================================

def _load_synopsis_text() -> dict[str, dict]:
    """canonical_id -> {text_norm, signals, frameworks, oos} from the per-cid
    synopsis JSON. text_norm is the high-signal connection text."""
    out: dict[str, dict] = {}
    if not _SYNOPSIS_DIR.exists():
        return out
    for fp in _SYNOPSIS_DIR.glob("*.json"):
        try:
            d = json.loads(fp.read_text("utf-8"))
        except Exception:
            continue
        cid = d.get("canonical_id") or fp.stem
        signals = d.get("connecting_signals") or []
        fws = [f.get("name", "") for f in (d.get("frameworks") or [])
               if isinstance(f, dict)]
        parts = list(signals) + fws
        if d.get("core_question"):
            parts.append(d["core_question"])
        parts += (d.get("key_findings") or [])
        parts += (d.get("key_assumptions") or [])
        parts += (d.get("manipulations") or [])
        out[cid] = {
            "text_norm": _norm(" ".join(str(p) for p in parts)),
            "signals": signals,
            "frameworks": fws,
            "oos": bool(d.get("out_of_scope_note")),
        }
    return out


def _paper_text_norm(paper: dict, syn: Optional[dict]) -> str:
    parts = [paper.get("title") or "", (paper.get("abstract") or "")[:1800]]
    if syn:
        parts.append(syn["text_norm"])
    return _norm(" ".join(parts))


# ===========================================================================
# Aim model
# ===========================================================================

class Aim:
    __slots__ = ("aim_id", "domain", "phenomenon", "task", "mechanism",
                 "population", "ph_A", "ph_B", "ph_C", "all_phrases")

    def __init__(self, a: dict, kw_phrases: list[str]):
        self.aim_id = a["aim_id"]
        self.domain = a.get("domain") or ""
        self.phenomenon = a.get("phenomenon") or ""
        self.task = a.get("task") or ""
        self.mechanism = a.get("mechanism") or ""
        self.population = a.get("population") or ""
        # axis phrase sets
        self.ph_A = _phrases(self.domain, self.task)              # aim
        self.ph_B = _phrases(self.phenomenon)                     # phenomenon
        self.ph_C = _phrases(self.mechanism)                      # mechanism
        self.all_phrases = list(dict.fromkeys(
            self.ph_A + self.ph_B + self.ph_C + kw_phrases))


def _build_aims(mem: dict) -> list[Aim]:
    kw_phrases = _phrases(*[k["keyword"] for k in mem["keywords"]])
    return [Aim(a, kw_phrases) for a in mem["aims"]]


# ===========================================================================
# Connection scoring (candidate × aim) — the heart of the contract
# ===========================================================================

# Bare prepositions/conjunctions: a multiword phrase bookended by one of these
# is glue ('driven by', 'signal to'), not a specific concept (adversarial review
# C2 residual). The interior trigram ('signal to noise') still counts.
_PREP = {"by", "to", "of", "in", "on", "for", "with", "vs", "per", "as",
         "and", "or", "from", "into", "onto", "the", "a"}


def _specific(matched: list[str]) -> list[str]:
    """The SPECIFIC (multiword, non-glue) members of a matched-phrase list. A
    unigram match ('bayesian', 'prior', 'rate') is generic and must not, on its
    own, carry a genuine connection (adversarial review C1). A bigram bookended
    by a bare preposition ('driven by', 'signal to') is glue, not a concept
    (adversarial review C2 residual), so it is also excluded."""
    out = []
    for m in matched:
        toks = m.split()
        if len(toks) < 2:
            continue
        if toks[0] in _PREP or toks[-1] in _PREP:
            continue
        out.append(m)
    return out


def connection(paper_norm: str, aim: Aim) -> dict:
    """Score the genuine connection of a paper to one aim, with admission gated
    on SPECIFIC PHENOMENON evidence (the appendix's same-job rule + the
    adversarial-review C1 fix):

      ADMIT (structurally certain) ⟺
         a SPECIFIC (multiword) phenomenon phrase matches at ≥ STRONG_CONN, OR
         phenomenon AND mechanism both match specifically (clear same-job).
      Everything else — a mechanism-NAME match (even a specific multiword one
      like 'rate distortion theory', which collides with off-domain papers), an
      aim/domain-only match, or generic-unigram overlap — is BORDERLINE and the
      LLM same-job gate decides it. A specific multiword mechanism name is NOT
      sufficient to auto-admit, because the structured layer cannot tell
      'rate-distortion of duration priors' from 'rate-distortion in 5G'.
      NONE ⟺ no specific match on any axis and no phenomenon signal.

    decision ∈ admit | borderline | none; `specB` flags specific-phenomenon
    evidence (used by the offline stand-in + the veto's over-veto rail)."""
    sA, mA = _overlap(paper_norm, aim.ph_A)
    sB, mB = _overlap(paper_norm, aim.ph_B)
    sC, mC = _overlap(paper_norm, aim.ph_C)
    spB, spC, spA = _specific(mB), _specific(mC), _specific(mA)
    has_B, has_C, has_A = bool(spB), bool(spC), bool(spA)
    # specA must be DISTINCT from the mechanism phrases to serve as a same-job
    # proxy: when a survey repeats one string across BOTH task and mechanism
    # (JYK put 'recurrent neural network (RNN)' in axis A and C), a bare match on
    # that shared phrase is NOT independent A+C evidence — it is one generic
    # mechanism-name hit (adversarial review C1 regression). Exclude ph_A ∩ ph_C.
    _phc = set(aim.ph_C)
    has_A_distinct = bool([m for m in spA if m not in _phc])

    if has_B and sB >= STRONG_CONN:
        decision, axis, score, matched = "admit", "B", sB, mB
    elif has_B and has_C and sC >= STRONG_CONN:        # phenomenon + mechanism
        decision, axis, score, matched = "admit", "B", max(sB, sC), spB + spC
    elif has_B or has_C or has_A:                       # specific, but not same-job-certain
        decision = "borderline"
        axis = "B" if has_B else ("C" if has_C else "A")
        score = sB if has_B else (sC if has_C else sA)
        matched = spB or spC or spA
    elif sB >= WEAK_CONN:                               # generic phenomenon signal only
        decision, axis, score, matched = "borderline", "B", sB, mB
    else:
        decision, axis, score, matched = "none", "B", max(sA, sB, sC), []

    return {"axis": axis, "score": round(score, 3), "matched": matched[:5],
            "decision": decision, "specB": has_B, "specC": has_C, "specA": has_A,
            "specA_distinct": has_A_distinct,
            "sA": round(sA, 3), "sB": round(sB, 3), "sC": round(sC, 3)}


def _derive_tier(conn: dict) -> str:
    """R-TIER (P33 A4/MF-12): derive a real queue tier from connection strength,
    replacing the P28b hardcoded 'B'. Emits the S/A/B labels build_digest's tier
    solver (TIER_TARGETS = {S,A,B}) consumes:
        specB-strong  → 'S'  (a SPECIFIC phenomenon phrase matched at ≥ STRONG_CONN
                              — the axis-B admit: the genuine same-job connection)
        specB         → 'A'  (specific phenomenon present but sub-threshold)
        else          → 'B'  (mechanism/aim-only, gated, or generic — the floor)
    Pure. Parked until P28 is promoted (recommend --apply stays operator-gated)."""
    if conn.get("specB"):
        return "S" if conn.get("sB", 0.0) >= STRONG_CONN else "A"
    return "B"


# ===========================================================================
# Veto (per-researcher negatives) + definition-aware subtraction
# ===========================================================================

def _veto(paper_norm: str, negatives: list[dict], conn_score: float,
          conn_specific: bool) -> Optional[dict]:
    """Return the matched negative (veto) or None. Fires ONLY on phenomenon/
    research-focus/anti-example negatives (never species/domain/method).

    Adversarial-review C2 fix — the anchor is the structured `excl_topic` ONLY,
    NOT the free contrast prose (whose glue words 'mechanism / pattern / only /
    abstract / rate' became false veto triggers and killed genuine task-matched
    papers). A SPECIFIC (multiword) excl_topic phrase must match — a generic
    unigram never vetoes. Guards:
      * a STRONG genuine connection (≥STRONG_CONN) is never lexically vetoed;
      * a paper with a SPECIFIC phenomenon connection needs the exclusion to
        CLEARLY dominate (≥ conn+0.20) before it is dropped;
      * otherwise the exclusion must outscore the connection (≥ conn+0.10) or be
        very strong on its own (≥0.70)."""
    best: Optional[dict] = None
    for n in negatives:
        ntype = (n.get("neg_type") or "").lower()
        if ntype and ntype not in ("phenomenon", "research-focus", "anti-example"):
            continue
        anchor = _phrases(n.get("excl_topic") or "")        # excl_topic ONLY
        score, matched = _overlap(paper_norm, anchor)
        if not _specific(matched) or score < VETO_THRESH:   # require a SPECIFIC match
            continue
        if conn_score >= STRONG_CONN:
            continue
        margin = 0.20 if conn_specific else 0.10
        if score >= conn_score + margin or score >= 0.70:
            if best is None or score > best["score"]:
                best = {"neg_id": n["neg_id"], "score": round(score, 3),
                        "matched": _specific(matched)[:4], "type": ntype or "?"}
    return best


def _def_penalty(paper_norm: str, keywords: list[dict]) -> tuple[float, list[str]]:
    """Definition-aware subtraction: for an ambiguous keyword whose conflict_term
    sense appears in the paper but whose operational_def anchors do NOT, apply a
    penalty (wrong-sense match). Returns (multiplier ∈ (0,1], notes)."""
    mult = 1.0
    notes: list[str] = []
    for k in keywords:
        if not k.get("is_ambiguous"):
            continue
        kw = _norm(k["keyword"])
        if not kw or kw not in paper_norm:
            continue
        _, conflict_m = _overlap(paper_norm, _phrases(k.get("conflict_term") or ""))
        _, def_m = _overlap(paper_norm, _phrases(k.get("operational_def") or ""))
        # "right sense present" requires SPECIFIC (multiword) operational_def
        # evidence — generic-unigram overlap ('rate', 'prior') must NOT cancel
        # the penalty (adversarial review H1). Wrong-sense conflict evidence is
        # likewise only credited when specific.
        has_conflict = bool(_specific(conflict_m))
        has_def = bool(_specific(def_m))
        if has_conflict and not has_def:
            mult *= DEF_PENALTY
            notes.append(f"{k['keyword']}~wrong-sense")
    return mult, notes


# ===========================================================================
# Rerank features
# ===========================================================================

def _domain_priority(paper_norm: str, aim: Aim) -> float:
    """Weak priority signal — same domain + neurotypical-human bonus; NEVER a
    veto. Cross-domain/species papers keep their connection score, just ranked
    a touch lower."""
    dscore, _ = _overlap(paper_norm, _phrases(aim.domain))
    bonus = 0.5 * dscore
    if aim.population and "neuroty" in _norm(aim.population) and \
            ("human" in paper_norm or "adult" in paper_norm):
        bonus += 0.3
    return min(1.0, bonus)


def _pi_signal(paper: dict, pis: list[dict]) -> float:
    auth = _norm(" ".join(paper.get("authors_json") or []) if isinstance(
        paper.get("authors_json"), list) else str(paper.get("authors_json") or ""))
    s = 0.0
    for pi in pis:
        # match on the PI's surname tokens
        toks = [w for w in _norm(pi["pi_name"]).split() if len(w) > 2]
        if any(t in auth for t in toks):
            s += 1.0 if pi.get("polarity") == "+" else -1.0
    return max(-1.0, min(1.0, s))


def _method_signal(paper_norm: str, methods: list[dict]) -> float:
    if not methods:
        return 0.0
    ph = _phrases(*[m["approach"] for m in methods])
    score, _ = _overlap(paper_norm, ph)
    return score


# ===========================================================================
# LLM reasoning-gate (pluggable; boundary-safe)
# ===========================================================================

def _gate_key(init: str, cid: str, aim_id: str) -> str:
    return f"{init}|{cid}|{aim_id}"


def load_gate_cache(paths: list[Path]) -> dict[str, dict]:
    cache: dict[str, dict] = {}
    for p in paths:
        if not p or not p.exists():
            continue
        for line in p.read_text("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            cache[_gate_key(d["researcher_id"], d["canonical_id"], d["aim_id"])] = d
    return cache


# ===========================================================================
# Behaviour signal (MF-7, GATED behind --use-behaviour — DEFAULT OFF)
# ---------------------------------------------------------------------------
# EXTENDS the existing veto/rerank (NO parallel engine):
#   * archive_responses `not_relevant` → EXTRA negatives fed to the EXISTING
#     `_veto`, anchored ONLY on the researcher's stated reason text — NEVER the
#     paper's own connecting_signal (the P24 landmine: save/reject signals
#     overlap). A not_relevant with no reason text yields NO veto anchor.
#   * `save_later`/`already_read` → boost phrases (a positive-only input to the
#     EXISTING rerank via W_BEHAVIOUR).
# The whole path is gated behind an offline precision eval vs validate_drift
# before it may be promoted (it changes rankings). Pure transforms below are
# offline-testable; only load_behaviour touches the (read-only) prod DB and is
# called solely when the flag is set.
# ===========================================================================

def _reason_text(detail: Any) -> str:
    """Best-effort extraction of the researcher's free-text reason from an
    archive_responses.choice_detail value (dict / JSON string / plain string).
    Returns '' when no usable text is present."""
    if not detail:
        return ""
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except Exception:
            return detail.strip()
    if isinstance(detail, dict):
        keyed = [detail[k].strip() for k in
                 ("reason", "why", "why_not", "why_not_relevant", "note",
                  "text", "detail", "comment", "rationale")
                 if isinstance(detail.get(k), str) and detail[k].strip()]
        if keyed:
            return " ".join(keyed)
        return " ".join(v.strip() for v in detail.values()
                        if isinstance(v, str) and v.strip())
    return ""


def _behaviour_negatives(responses: list[dict]) -> list[dict]:
    """not_relevant responses → EXTRA negatives for the existing `_veto`, anchored
    ONLY on the researcher's stated reason text (never the paper's own signals —
    the P24 landmine). A not_relevant with NO reason text yields NO veto anchor.
    Rows match mem['negatives'] shape (`_veto` reads neg_id/neg_type/excl_topic);
    neg_type='anti-example' clears the veto's phenomenon/focus/anti-example filter.
    Pure."""
    out: list[dict] = []
    for r in responses:
        if (r.get("choice") or "") != "not_relevant":
            continue
        reason = _reason_text(r.get("choice_detail"))
        if not reason.strip():
            continue                      # no reason ⇒ no veto (landmine guard)
        out.append({"neg_id": f"beh:{r.get('canonical_id')}",
                    "neg_type": "anti-example", "excl_topic": reason})
    return out


def _behaviour_positive_phrases(responses: list[dict], synopses: dict) -> list[str]:
    """save_later / already_read responses → boost phrases drawn from those
    papers' synopsis signals/frameworks (a positive rerank INPUT, not a veto — the
    landmine only concerns vetoing on shared signals). Deduped, stop-filtered by
    `_phrases`. Pure w.r.t. the passed `synopses` dict."""
    texts: list[str] = []
    for r in responses:
        if (r.get("choice") or "") not in ("save_later", "already_read"):
            continue
        syn = synopses.get(r.get("canonical_id"))
        if not syn:
            continue
        texts.extend(str(s) for s in (syn.get("signals") or []))
        texts.extend(str(f) for f in (syn.get("frameworks") or []))
    return _phrases(*texts)


def load_behaviour(init: str, synopses: dict) -> dict:
    """GATED (--use-behaviour only): read this researcher's archive_responses from
    the prod DB and build {neg, pos_phrases}. Mirrors load_memory's db path;
    archive_responses is READ ONLY. Never called unless the flag is set."""
    from _db import load_env, query_json, ledger_schema  # noqa: E402
    load_env()
    sch = ledger_schema()
    resp = query_json(
        f"SELECT canonical_id, choice, choice_detail "
        f"FROM {sch}.archive_responses WHERE researcher_id='{init}'") or []
    return {"neg": _behaviour_negatives(resp),
            "pos_phrases": _behaviour_positive_phrases(resp, synopses)}


# ===========================================================================
# Main per-researcher build
# ===========================================================================

def recommend_one(init: str, mem: dict, papers: dict, filters: dict,
                  synopses: dict, today: datetime,
                  embeddings: Optional[dict], gate_mode: str,
                  gate_cache: dict, gate_emit: list, log: list,
                  behaviour: Optional[dict] = None) -> list[dict]:
    aims = _build_aims(mem)
    if not aims:
        log.append(f"{init}: no aims (survey blank?) — skipping, legacy builder owns this researcher")
        return []
    negatives = mem["negatives"]
    keywords = mem["keywords"]

    # ---- behaviour (MF-7, GATED) — EXTEND negatives + prep boost phrases.
    # Inert unless --use-behaviour built a `behaviour` dict (default OFF): with
    # no behaviour, negatives are unchanged and beh_pos_phrases is empty so the
    # W_BEHAVIOUR rerank term is exactly 0.
    beh_pos_phrases: list[str] = []
    if behaviour:
        beh_neg = behaviour.get("neg") or []
        negatives = negatives + beh_neg            # fed to the EXISTING _veto
        beh_pos_phrases = behaviour.get("pos_phrases") or []
        log.append(f"{init}: behaviour ON — +{len(beh_neg)} reason-anchored "
                   f"negatives, {len(beh_pos_phrases)} boost phrases "
                   f"(EXPERIMENTAL/gated — validate vs validate_drift before promoting)")

    # ---- candidate pool: structured-lexical ∪ optional cosine ----
    in_scope = [c for c, f in filters.items()
                if f.get("is_lab_relevant", True) and c in papers]
    all_anchor = list(dict.fromkeys(sum([a.all_phrases for a in aims], [])))
    cand: set[str] = set()
    for c in in_scope:
        syn = synopses.get(c)
        if syn and syn.get("oos"):
            continue
        pnorm = _paper_text_norm(papers[c], syn)
        score, matched = _overlap(pnorm, all_anchor)
        if matched:                       # ≥1 genuine anchor phrase present
            cand.add(c)
    n_lex = len(cand)

    n_cos = 0
    if embeddings:
        qtext = "\n".join([mem["profile"].get("summary") or ""] +
                          [f"{a.domain} {a.phenomenon} {a.task} {a.mechanism}" for a in aims])
        try:
            from compute_embeddings import _make_backend  # noqa: E402
            backend = _make_backend("local")
            qv = backend.encode([qtext])[0]
            cids = [c for c in in_scope if c in embeddings]
            sims = brq._try_numpy_cosine(qv, [embeddings[c] for c in cids]) \
                or [brq._cosine(qv, embeddings[c]) for c in cids]
            cos_by_cid = dict(zip(cids, sims))
            for c, s in sorted(cos_by_cid.items(), key=lambda kv: -kv[1])[:400]:
                if c not in cand and not (synopses.get(c) or {}).get("oos"):
                    cand.add(c)
                    n_cos += 1
        except Exception as e:
            log.append(f"{init}: cosine recall skipped ({type(e).__name__}: {e})")
            cos_by_cid = {}
    else:
        cos_by_cid = {}

    log.append(f"{init}: candidates = {n_lex} lexical + {n_cos} cosine = {len(cand)} "
               f"(in_scope={len(in_scope)})")

    # ---- per-candidate: best connection over aims → admit/veto/rerank ----
    n_admit = n_borderline_admit = n_gate_emit = n_reject = n_veto = 0
    rows: list[dict] = []
    for c in cand:
        paper = papers[c]
        syn = synopses.get(c)
        pnorm = _paper_text_norm(paper, syn)
        cos = float(cos_by_cid.get(c, 0.0))

        # best aim connection
        best = None
        for a in aims:
            conn = connection(pnorm, a)
            decision = conn["decision"]
            if decision == "none" and cos >= COS_BORDERLINE:
                decision = "borderline"        # cosine-only → gate decides
                conn = dict(conn, decision="borderline")
            if decision == "none":
                continue
            cand_row = {"aim": a, "conn": conn, "decision": decision}
            if best is None or conn["score"] > best["conn"]["score"]:
                best = cand_row
        if best is None:
            n_reject += 1
            continue

        aim = best["aim"]
        conn = best["conn"]
        decision = best["decision"]

        # ---- veto FIRST (before the gate) — a paper that aligns with an
        # excluded phenomenon is dropped here, so it never wastes an Opus
        # same-job judgement and a borderline veto-match can't be gate-admitted.
        v = _veto(pnorm, negatives, conn["score"], conn.get("specB", False))
        if v:
            n_veto += 1
            continue

        # ---- LLM reasoning-gate on borderline ----
        if decision == "borderline":
            gk = _gate_key(init, c, aim.aim_id)
            if gate_mode == "structured":
                # offline stand-in: admit a borderline on SPECIFIC phenomenon
                # evidence, OR a specific mechanism applied to the researcher's
                # OWN specific domain/task (specC ∧ specA — a safe same-job proxy
                # that still excludes off-domain mechanism-name collisions like
                # 'rate-distortion in 5G', which have no specA match). Pure
                # mechanism-name / generic borderlines are the Opus gate's job
                # (emit mode) and are rejected here so the preview stays precise
                # (adversarial review C1/M1).
                if conn.get("specB") or (conn.get("specC") and conn.get("specA_distinct")):
                    decision = "admit"; n_borderline_admit += 1
                else:
                    n_reject += 1
                    continue
            elif gate_mode == "cached":
                v2 = gate_cache.get(gk)
                if v2 and v2.get("relevance_type") in ("A", "B", "C"):
                    decision = "admit"
                    conn = dict(conn, axis=v2["relevance_type"], gated=True)
                    n_borderline_admit += 1
                else:
                    n_reject += 1     # conservative on cache miss
                    continue
            else:  # emit
                gate_emit.append({
                    "researcher_id": init, "canonical_id": c, "aim_id": aim.aim_id,
                    "aim": {"domain": aim.domain, "phenomenon": aim.phenomenon,
                            "task": aim.task, "mechanism": aim.mechanism},
                    "paper_title": paper.get("title"),
                    "synopsis_signals": (syn or {}).get("signals"),
                    "axes": {"A": conn["sA"], "B": conn["sB"], "C": conn["sC"]},
                    "cosine": round(cos, 3),
                })
                n_gate_emit += 1
                continue
        else:
            n_admit += 1

        # ---- definition-aware subtraction ----
        def_mult, def_notes = _def_penalty(pnorm, keywords)

        # ---- rerank composite ----
        mech_sig = conn["sC"]
        kw_sig, kw_matched = _overlap(pnorm, _phrases(*[k["keyword"] for k in keywords]))
        pi_sig = _pi_signal(paper, mem["pis"])
        method_sig = _method_signal(pnorm, mem["methods"])
        dom_sig = _domain_priority(pnorm, aim)
        # MF-7 (GATED): behaviour boost. 0 unless --use-behaviour supplied phrases.
        beh_sig = _overlap(pnorm, beh_pos_phrases)[0] if beh_pos_phrases else 0.0
        chunk = brq._chunk_for(paper, today)
        rec_sig = RECENCY_TIER[chunk]
        composite = (
            W_CONN * conn["score"] + W_MECH * mech_sig + W_KW * kw_sig +
            W_PI * max(0.0, pi_sig) + W_METHOD * method_sig +
            W_DOMAIN * dom_sig + W_RECENCY * rec_sig + W_BEHAVIOUR * beh_sig)
        # phenomenon (axis B) IS the connection criterion — give it a small
        # bonus so a genuine phenomenon match is not buried under the (more
        # numerous) broad-mechanism matches.
        if conn["axis"] == "B":
            composite += W_AXIS_B
        composite *= def_mult
        if pi_sig < 0:
            composite *= 0.7      # negative-PI deprioritise (never drops)
        composite = round(composite, 4)

        prov = {"axis": conn["axis"], "matched": conn["matched"],
                "kw_matched": kw_matched[:3], "def_notes": def_notes,
                "pi": round(pi_sig, 2), "method": round(method_sig, 2),
                "domain_priority": round(dom_sig, 2),
                "decision": "gated" if conn.get("gated") else best["decision"]}
        if behaviour:
            prov["behaviour_boost"] = round(beh_sig, 2)
        rows.append({
            "researcher_id": init, "canonical_id": c, "chunk": chunk,
            "tier": _derive_tier(conn),          # R-TIER: real tier, not hardcoded 'B'
            "connected_aim": aim.aim_id, "connection_axis": conn["axis"],
            "conn_score": conn["score"], "composite": composite,
            "veto_checked": True, "gated": conn.get("gated", False),
            "cos": round(cos, 3),
            "provenance": prov,
        })

    log.append(f"{init}: admit={n_admit} borderline→admit={n_borderline_admit} "
               f"gate_emit={n_gate_emit} veto={n_veto} reject={n_reject} "
               f"→ ranked={len(rows)}")

    # ---- chunk, rank, cap ----
    per_chunk: dict[str, list[dict]] = {"recent": [], "mid": [], "classic": []}
    for r in rows:
        per_chunk[r["chunk"]].append(r)
    out: list[dict] = []
    for chunk, items in per_chunk.items():
        items.sort(key=lambda r: (-r["composite"], -r["conn_score"]))
        n = CHUNK_MIX[chunk]
        kept = items[:n]
        if len(items) > n:
            log.append(f"{init}: {chunk} capped {len(items)}→{n} "
                       f"(dropped {len(items)-n})")
        for rank, r in enumerate(kept, 1):
            r["rank_in_chunk"] = rank
            out.append(r)
    # Explicit hard cap (P33 MF-12) — QUEUE_CAP == sum(CHUNK_MIX). The per-chunk
    # caps already bound the queue to this total; slice unconditionally so the
    # ceiling holds even if CHUNK_MIX is retuned upward. Log only if it truncates.
    if len(out) > QUEUE_CAP:
        log.append(f"{init}: queue {len(out)}→{QUEUE_CAP} (hard cap)")
    out = out[:QUEUE_CAP]
    return out


# ===========================================================================
# Memory loaders
# ===========================================================================

def load_memory(init: str, source: str) -> dict:
    if source == "prefill":
        return ing.ingest_one(init, ing.load_blocks_prefill(init))
    # db
    from _db import load_env, query_json, ledger_schema  # noqa: E402
    load_env()
    sch = ledger_schema()
    prof = query_json(f"SELECT * FROM {sch}.archive_survey_profile WHERE researcher_id='{init}'")
    aims = query_json(f"SELECT * FROM {sch}.archive_survey_aims WHERE researcher_id='{init}' ORDER BY aim_id")
    negs = query_json(f"SELECT * FROM {sch}.archive_survey_negatives WHERE researcher_id='{init}' ORDER BY neg_id")
    kws = query_json(f"SELECT * FROM {sch}.archive_survey_keywords WHERE researcher_id='{init}'")
    pis = query_json(f"SELECT * FROM {sch}.archive_survey_pis WHERE researcher_id='{init}'")
    meth = query_json(f"SELECT * FROM {sch}.archive_survey_methods WHERE researcher_id='{init}'")
    return {"researcher_id": init, "profile": (prof or [{}])[0], "aims": aims,
            "negatives": negs, "keywords": kws, "pis": pis, "methods": meth,
            "models": []}


# ===========================================================================
# DB upsert
# ===========================================================================

def _apply_queue(all_rows: list[dict], tokens: dict[str, str]) -> None:
    from _db import load_env, ledger_schema, _conn  # noqa: E402
    load_env()
    sch = ledger_schema()
    rids = sorted({r["researcher_id"] for r in all_rows})
    upsert = f"""
      INSERT INTO {sch}.archive_researcher_queues
        (researcher_id,canonical_id,chunk,rank_in_chunk,similarity,built_at,
         build_token,tier,composite,dim_match,connected_aim,connection_axis,
         veto_checked,builder)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
      ON CONFLICT (researcher_id,canonical_id) DO UPDATE SET
        chunk=EXCLUDED.chunk, rank_in_chunk=EXCLUDED.rank_in_chunk,
        similarity=EXCLUDED.similarity, built_at=EXCLUDED.built_at,
        build_token=EXCLUDED.build_token, tier=EXCLUDED.tier,
        composite=EXCLUDED.composite, dim_match=EXCLUDED.dim_match,
        connected_aim=EXCLUDED.connected_aim, connection_axis=EXCLUDED.connection_axis,
        veto_checked=EXCLUDED.veto_checked, builder=EXCLUDED.builder
      WHERE {sch}.archive_researcher_queues.builder = EXCLUDED.builder;"""
    # MF-1/MF-B: p28 is a PARKED builder (recommend --apply is operator-gated;
    # P28 not live). Two guards keep it from ever corrupting brq's authoritative
    # queue: (1) the DO UPDATE WHERE builder=EXCLUDED.builder above makes a PK
    # conflict on a brq-owned row a NO-OP (the p28 insert is skipped, the brq row
    # is untouched) rather than flipping it to 'p28'; (2) the prune below deletes
    # only THIS builder's own stale rows (build_digest reads builder='brq').
    prune = (f"DELETE FROM {sch}.archive_researcher_queues "
             f"WHERE researcher_id=%s AND builder='p28' "
             f"AND (build_token IS NULL OR build_token<>%s);")
    built = brq.kst_iso()
    for rid in rids:
        rows = [r for r in all_rows if r["researcher_id"] == rid]
        tok = tokens[rid]
        conn = _conn()
        try:
            conn.autocommit = False
            with conn.cursor() as cur:
                for r in rows:
                    cur.execute(upsert, (
                        rid, r["canonical_id"], r["chunk"], r["rank_in_chunk"],
                        r["cos"], built, tok, r["tier"], r["composite"],
                        json.dumps(r["provenance"], ensure_ascii=False),
                        r["connected_aim"], r["connection_axis"], r["veto_checked"],
                        "p28"))
                cur.execute(prune, (rid, tok))
            conn.commit()
        except Exception:
            conn.rollback(); raise
        finally:
            conn.close()


# ===========================================================================
# CLI
# ===========================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", metavar="INIT", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--profile-source", choices=("prefill", "db"), default="prefill")
    ap.add_argument("--embed", action="store_true",
                    help="Add embedding cosine recall to candidate-gen (loads "
                         "bge-m3; default off = structured-lexical only).")
    ap.add_argument("--gate-mode", choices=("structured", "emit", "cached"),
                    default="structured")
    ap.add_argument("--gate-queue", metavar="PATH",
                    default=str(_REPO_ROOT / "state/archive/_tmp/p28_gate_queue.jsonl"))
    ap.add_argument("--gate-cache", metavar="PATH", action="append", default=[])
    ap.add_argument("--use-behaviour", action="store_true",
                    help="GATED/EXPERIMENTAL (default OFF): read archive_responses "
                         "(prod, READ-ONLY) → feed not_relevant reason-text as extra "
                         "veto negatives + save/read as a rerank boost. EXTENDS the "
                         "existing veto/rerank (no parallel engine). Must pass an "
                         "offline precision eval vs validate_drift before promotion.")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    targets = [args.only.strip().upper()] if args.only else list(ing.RESEARCHERS)
    if args.all:
        targets = list(ing.RESEARCHERS)

    papers = brq._load_papers()
    filters = brq._load_filter_decisions()
    synopses = _load_synopsis_text()
    print(f"[rec] papers={len(papers)} filters={len(filters)} synopses={len(synopses)}")
    embeddings = None
    if args.embed:
        embeddings = brq._load_archive_embeddings("BAAI/bge-m3")
        print(f"[rec] embeddings={len(embeddings)}")
    gate_cache = load_gate_cache([Path(p) for p in args.gate_cache])
    if gate_cache:
        print(f"[rec] gate cache loaded: {len(gate_cache)} verdicts")
    if args.use_behaviour:
        print("[rec] --use-behaviour ON: EXPERIMENTAL/gated behaviour veto+boost "
              "active (reads archive_responses, READ-ONLY). Validate precision vs "
              "validate_drift before promoting — it changes rankings.",
              file=sys.stderr)

    today = datetime.now(timezone(timedelta(hours=9)))
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []
    gate_emit: list[dict] = []
    tokens: dict[str, str] = {}
    log: list[str] = []

    for init in targets:
        mem = load_memory(init, args.profile_source)
        behaviour = load_behaviour(init, synopses) if args.use_behaviour else None
        rows = recommend_one(init, mem, papers, filters, synopses, today,
                             embeddings, args.gate_mode, gate_cache, gate_emit, log,
                             behaviour)
        tokens[init] = str(uuid.uuid4())
        for r in rows:
            r["build_token"] = tokens[init]
        outp = _OUT_DIR / f"{init}.jsonl"
        with outp.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        all_rows.extend(rows)
        if args.json and rows:
            print(json.dumps(rows[:5], ensure_ascii=False, indent=2))

    print("\n".join("  " + l for l in log))
    print(f"\n[rec] total ranked rows: {len(all_rows)} across {len(targets)} researcher(s)")

    if args.gate_mode == "emit" and gate_emit:
        gp = Path(args.gate_queue)
        gp.parent.mkdir(parents=True, exist_ok=True)
        with gp.open("w", encoding="utf-8") as f:
            for g in gate_emit:
                f.write(json.dumps(g, ensure_ascii=False) + "\n")
        print(f"[rec] gate-emit: {len(gate_emit)} borderline pairs → {gp}\n"
              f"      (orchestrator runs Opus same-job judgement, writes verdicts "
              f"back, then re-run --gate-mode cached --gate-cache <verdicts>.)")

    if args.apply and all_rows:
        if args.profile_source == "prefill":
            print("\n[rec] REFUSING --apply with --profile-source prefill "
                  "(pre-fill is not researcher truth). Use --profile-source db "
                  "after surveys are ingested.", file=sys.stderr)
            return 2
        _apply_queue(all_rows, tokens)
        print(f"[rec] UPSERT archive_researcher_queues: {len(all_rows)} rows.")
    else:
        print("[rec] dry-run only (wrote JSONL to state/archive/queues_p28/). "
              "Re-run with --apply (operator, --profile-source db) to write DB.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
