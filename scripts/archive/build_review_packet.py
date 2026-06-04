#!/usr/bin/env python3
"""Build a stratified, risk-weighted adversarial-review packet from the P26b
round-1 tables. Risk weighting: C (mechanism) accepts + cross-species/clinical
B accepts + a sample of rejects — the judgments most prone to contract error.
Writes to state/archive/discovery_run/review/round1_packet.md (no context bloat)."""
from __future__ import annotations
import glob
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TBL = ROOT / "state/archive/discovery_run/tables"
PROF = ROOT / "state/archive/profiles"
OUT = ROOT / "state/archive/discovery_run/review/round1_packet.md"
OUT.parent.mkdir(parents=True, exist_ok=True)

XSPECIES = re.compile(r'\b(macaque|monkey|rodent|\brat\b|mouse|mice|primate|clinical|schizophren|autism|patient|ferret|zebrafish|drosophila|RNN|artificial|deep network|neural network)\b', re.I)


def rows(path):
    out = []
    for ln in open(path, encoding="utf-8"):
        if not ln.startswith("| ") or ln.startswith("| A/B/C"):
            continue
        c = [x.strip() for x in ln.strip().strip("|").split("|")]
        if len(c) >= 4 and c[0] in ("A", "B", "C", "none"):
            out.append({"type": c[0], "title": c[1], "reason": c[2], "doi": c[3]})
    return out


def sample(rs):
    """Risk-weighted: 3 C, 1 cross-species B (else any B), 1 none."""
    C = [r for r in rs if r["type"] == "C"]
    B = [r for r in rs if r["type"] == "B"]
    A = [r for r in rs if r["type"] == "A"]
    N = [r for r in rs if r["type"] == "none"]
    Bx = [r for r in B if XSPECIES.search(r["title"] + " " + r["reason"])] or B
    pick = []
    pick += C[:3]
    pick += Bx[:1]
    pick += A[:1]
    pick += N[:2]
    # dedupe preserving order
    seen = set(); uniq = []
    for r in pick:
        k = r["doi"]
        if k not in seen:
            seen.add(k); uniq.append(r)
    return uniq


lines = []
lines.append("# P26b Round-1 — Adversarial Review Packet\n")
lines.append("You are an adversarial reviewer auditing a per-researcher paper-relevance gate.\n")
lines.append("## Relevance contract (what the gate is supposed to do)\n")
lines.append("A paper is RELEVANT to a researcher iff AT LEAST ONE of:\n"
             "- **A = aim**: the paper's research question/goal connects to one of the researcher's aims.\n"
             "- **B = phenomenon**: the paper's discovered phenomenon/effect is shared with / generalizes / contradicts / bears on a phenomenon the researcher studies — EVEN in a different species (macaque/rodent/etc.), modality, clinical population, or AI/ML system.\n"
             "- **C = shared mechanism / computational theory**: shared computational principle/model/theory (efficient coding, Bayesian inference, attractor dynamics, drift-diffusion, RNN dynamics, rate-distortion, normalization, …).\n\n"
             "REJECT (`none`) ONLY for: method/tool/metadata-overlap-ALONE (same fMRI/EEG/RSA/species/subject/stimulus with no A/B/C), OR genuinely off-field topic (nutrition/oncology/materials/UAV/agriculture/etc.).\n"
             "NEVER reject for species, clinical population, AI/ML, subject-type, or method per se. Method-tag-only = reject.\n\n")
lines.append("## Your job\n")
lines.append("For the sampled decisions below, find SYSTEMATIC gate errors. For each problem flag:\n"
             "1. **Contract violation** — accepted (A/B/C) on method/tag/metadata-alone, or off-field accepted; OR rejected (`none`) for species/method/population (forbidden).\n"
             "2. **Coherence** — does the `reason` actually describe THIS `title`? (mis-pairing).\n"
             "3. **Over-broad C** — `C` granted on a generic shared word (e.g. 'attention', 'decision', 'learning', 'attractor') without a genuine shared computational principle.\n"
             "4. **False-negative reject** — a `none` that genuinely connects via A/B/C.\n\n"
             "Return: a findings list — each with {researcher, title (truncated ok), type-as-judged, problem-class, severity HIGH/MED/LOW, one-line why, recommended fix}. "
             "Then 3–5 SYSTEMATIC patterns + concrete SOP-rule changes (the SOP is state/archive/discovery_run/scout_prompt.md). "
             "Be skeptical and specific. If the gate looks correct on a given item, say nothing about it.\n\n")

for path in sorted(glob.glob(str(TBL / "*.md"))):
    init = os.path.basename(path)[:-3]
    pf = PROF / f"{init}.json"
    aims = phen = []
    if pf.exists():
        p = json.loads(pf.read_text(encoding="utf-8")).get("profile", {})
        aims = p.get("aims", [])[:3]
        phen = p.get("phenomena", [])[:4]
    samp = sample(rows(path))
    lines.append(f"\n---\n\n## {init} — profile (abbrev)\n")
    lines.append("**Aims:** " + " | ".join(a[:110] for a in aims) + "\n\n")
    lines.append("**Phenomena:** " + " | ".join(x[:80] for x in phen) + "\n\n")
    lines.append(f"### {init} — sampled decisions ({len(samp)})\n")
    lines.append("| judged | title | reason | DOI |\n|---|---|---|---|\n")
    for r in samp:
        lines.append(f"| {r['type']} | {r['title'][:120]} | {r['reason'][:220]} | {r['doi']} |\n")

OUT.write_text("".join(lines), encoding="utf-8")
nrows = sum(1 for l in lines if l.startswith("| ") and not l.startswith("| judged") and not l.startswith("|---"))
print(f"packet written: {OUT}  (~{nrows} sampled decisions, {OUT.stat().st_size} bytes)")
