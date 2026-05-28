#!/usr/bin/env python3
"""
scripts/archive/prefilter_oos.py — title+abstract keyword classifier that
identifies obviously out-of-scope papers and emits template synopsis JSON
without spawning an LLM agent. Per the @40 milestone codex review verdict
"SWITCH-TO-HYBRID-CLASSIFIER": 75% of phase-2 papers are OOS and the agent
fanout was burning ~60% of tokens on cases the keyword classifier can
catch.

Decision rule (per paper):
  oos_score = +(in-scope hits) - (OOS hits) - (large negative if hard rule)
  score >= 1   → agent_fanout  (in-scope or marginal)
  score <= -2  → write OOS template synopsis here (confident)
  -1..0        → agent_fanout  (uncertain — defer to LLM)

The classifier is intentionally CONSERVATIVE on the OOS side:
- A false-positive (in-scope paper mistakenly templated as OOS) is the
  cost we are trying to avoid; we accept losing a few in-scope papers to
  the operator's eventual hand-review rather than wrongly OOS-tagging a
  real recommendation.
- A false-negative (OOS paper goes to agent fanout anyway) is cheap —
  the agent will mark it OOS and we have spent one agent call.

So the OOS threshold (-2) is intentionally strict. Tune only after a
codex re-review at a later milestone.

Usage:
    python3 scripts/archive/prefilter_oos.py                 # dry-run report
    python3 scripts/archive/prefilter_oos.py --apply         # write templates + update queue
    python3 scripts/archive/prefilter_oos.py --apply --limit 10
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
QUEUE_FILE = _REPO_ROOT / "state" / "archive" / "synopsis_phase2_queue.jsonl"
SYNOPSES_DIR = _REPO_ROOT / "state" / "archive" / "synopses"

KST = dt.timezone(dt.timedelta(hours=9))

# Keyword bag-of-words, lowercased. The classifier checks substring presence
# (no word-boundary) so we can match Korean / multi-word hits cheaply.
# The lists are deliberately overlapping with the synopsis_prompt.md scope
# rules — they encode the same boundary the LLM is supposed to follow.

IN_SCOPE_TOKENS = (
    # neural systems
    "neuron", "neural", "cortex", "cortical", "synapse", "synaptic",
    "hippocampus", "amygdala", "striatum", "thalamus", "cerebell",
    "v1 ", "primary visual", "extrastriate", "mt area", "place cell",
    "grid cell", "head-direction", "ring attractor",
    # perception / decision
    "perception", "perceptual", "psychophysic", "decision-making",
    "decision making", "drift diffusion", "ddm", "evidence accumulation",
    "bayesian observer", "bayesian inference",
    "efficient coding", "predictive coding", "free energy",
    "active inference", "rate-distortion", "rate distortion",
    "divisive normalization", "divisive inhibition",
    "signal-detection", "signal detection theory",
    # eye movements / vision
    "saccade", "saccadic", "fixation", "fixational", "oculomotor",
    "eye movement", "vision science", "visual percept", "visual cortex",
    "orientation tuning", "receptive field", "scene statistic",
    "crowding", "filling-in", "filling in",
    # cognition
    "working memory", "episodic memory", "metacognition", "metacognitive",
    "confidence", "attention", "feature binding",
    # methods (in-scope when paired with neural signal)
    "tdcs", "tacs", "trns", "tms", "fmri", "eeg", "meg", "ecog",
    "neuropixels", "calcium imaging", "two-photon", "two photon",
    "fmri ", "single-unit", "spike train", "spike trains",
    "spiking", "population code", "population activity",
    # frameworks
    "ring model", "attractor dynamic", "attractor model",
    "hierarchical gaussian filter", "bayesian causal inference",
    "kalman filter neural", "binding by synchrony",
    "binding-by-synchrony", "gamma synchron", "theta phase",
)

OOS_TOKENS = (
    # physics / engineering
    "pid controller", "nonlinear pid", "telecontrol", "traffic control",
    "concrete slab", "shear failure", "thermal load", "earthquake load",
    "fatigue ", "nb3sn", "quench protection", "lhc ", "tev ",
    "einstein", "mass-energy", "cosmology", "universe lifecycle",
    "sine wave fundamental", "plasma wave", "k-vector", "fourier physics",
    "magnetic resonance imaging engineering",
    "particle accelerator", "superconducting magnet",
    "arriving wave model", "direction finding", "natural wave",
    "engineering organization", "functional architecture",
    "transversal collaboration", "system shall do",
    "automation", "geo-information", "geo-space",
    "transmission of a tele",
    "perspective of physics", "perspective from physics",
    "energy gap forms", "open nonlinear system",
    "discussion of what life is", "energy storage",
    # batteries / materials
    "lithium-ion", "lithium ion", "li-ion ", "solid electrolyte",
    "antiperovskite", "chlorite anion", "chlorine dioxide", "redox couple",
    "battery charging", "battery cycling", "ceramic electrode",
    "fuel cell", "paddle-wheel", "paddle wheel mechanism", "pb-fp-bef",
    # optics / signal processing
    "laser noise", "mach-zehnder", "fiber spool", "phase-locked loop",
    "phase locked loop laser",
    # humanities / philosophy
    "philology", "lexicology", "stylistics", "phenomenology of",
    "husserl", "schutz", "consumerism", "art project",
    "human rights", "yk julistus", "yhdistyneiden",
    "cultural object", "anthropological",
    # pedagogy
    "teaching english", "esl learner", "esl ", "foreign language teaching",
    "pedagogical technolog", "didactic ",
    "communicative competence", "speech etiquette",
    "tutorial paper", "demystifying", "linear regression model. source code",
    "english classes", "language-learning",
    # pure ML / CV / NLP
    "voice conversion", "speech disentanglement", "speech split",
    "face recognition", "eigenface", "2d-pca", "pcanet",
    "gan latent", "generative adversarial network latent",
    "autoencoder bottleneck", "biometric verification",
    "biometrics security", "passport verification",
    "mongolian corpus", "chinese open question", "llm fine-tuning chinese",
    "qlora", "quantized low-rank",
    "adversarial robustness", "adversarial robust", "catastrophic forgetting",
    "continual learning benchmark", "multi-task dataset",
    "image classification benchmark", "deep subspace learning",
    "low-quality face", "biometric data",
    "corpus dataset", "manually corrected", "question and answer",
    "knowledge distillation",
    # clinical surgery / cardiology
    "thrombectomy", "endoscopic intubation", "macintosh blade",
    "subdural hematoma", "subclavicular fistula", "fontan completion",
    "glenn shunt", "carotid web", "intraplaque hemorrhage",
    "phacoemulsification", "vitrectomy", "macular pucker",
    "macular hole", "intubation device", "anesthesiology device",
    "miscarriage", "recurrent abortion", "androgen receptor mutation",
    "covid-19", "covid 19", " coronavirus ", "pandemic distribution",
    "myocardial infarction", "coronary artery disease",
    "knee osteoarthritis", "platelet-rich plasma", "bone marrow aspirate",
    "intubation", "endotracheal", "laryngoscope", "stroke patient",
    "anterior cerebral artery", "frontal lesion", "depressive symptoms",
    "apathetic symptoms", "psychiatric illness", "schizophrenia patient",
    "carotid plaque", "carotid stenosis", "carotid artery",
    "post-stroke", "stroke recovery",
    "patient was", "case report",
    # epidemiology / public health
    "air pollution exposure",
    # ecology / agriculture / GIS
    "wildlife", "sagebrush", "crayfish", "primate hand evolution",
    "thumb length brain", "agricultural territory", "geo-information",
    "biodiversity", "ecosystem service",
    # economics / social science
    "free riders", "free-rider", "public-goods game", "public goods game",
    "collective action problem", "voting accuracy",
    "global warming", "climate emergency",
    "heatwave wildfire",
    # neuroimaging tools / pipelines (per @10 finding)
    "fmriprep", "deepprep", "preprocessing pipeline",
    "scanner benchmark", "neuroimaging preprocessing",
    "freesurfer pipeline",
    # molecular biology not connected to systems
    "dna methylation", "drug addiction methylation",
    "fatty liver", "nafld", "er-phagy", "reticulon",
    "lgg-1/gabarap", "autophagy receptor",
    # politics / society
    "border control", "uniform passport",
)

# Hard rules (each triggers a heavy negative score, overriding anything else)
HARD_OOS_PATTERNS = (
    (re.compile(r"^retracted\b", re.IGNORECASE), "retracted paper"),
    (re.compile(r"\bcopyright page\b", re.IGNORECASE), "copyright/imprint page"),
    (re.compile(r"\bfull issue download\b", re.IGNORECASE), "journal-issue-banner"),
    (re.compile(r"\bopinion article front\. psychol\b", re.IGNORECASE), "journal banner only"),
)


def _score(title: str, abstract: str) -> tuple[int, list[str], str | None]:
    """Return (score, matched_tags, hard_reason_or_None)."""
    text = (title or "").lower() + "\n" + (abstract or "").lower()
    matched: list[str] = []

    for pat, reason in HARD_OOS_PATTERNS:
        if pat.search(title or "") or pat.search(abstract or ""):
            return (-99, [reason], reason)

    in_hits = 0
    out_hits = 0
    for tok in IN_SCOPE_TOKENS:
        if tok in text:
            in_hits += 1
            matched.append(f"+{tok}")
    for tok in OOS_TOKENS:
        if tok in text:
            out_hits += 1
            matched.append(f"-{tok}")

    score = in_hits - out_hits
    return (score, matched, None)


def _classify(title: str, abstract: str) -> dict:
    score, matched, hard = _score(title, abstract)
    # Decision logic (more aggressive than -2 threshold):
    # - Hard rule → template
    # - score <= -2 → template
    # - score == -1 AND no in-scope hit → template (single negative signal, no positive)
    # - otherwise → agent_fanout
    if hard:
        verdict = "oos_template"
    elif score <= -2:
        verdict = "oos_template"
    elif score == -1:
        # check if any in-scope hit; we keep matched list so we can count
        in_hits_count = sum(1 for m in matched if m.startswith("+"))
        if in_hits_count == 0:
            verdict = "oos_template"
        else:
            verdict = "agent_fanout"
    else:
        verdict = "agent_fanout"
    return {"score": score, "matched": matched, "hard_reason": hard, "verdict": verdict}


def _kst_iso() -> str:
    return dt.datetime.now(KST).isoformat(timespec="seconds")


def _template_synopsis(cid: str, oos_reason: str) -> dict:
    """Build the FLAT JSON for an OOS-template synopsis. Python-generated;
    `generator` reflects that so the operator can audit later."""
    return {
        "canonical_id": cid,
        "synopsis_version": "v1.2026-05-28",
        "generator": "python-prefilter@2026-05-28",
        "generated_at": _kst_iso(),
        "review_status": "auto_unreviewed",
        "abstract_coverage": 0.0,
        "frameworks": [],
        "core_question": None,
        "key_assumptions": [],
        "manipulations": [],
        "key_findings": [],
        "interpretations": [],
        "limitations_noted": [],
        "connecting_signals": [],
        "out_of_scope_note": oos_reason,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Hybrid OOS prefilter for synopsis batch.")
    ap.add_argument("--apply", action="store_true",
                    help="Write OOS template files + rewrite queue to keep "
                         "only the agent_fanout subset.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Process only first N papers (smoke test).")
    args = ap.parse_args()

    if not QUEUE_FILE.exists():
        print(f"FATAL: queue not found at {QUEUE_FILE}", file=sys.stderr)
        return 2

    papers = [json.loads(l) for l in open(QUEUE_FILE, encoding="utf-8") if l.strip()]
    if args.limit:
        papers = papers[:args.limit]
    print(f"[prefilter] queue size: {len(papers)}")

    oos_papers: list[tuple[dict, str]] = []  # (paper, oos_reason)
    keep_papers: list[dict] = []
    score_dist = {"hard": 0, "<=-2": 0, "-1..0": 0, "1..2": 0, ">=3": 0}

    for p in papers:
        title = p.get("title") or ""
        abstract = p.get("abstract") or ""
        result = _classify(title, abstract)
        if result["hard_reason"]:
            score_dist["hard"] += 1
        elif result["score"] <= -2:
            score_dist["<=-2"] += 1
        elif result["score"] <= 0:
            score_dist["-1..0"] += 1
        elif result["score"] <= 2:
            score_dist["1..2"] += 1
        else:
            score_dist[">=3"] += 1

        if result["verdict"] == "oos_template":
            reason = (result["hard_reason"]
                      or f"keyword-classifier (score={result['score']}, "
                         f"hits={', '.join(t for t in result['matched'][:6])})")
            oos_papers.append((p, reason))
        else:
            keep_papers.append(p)

    print(f"[prefilter] score distribution: {score_dist}")
    print(f"[prefilter] oos_template: {len(oos_papers)} ({100*len(oos_papers)/len(papers):.0f}%)")
    print(f"[prefilter] agent_fanout: {len(keep_papers)} ({100*len(keep_papers)/len(papers):.0f}%)")

    # Show a sample of borderline cases.
    print("\n[prefilter] sample OOS-template (first 10):")
    for paper, reason in oos_papers[:10]:
        print(f"  {paper['canonical_id']}  reason={reason[:60]}")
        print(f"    title: {(paper.get('title') or '')[:90]}")

    print("\n[prefilter] sample agent_fanout (first 10):")
    for p in keep_papers[:10]:
        title = (p.get("title") or "")[:90]
        print(f"  {p['canonical_id']}  {title}")

    if not args.apply:
        print("\n[prefilter] DRY-RUN — pass --apply to write template files + rewrite queue.")
        return 0

    # Apply: write template synopsis JSON for each OOS paper, then rewrite
    # the queue file to keep only the agent-fanout subset.
    SYNOPSES_DIR.mkdir(parents=True, exist_ok=True)
    n_written = 0
    n_skipped_existing = 0
    for paper, reason in oos_papers:
        path = SYNOPSES_DIR / f"{paper['canonical_id']}.json"
        if path.exists():
            # Don't overwrite an existing (possibly hand-curated) synopsis.
            n_skipped_existing += 1
            continue
        syn = _template_synopsis(paper["canonical_id"], reason)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(syn, f, ensure_ascii=False, indent=2)
        n_written += 1
    print(f"\n[prefilter] OOS-template files written: {n_written}")
    print(f"[prefilter] OOS-template files skipped (already exist): {n_skipped_existing}")

    # Rewrite queue.
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        for p in keep_papers:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"[prefilter] queue rewritten to {len(keep_papers)} agent_fanout papers")

    return 0


if __name__ == "__main__":
    sys.exit(main())
